import sqlite3
import hashlib
import time
import random
import re
import os
import shutil
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

MODULE_DIR = os.path.abspath(os.path.dirname(__file__))
APP_DIR = os.path.abspath(os.path.join(MODULE_DIR, ".."))
WORKSPACE_DIR = os.path.abspath(os.path.join(APP_DIR, ".."))

if load_dotenv is not None:
    for env_candidate in (
        os.path.join(APP_DIR, ".env"),
        os.path.join(WORKSPACE_DIR, ".env"),
    ):
        if os.path.exists(env_candidate):
            load_dotenv(env_candidate, override=False)

DEFAULT_MONGO_URI = str(os.getenv("MONGO_URI", "mongodb://localhost:27017") or "mongodb://localhost:27017").strip() or "mongodb://localhost:27017"
DEFAULT_MONGO_DB_NAME = str(os.getenv("MONGO_DB_NAME", "barberboss") or "barberboss").strip() or "barberboss"
LOCAL_BACKUP_DIR = os.path.join(APP_DIR, "local_backups")
LOCAL_BACKUP_PATH = str(
    os.getenv("TEAM_AI_LOCAL_BACKUP_PATH", os.path.join(LOCAL_BACKUP_DIR, "team_ai.latest.db"))
    or os.path.join(LOCAL_BACKUP_DIR, "team_ai.latest.db")
).strip()
try:
    AUTO_BACKUP_INTERVAL_SECONDS = max(2.0, float(str(os.getenv("TEAM_AI_AUTO_BACKUP_INTERVAL", "5")).strip() or "5"))
except Exception:
    AUTO_BACKUP_INTERVAL_SECONDS = 5.0

_AUTO_BACKUP_IN_PROGRESS = False
_LAST_AUTO_BACKUP_TS = 0.0

def _score_existing_db(path):
    if not path or not os.path.exists(path):
        return (-1, -1, -1)

    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0

    core_tables = 0
    extra_tables = 0
    try:
        probe = sqlite3.connect(path)
        tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        probe.close()
        core_tables = sum(1 for name in ("organizations", "branches", "users") if name in tables)
        extra_tables = sum(1 for name in ("attendance", "ratings", "leaves", "payments") if name in tables)
    except Exception:
        pass

    return (core_tables, extra_tables, size)


def _resolve_db_path():
    env_path = str(os.getenv("TEAM_AI_DB_PATH", "") or "").strip()
    if env_path:
        return os.path.abspath(env_path)

    preferred_path = os.path.abspath(os.path.join(APP_DIR, "team_ai.db"))
    legacy_path = os.path.abspath(os.path.join(WORKSPACE_DIR, "team_ai.db"))

    preferred_score = _score_existing_db(preferred_path)
    legacy_score = _score_existing_db(legacy_path)

    if legacy_score > preferred_score and os.path.exists(legacy_path):
        try:
            os.makedirs(os.path.dirname(preferred_path), exist_ok=True)
            shutil.copy2(legacy_path, preferred_path)
            preferred_score = _score_existing_db(preferred_path)
        except Exception:
            pass

    return preferred_path


DB_PATH = _resolve_db_path()


class SyncedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        return super().execute(_normalize_sqlite_now(sql), parameters)

    def executemany(self, sql, seq_of_parameters):
        return super().executemany(_normalize_sqlite_now(sql), seq_of_parameters)


class SyncedConnection(sqlite3.Connection):
    def cursor(self, factory=SyncedCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=(), /):
        return super().execute(_normalize_sqlite_now(sql), parameters)

    def executemany(self, sql, seq_of_parameters, /):
        return super().executemany(_normalize_sqlite_now(sql), seq_of_parameters)

    def commit(self):
        result = super().commit()
        try:
            _maybe_auto_backup_after_commit()
        except Exception:
            pass
        return result


def get_local_now():
    return datetime.now().replace(microsecond=0)


def get_local_now_text():
    return get_local_now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_sqlite_now(query):
    if not isinstance(query, str):
        return query

    normalized = query
    normalized = re.sub(
        r"datetime\(\s*'now'\s*\)",
        "datetime('now','localtime')",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r'datetime\(\s*"now"\s*\)',
        'datetime("now","localtime")',
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"datetime\(\s*'now'\s*,(?!\s*'localtime')",
        "datetime('now','localtime',",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r'datetime\(\s*"now"\s*,(?!\s*"localtime")',
        'datetime("now","localtime",',
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"date\(\s*'now'\s*\)",
        "date('now','localtime')",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r'date\(\s*"now"\s*\)',
        'date("now","localtime")',
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"date\(\s*'now'\s*,(?!\s*'localtime')",
        "date('now','localtime',",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r'date\(\s*"now"\s*,(?!\s*"localtime")',
        'date("now","localtime",',
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30, factory=SyncedConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -20000")
    return conn


def execute_write(conn, query, params=(), commit=False, retries=4, base_delay=0.04):
    """Execute a single write query with lightweight retry on SQLite lock contention."""
    query = _normalize_sqlite_now(query)
    last_error = None
    for attempt in range(retries + 1):
        try:
            cur = conn.execute(query, params)
            if commit:
                conn.commit()
            return cur
        except sqlite3.OperationalError as err:
            last_error = err
            msg = str(err).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            if attempt >= retries:
                raise
            sleep_s = (base_delay * (2 ** attempt)) + random.uniform(0, base_delay)
            time.sleep(sleep_s)
    if last_error:
        raise last_error


def execute_many_write(conn, query, seq_of_params, commit=False, retries=4, base_delay=0.04):
    """Execute many write queries with retry for lock contention."""
    query = _normalize_sqlite_now(query)
    last_error = None
    for attempt in range(retries + 1):
        try:
            cur = conn.executemany(query, seq_of_params)
            if commit:
                conn.commit()
            return cur
        except sqlite3.OperationalError as err:
            last_error = err
            msg = str(err).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            if attempt >= retries:
                raise
            sleep_s = (base_delay * (2 ** attempt)) + random.uniform(0, base_delay)
            time.sleep(sleep_s)
    if last_error:
        raise last_error


def is_recent_duplicate_message(conn, sender, receiver, organization, branch, message, within_seconds=120):
    sender_v = str(sender or "").strip()
    receiver_v = str(receiver or "").strip()
    organization_v = str(organization or "").strip()
    branch_v = str(branch or "").strip()
    message_v = str(message or "").strip()

    if not sender_v or not receiver_v or not organization_v or not message_v:
        return False

    row = conn.execute(
        """
        SELECT created_at
        FROM messages
        WHERE sender=?
          AND receiver=?
          AND organization=?
          AND COALESCE(branch, '')=?
          AND TRIM(message)=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (sender_v, receiver_v, organization_v, branch_v, message_v),
    ).fetchone()

    if not row or not row[0]:
        return False

    created_raw = str(row[0]).strip()
    parsed = None
    for parser in (
        lambda value: datetime.fromisoformat(value),
        lambda value: datetime.strptime(value, "%Y-%m-%d %H:%M:%S"),
        lambda value: datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f"),
    ):
        try:
            parsed = parser(created_raw)
            break
        except Exception:
            continue

    if parsed is None:
        return False

    now = get_local_now()
    if parsed > now:
        return False

    return (now - parsed) <= timedelta(seconds=int(within_seconds))


# =========================
# PASSWORDS
# =========================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, hashed):
    return hash_password(password) == hashed


def normalize_phone_number(phone):
    raw = str(phone or "").strip()
    digits = re.sub(r"\D+", "", raw)

    if not digits:
        return "", "Phone number is required."

    if digits.startswith("254"):
        normalized = digits
    elif digits.startswith("0") and len(digits) == 10:
        normalized = "254" + digits[1:]
    elif len(digits) == 9 and digits[:1] in {"7", "1"}:
        normalized = "254" + digits
    else:
        return "", "Use the full standard phone format like 2547XXXXXXXX."

    if not re.fullmatch(r"254(7\d{8}|1\d{8})", normalized):
        return "", "Use a valid full standard phone number like 2547XXXXXXXX or 2541XXXXXXXX."

    return normalized, ""


def get_phone_uniqueness_error(conn, phone, exclude_username=None, exclude_organization=None):
    normalized, error = normalize_phone_number(phone)
    if error:
        return normalized, error

    try:
        user_query = "SELECT username FROM users WHERE TRIM(COALESCE(phone, ''))=?"
        user_params = [normalized]
        if exclude_username:
            user_query += " AND lower(trim(username))<>lower(trim(?))"
            user_params.append(str(exclude_username).strip())
        user_row = conn.execute(user_query + " LIMIT 1", tuple(user_params)).fetchone()
        if user_row and str(user_row[0]).strip():
            return normalized, f"Phone number {normalized} is already used by user '{user_row[0]}'."
    except Exception:
        pass

    try:
        org_query = "SELECT name FROM organizations WHERE TRIM(COALESCE(phone, ''))=?"
        org_params = [normalized]
        if exclude_organization:
            org_query += " AND lower(trim(name))<>lower(trim(?))"
            org_params.append(str(exclude_organization).strip())
        org_row = conn.execute(org_query + " LIMIT 1", tuple(org_params)).fetchone()
        if org_row and str(org_row[0]).strip():
            return normalized, f"Phone number {normalized} is already used by organization '{org_row[0]}'."
    except Exception:
        pass

    return normalized, ""


# =========================
# LOG SYSTEM (NO IMPORT LOOP 🔥)
# =========================
def log_action(conn, username, action, role, organization):
    try:
        conn.execute("""
        INSERT INTO audit_logs(username, action, role, organization, created_at)
        VALUES (?,?,?,?,?)
        """, (username, action, role, organization, get_local_now_text()))
        conn.commit()
    except:
        pass


# =========================
# CREATE TABLES (MASTER 🔥)
# =========================
def create_tables():

    conn = get_connection()
    c = conn.cursor()

    # =========================
    # ORGANIZATIONS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS organizations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        status TEXT DEFAULT 'active',
        phone TEXT,
        email TEXT,
        location TEXT,
        created_at TEXT,
        expires_at TEXT,
        business_type TEXT DEFAULT 'Office'
    )
    """)

    # Safe migration: add business_type to existing databases
    try:
        c.execute("ALTER TABLE organizations ADD COLUMN business_type TEXT DEFAULT 'Office'")
        conn.commit()
    except Exception:
        pass

    # =========================
    # USERS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT,
        branch TEXT,
        organization TEXT,
        status TEXT DEFAULT 'active',
        pin TEXT,
        phone TEXT DEFAULT '',
        gender TEXT DEFAULT 'unknown',
        created_at TEXT,
        exclude_from_analytics INTEGER DEFAULT 0
    )
    """)

    # Safe migrations for older databases
    try:
        c.execute("ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass

    # =========================
    # BRANCHES
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS branches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        organization TEXT,
        status TEXT DEFAULT 'active'
    )
    """)

    # =========================
    # BRANCH WORKING HOURS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS branch_working_hours(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT,
        day_name TEXT,
        work_start TEXT DEFAULT '09:00',
        work_end TEXT DEFAULT '18:00',
        off_day INTEGER DEFAULT 0,
        updated_at TEXT,
        UNIQUE(organization, branch, day_name)
    )
    """)

    # =========================
    # ORGANIZATION HOLIDAYS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS organization_holidays(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT DEFAULT '',
        holiday_date TEXT,
        holiday_name TEXT DEFAULT '',
        is_closed INTEGER DEFAULT 1,
        work_start TEXT DEFAULT '09:00',
        work_end TEXT DEFAULT '18:00',
        updated_at TEXT,
        UNIQUE(organization, branch, holiday_date)
    )
    """)

    # =========================
    # ORGANIZATION HOLIDAY PROFILE
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS organization_holiday_profiles(
        organization TEXT PRIMARY KEY,
        country_code TEXT DEFAULT 'KE',
        subdivision TEXT DEFAULT '',
        auto_detect_holidays INTEGER DEFAULT 0,
        updated_at TEXT
    )
    """)

    # =========================
    # TOPICS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS topics(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT UNIQUE
    )
    """)

    default_topics = [
        "Customer Service",
        "Punctuality",
        "Cleanliness",
        "Skill Level",
        "Teamwork",
        "Communication",
        "Work Ethics",
        "Honesty",
        "Discipline"
    ]

    for t in default_topics:
        c.execute("INSERT OR IGNORE INTO topics(topic) VALUES(?)", (t,))

    # =========================
    # RATINGS (FIXED 🔥)
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rater TEXT,
        rated TEXT,
        topic TEXT,
        score INTEGER,
        branch TEXT,
        organization TEXT,
        created_at TEXT
    )
    """)

    # =========================
    # ATTENDANCE
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        branch TEXT,
        organization TEXT,
        clock_in TEXT,
        clock_out TEXT,
        status TEXT,
        date TEXT,
        image TEXT
    )
    """)

    # =========================
    # EARLY CLOCK-OUT APPROVALS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS early_clockout_approvals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        organization TEXT,
        branch TEXT,
        approved_for_date TEXT,
        reason TEXT,
        approved_by TEXT,
        status TEXT DEFAULT 'approved',
        actual_reason TEXT DEFAULT '',
        used_at TEXT DEFAULT '',
        created_at TEXT
    )
    """)

    # =========================
    # LATENESS APPROVALS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS lateness_approvals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        organization TEXT,
        branch TEXT,
        approved_for_date TEXT,
        reason TEXT,
        approved_by TEXT,
        status TEXT DEFAULT 'approved',
        actual_reason TEXT DEFAULT '',
        used_at TEXT DEFAULT '',
        created_at TEXT
    )
    """)

    # =========================
    # SCHEDULES (FIXED 🔥)
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS schedules(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        branch TEXT,
        organization TEXT,
        day TEXT,
        work_start TEXT,
        work_end TEXT,
        off_day INTEGER DEFAULT 0
    )
    """)

    # =========================
    # LEAVES
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS leaves(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        organization TEXT,
        branch TEXT,
        start_date TEXT,
        end_date TEXT,
        reason TEXT,      
        status TEXT,
        approved_by TEXT,
        admin_note TEXT,
        reviewed_at TEXT
    )
    """)

    try:
        c.execute("ALTER TABLE leaves ADD COLUMN approved_by TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE leaves ADD COLUMN admin_note TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE leaves ADD COLUMN reviewed_at TEXT")
    except Exception:
        pass

    # =========================
    # WARNINGS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS warnings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        organization TEXT,
        branch TEXT,
        type TEXT,
        message TEXT,
        created_at TEXT
    )
    """)

    # =========================
    # MESSAGES
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT,
        receiver TEXT,
        organization TEXT,
        branch TEXT,
        message TEXT,
        created_at TEXT
    )
    """)

    # =========================
    # POLLS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS polls(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT DEFAULT '',
        question TEXT,
        allow_custom INTEGER DEFAULT 1,
        anonymous INTEGER DEFAULT 1,
        status TEXT DEFAULT 'open',
        created_by TEXT,
        creator_role TEXT DEFAULT '',
        created_at TEXT,
        closed_at TEXT DEFAULT '',
        expires_at TEXT DEFAULT '',
        archived INTEGER DEFAULT 0
    )
    """)
    try:
        c.execute("ALTER TABLE polls ADD COLUMN expires_at TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE polls ADD COLUMN archived INTEGER DEFAULT 0")
    except Exception:
        pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS poll_responses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER,
        organization TEXT,
        branch TEXT DEFAULT '',
        responder TEXT,
        responder_role TEXT DEFAULT '',
        response_choice TEXT DEFAULT '',
        custom_answer TEXT DEFAULT '',
        created_at TEXT,
        UNIQUE(poll_id, responder)
    )
    """)

    # =========================
    # PAYMENTS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        amount REAL,
        method TEXT,
        phone TEXT,
        created_at TEXT
    )
    """)

    # =========================
    # KIOSK DEVICES
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS kiosks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        branch TEXT,
        organization TEXT,
        device_name TEXT,
        last_active TEXT,
        status TEXT DEFAULT 'active'
    )
    """)
    try:
        c.execute("ALTER TABLE kiosks ADD COLUMN status TEXT DEFAULT 'active'")
    except Exception:
        pass

    # =========================
    # CLIENT FEEDBACK SETTINGS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS client_feedback_settings(
        organization TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 0,
        allow_named INTEGER DEFAULT 1,
        updated_at TEXT
    )
    """)

    # =========================
    # CLIENT FEEDBACK
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS client_feedback(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT,
        feedback_scope TEXT,
        target_username TEXT,
        stars INTEGER,
        message TEXT,
        is_anonymous INTEGER DEFAULT 1,
        client_name TEXT DEFAULT '',
        created_at TEXT
    )
    """)

    # =========================
    # SETTINGS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY,
        rating_open INTEGER DEFAULT 1,
        allow_duplicates INTEGER DEFAULT 0,
        anonymous_rating INTEGER DEFAULT 0,
        max_score INTEGER DEFAULT 5,
        work_start TEXT DEFAULT '09:00',
        work_end TEXT DEFAULT '18:00',
        late_minutes INTEGER DEFAULT 15
    )
    """)

    c.execute("""
    INSERT OR IGNORE INTO settings(id) VALUES (1)
    """)

    # =========================
    # AUDIT LOGS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        role TEXT,
        organization TEXT,
        created_at TEXT
    )
    """)

    # =========================
    # ADMIN ACTION REQUESTS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS admin_action_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT,
        target_username TEXT,
        target_role TEXT,
        requested_by TEXT,
        action_type TEXT,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        reviewed_by TEXT,
        review_note TEXT,
        created_at TEXT,
        reviewed_at TEXT
    )
    """)

    # =========================
    # PERSISTENT USER SESSIONS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS user_sessions(
        token TEXT PRIMARY KEY,
        username TEXT,
        role TEXT,
        organization TEXT,
        created_at TEXT,
        expires_at TEXT,
        active INTEGER DEFAULT 1
    )
    """)

    # =========================
    # MASTER USER (SAFE)
    # =========================
    c.execute("""
    INSERT OR IGNORE INTO users(username, password, role, organization, status)
    VALUES (?, ?, ?, ?, ?)
    """, (
        "master",
        hash_password("1234"),
        "master",
        "MASTER",
        "active"
    ))

    # =========================
    # PAYMENT CONFIG
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS payment_config(
        id INTEGER PRIMARY KEY,
        paybill TEXT DEFAULT '',
        till_number TEXT DEFAULT '',
        bank_name TEXT DEFAULT '',
        bank_account TEXT DEFAULT '',
        bank_branch TEXT DEFAULT '',
        price_single_branch INTEGER DEFAULT 1000,
        price_per_branch INTEGER DEFAULT 800
    )
    """)
    c.execute("INSERT OR IGNORE INTO payment_config(id) VALUES(1)")

    # =========================
    # ALERTS & NOTIFICATIONS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT,
        alert_type TEXT,
        severity TEXT,
        subject TEXT,
        message TEXT,
        related_user TEXT,
        assigned_to TEXT,
        status TEXT DEFAULT 'open',
        created_at TEXT,
        resolved_at TEXT
    )
    """)

    # =========================
    # ANALYTICS EXCLUSIONS (ROLE-BASED FILTERING)
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS analytics_filters(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        viewer_role TEXT,
        exclude_roles TEXT,
        created_at TEXT
    )
    """)

    # Set exclusion rules
    exclusion_rules = [
        ('MASTER', 'super_admin, master'),
        ('super_admin', 'super_admin, master'),
        ('admin', 'super_admin, master'),
        ('employee', 'super_admin, master, admin'),
    ]

    for org_or_role, excluded in exclusion_rules:
        c.execute("""
        INSERT OR IGNORE INTO analytics_filters(organization, viewer_role, exclude_roles, created_at)
        VALUES(?, ?, ?, ?)
        """, (org_or_role, org_or_role, excluded, str(datetime.now())))

    # =========================
    # GROUP DEMOGRAPHICS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS group_demographics(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT,
        group_id TEXT,
        group_type TEXT,
        members TEXT,
        total_members INTEGER,
        male_count INTEGER,
        female_count INTEGER,
        other_count INTEGER,
        avg_rating REAL,
        risk_level TEXT,
        detected_at TEXT,
        notes TEXT
    )
    """)

    # =========================
    # ANALYTICS REPORTS
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS analytics_reports(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT,
        report_type TEXT,
        generated_by TEXT,
        report_data TEXT,
        summary TEXT,
        critical_count INTEGER,
        warning_count INTEGER,
        generated_at TEXT
    )
    """)

    # =========================
    # MESSAGING SYSTEM
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS system_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user TEXT,
        to_user TEXT,
        organization TEXT,
        branch TEXT,
        message_type TEXT,
        subject TEXT,
        body TEXT,
        priority TEXT,
        read_at TEXT,
        created_at TEXT
    )
    """)

    # =========================
    # SUPER ADMIN INTELLIGENCE SESSION
    # =========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS super_admin_insights(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization TEXT,
        branch TEXT,
        super_admin TEXT,
        insight_category TEXT,
        insight_data TEXT,
        action_required INTEGER,
        viewed_at TEXT,
        created_at TEXT
    )
    """)

    # =========================
    # PERFORMANCE INDEXES
    # =========================
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_org_branch_role_status ON users(organization, branch, role, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_username_org ON users(username, organization)")
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone) WHERE TRIM(COALESCE(phone, '')) <> ''")
    except Exception:
        pass
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_phone_unique ON organizations(phone) WHERE TRIM(COALESCE(phone, '')) <> ''")
    except Exception:
        pass

    c.execute("CREATE INDEX IF NOT EXISTS idx_ratings_org_branch_created ON ratings(organization, branch, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ratings_rated_org ON ratings(rated, organization)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ratings_rater_org ON ratings(rater, organization)")

    c.execute("CREATE INDEX IF NOT EXISTS idx_attendance_org_branch_date ON attendance(organization, branch, date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_attendance_user_org_date ON attendance(username, organization, date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_attendance_status_org ON attendance(status, organization)")

    c.execute("CREATE INDEX IF NOT EXISTS idx_warnings_user_org_created ON warnings(username, organization, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_receiver_org_created ON messages(receiver, organization, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_system_messages_to_org_created ON system_messages(to_user, organization, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_polls_org_branch_status ON polls(organization, branch, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_poll_responses_poll_org ON poll_responses(poll_id, organization)")

    c.execute("CREATE INDEX IF NOT EXISTS idx_leaves_org_branch_status ON leaves(organization, branch, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_schedules_user_org_day ON schedules(username, organization, day)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_branch_working_hours_org_branch_day ON branch_working_hours(organization, branch, day_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_organization_holidays_org_branch_date ON organization_holidays(organization, branch, holiday_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_organization_holiday_profiles_org ON organization_holiday_profiles(organization)")

    c.execute("CREATE INDEX IF NOT EXISTS idx_early_clockout_org_branch_date_status ON early_clockout_approvals(organization, branch, approved_for_date, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lateness_org_branch_date_status ON lateness_approvals(organization, branch, approved_for_date, status)")

    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_org_created ON payments(organization, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_username_active ON user_sessions(username, active)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_expires ON user_sessions(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_client_feedback_org_branch_created ON client_feedback(organization, branch, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_client_feedback_target_scope ON client_feedback(target_username, feedback_scope)")

    conn.commit()
    conn.close()


def mongo_is_configured():
    if MongoClient is None:
        return False

    mongo_uri = str(os.getenv("MONGO_URI", DEFAULT_MONGO_URI) or DEFAULT_MONGO_URI).strip()
    mongo_db_name = str(os.getenv("MONGO_DB_NAME", DEFAULT_MONGO_DB_NAME) or DEFAULT_MONGO_DB_NAME).strip()
    return bool(mongo_uri and mongo_db_name)


def get_mongo_database():
    if not mongo_is_configured():
        return None

    mongo_uri = str(os.getenv("MONGO_URI", DEFAULT_MONGO_URI) or DEFAULT_MONGO_URI).strip()
    mongo_db_name = str(os.getenv("MONGO_DB_NAME", DEFAULT_MONGO_DB_NAME) or DEFAULT_MONGO_DB_NAME).strip()

    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        return client[mongo_db_name]
    except Exception:
        return None


def get_mongo_db():
    return get_mongo_database()


def backup_sqlite_to_local_snapshot():
    source_conn = None
    dest_conn = None
    try:
        if not os.path.exists(DB_PATH):
            return {
                "ok": False,
                "status": "sqlite_missing",
                "path": LOCAL_BACKUP_PATH,
            }

        if os.path.abspath(DB_PATH) == os.path.abspath(LOCAL_BACKUP_PATH):
            return {
                "ok": True,
                "status": "snapshot_same_as_primary",
                "path": LOCAL_BACKUP_PATH,
            }

        source_conn = sqlite3.connect(DB_PATH)
        if not _sqlite_has_meaningful_data(source_conn):
            return {
                "ok": True,
                "status": "skipped_sqlite_has_no_meaningful_data",
                "path": LOCAL_BACKUP_PATH,
                "preserved_existing": os.path.exists(LOCAL_BACKUP_PATH),
            }

        backup_dir = os.path.dirname(LOCAL_BACKUP_PATH) or APP_DIR
        os.makedirs(backup_dir, exist_ok=True)
        dest_conn = sqlite3.connect(LOCAL_BACKUP_PATH)
        source_conn.backup(dest_conn)
        dest_conn.commit()
        return {
            "ok": True,
            "status": "local_snapshot_saved",
            "path": LOCAL_BACKUP_PATH,
            "size_bytes": os.path.getsize(LOCAL_BACKUP_PATH) if os.path.exists(LOCAL_BACKUP_PATH) else 0,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "local_snapshot_failed",
            "path": LOCAL_BACKUP_PATH,
            "error": str(exc),
        }
    finally:
        try:
            if dest_conn is not None:
                dest_conn.close()
        except Exception:
            pass
        try:
            if source_conn is not None:
                source_conn.close()
        except Exception:
            pass


def _maybe_auto_backup_after_commit():
    global _AUTO_BACKUP_IN_PROGRESS, _LAST_AUTO_BACKUP_TS

    backup_sqlite_to_local_snapshot()

    now_ts = time.time()
    if _AUTO_BACKUP_IN_PROGRESS:
        return
    if (now_ts - _LAST_AUTO_BACKUP_TS) < AUTO_BACKUP_INTERVAL_SECONDS:
        return
    if not mongo_is_configured():
        return

    _AUTO_BACKUP_IN_PROGRESS = True
    _LAST_AUTO_BACKUP_TS = now_ts
    try:
        backup_sqlite_to_mongo()
    finally:
        _AUTO_BACKUP_IN_PROGRESS = False


def _get_sqlite_tables(conn):
    cur = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return [row[0] for row in cur.fetchall()]


def _sqlite_has_meaningful_data(conn):
    checks = [
        ("users", "SELECT COUNT(*) FROM users WHERE username <> 'master'"),
        ("organizations", "SELECT COUNT(*) FROM organizations"),
        ("ratings", "SELECT COUNT(*) FROM ratings"),
        ("attendance", "SELECT COUNT(*) FROM attendance"),
        ("branches", "SELECT COUNT(*) FROM branches"),
    ]

    for table_name, query in checks:
        try:
            count = conn.execute(query).fetchone()[0]
            if int(count) > 0:
                return True
        except Exception:
            # Ignore missing table edge cases on first boot.
            continue
    return False


def backup_sqlite_to_mongo():
    local_snapshot_result = backup_sqlite_to_local_snapshot()
    db = get_mongo_database()
    if db is None:
        return {
            "ok": False,
            "status": "mongo_not_configured",
            "message": "MongoDB is not reachable. Local snapshot was attempted instead.",
            "local_snapshot": local_snapshot_result,
        }

    try:
        conn = get_connection()
        if not _sqlite_has_meaningful_data(conn):
            conn.close()
            return {
                "ok": True,
                "status": "skipped_sqlite_has_no_meaningful_data",
                "local_snapshot": local_snapshot_result,
            }

        tables = _get_sqlite_tables(conn)
        payload = {}
        total_rows = 0

        for table in tables:
            cur = conn.execute(f"SELECT * FROM {table}")
            columns = [d[0] for d in cur.description or []]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            payload[table] = rows
            total_rows += len(rows)

        backup_doc = {
            "backup_key": "latest",
            "db_path": DB_PATH,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "tables": payload,
            "table_count": len(tables),
            "total_rows": total_rows,
        }

        collection_name = os.getenv("MONGO_BACKUP_COLLECTION", "sqlite_backups").strip() or "sqlite_backups"
        db[collection_name].replace_one({"backup_key": "latest"}, backup_doc, upsert=True)

        conn.close()
        return {
            "ok": True,
            "status": "backup_saved",
            "collection": collection_name,
            "table_count": len(tables),
            "total_rows": total_rows,
            "local_snapshot": local_snapshot_result,
        }
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        return {
            "ok": False,
            "status": "backup_failed",
            "error": str(exc),
        }


def restore_sqlite_from_mongo_if_empty():
    conn = None
    try:
        create_tables()
        conn = get_connection()

        if _sqlite_has_meaningful_data(conn):
            conn.close()
            return {
                "ok": True,
                "status": "skipped_sqlite_has_data",
            }
        conn.close()

        if os.path.exists(LOCAL_BACKUP_PATH) and os.path.getsize(LOCAL_BACKUP_PATH) > 0:
            shutil.copy2(LOCAL_BACKUP_PATH, DB_PATH)
            conn = get_connection()
            local_has_data = _sqlite_has_meaningful_data(conn)
            conn.close()
            if local_has_data:
                return {
                    "ok": True,
                    "status": "restore_completed_local_snapshot",
                    "path": LOCAL_BACKUP_PATH,
                }

        db = get_mongo_database()
        if db is None:
            return {
                "ok": False,
                "status": "mongo_not_configured",
                "message": "MongoDB is not reachable and no usable local snapshot was restored.",
            }

        conn = get_connection()
        collection_name = os.getenv("MONGO_BACKUP_COLLECTION", "sqlite_backups").strip() or "sqlite_backups"
        doc = db[collection_name].find_one({"backup_key": "latest"})
        if not doc or not isinstance(doc.get("tables"), dict):
            conn.close()
            return {
                "ok": True,
                "status": "no_backup_found",
                "collection": collection_name,
            }

        sqlite_tables = set(_get_sqlite_tables(conn))
        restored_rows = 0
        restored_tables = 0

        for table_name, rows in doc.get("tables", {}).items():
            if table_name not in sqlite_tables:
                continue

            conn.execute(f"DELETE FROM {table_name}")

            if not rows:
                restored_tables += 1
                continue

            if not isinstance(rows, list):
                continue

            first = rows[0]
            if not isinstance(first, dict) or not first:
                continue

            columns = list(first.keys())
            placeholders = ",".join(["?"] * len(columns))
            col_sql = ",".join(columns)
            insert_sql = f"INSERT INTO {table_name} ({col_sql}) VALUES ({placeholders})"

            values = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                values.append(tuple(row.get(col) for col in columns))

            if values:
                conn.executemany(insert_sql, values)
                restored_rows += len(values)

            restored_tables += 1

        conn.commit()
        conn.close()

        return {
            "ok": True,
            "status": "restore_completed",
            "collection": collection_name,
            "restored_tables": restored_tables,
            "restored_rows": restored_rows,
        }
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return {
            "ok": False,
            "status": "restore_failed",
            "error": str(exc),
        }