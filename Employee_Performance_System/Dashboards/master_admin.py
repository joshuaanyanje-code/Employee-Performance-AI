import streamlit as st
import pandas as pd
import os
from datetime import datetime, timedelta
from database.db import get_connection, hash_password, verify_password, execute_write, get_phone_uniqueness_error, DB_PATH as MAIN_DB_PATH
from Dashboards.ui_responsive import apply_responsive_ui
try:
    from Dashboards.ui_responsive import is_mobile_device
except Exception:
    def is_mobile_device():
        return False

# ==============================
# OPTIONAL IMPORTS
# ==============================
try:
    from payments.mpesa import stk_push
except Exception:
    stk_push = None

try:
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except Exception:
    PLOTLY_AVAILABLE = False

try:
    from Analytics.insights import generate_insights
    from Analytics.decision_engine import management_recommendations
    from Analytics.stability import (
        stability_analysis,
        detect_favoritism,
        detect_power_abuse_and_retaliation,
        detect_peer_gangup_and_targeting,
        detect_isolation_and_low_performers,
    )
    from Analytics.prediction import predict_future
    from Analytics.leadership import detect_leaders
    from Analytics.powermap import analyze_relationships
    from Analytics.badges import get_best_employees_across_organizations
    from Analytics.ai_recommendations import get_cached_recommendations
    ANALYTICS_AVAILABLE = True
except Exception:
    ANALYTICS_AVAILABLE = False

    def get_cached_recommendations(business_type="Office"):
        return {
            "articles": [],
            "fetched_at": "",
            "sources_ok": 0,
            "business_type": business_type,
            "error": "Live industry recommendations are currently unavailable.",
        }


# ==============================
# SAFE READ
# ==============================
def safe_read(query, conn, params=None):
    try:
        if params:
            return pd.read_sql(query, conn, params=params)
        return pd.read_sql(query, conn)
    except Exception:
        return pd.DataFrame()


def set_flash_message(key, level, text):
    st.session_state[key] = {"level": level, "text": text}


def show_flash_message(key):
    payload = st.session_state.pop(key, None)
    if not payload:
        return

    level = str(payload.get("level", "info")).lower()
    text = str(payload.get("text", "")).strip()
    if not text:
        return

    if level == "success":
        st.success(text)
    elif level == "warning":
        st.warning(text)
    elif level == "error":
        st.error(text)
    else:
        st.info(text)


# ==============================
# SCHEMA MIGRATION (safe, idempotent)
# ==============================
def run_migration(conn):
    try:
        execute_write(conn, "ALTER TABLE branches ADD COLUMN status TEXT DEFAULT 'active'")
        conn.commit()
    except Exception:
        pass
    try:
        execute_write(conn, """
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
        execute_write(conn, "INSERT OR IGNORE INTO payment_config(id) VALUES(1)")
        conn.commit()
    except Exception:
        pass
    try:
        execute_write(conn, "ALTER TABLE leaves ADD COLUMN reason TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    try:
        execute_write(conn, "ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass


# ==============================
# HELPERS
# ==============================
def calc_price(branch_count, cfg):
    if branch_count <= 1:
        return int(cfg.get("price_single_branch", 1000))
    return int(branch_count) * int(cfg.get("price_per_branch", 800))


def reset_database():
    import os
    import time
    db_file = MAIN_DB_PATH
    try:
        time.sleep(1)
        if os.path.exists(db_file):
            os.remove(db_file)
        conn = get_connection()
        conn.close()
        return True
    except Exception as e:
        return str(e)


def is_full_reset_enabled():
    return str(os.getenv("TEAM_AI_ALLOW_FULL_RESET", "1")).strip() == "1"


def load_cfg(conn):
    cfg_df = safe_read("SELECT * FROM payment_config WHERE id=1", conn)
    if not cfg_df.empty:
        return cfg_df.iloc[0].to_dict()
    return {
        "paybill": "", "till_number": "", "bank_name": "",
        "bank_account": "", "bank_branch": "",
        "price_single_branch": 1000, "price_per_branch": 800,
    }


def build_master_user_compliance_export(conn):
    users = safe_read(
        """
        SELECT
            username,
            phone,
            role,
            organization,
            branch,
            status,
            gender,
            created_at
        FROM users
        WHERE role != 'master'
        ORDER BY organization, branch, role, username
        """,
        conn,
    )

    orgs = safe_read(
        """
        SELECT
            name AS organization,
            phone AS organization_phone,
            email,
            location,
            status AS organization_status,
            expires_at
        FROM organizations
        """,
        conn,
    )

    if users.empty:
        return pd.DataFrame()

    out = users.merge(orgs, on="organization", how="left") if not orgs.empty else users.copy()
    return out


def get_master_export_sources(conn):
    users_export_df = safe_read(
        """
        SELECT username, phone, role, organization, branch, status, gender, created_at
        FROM users
        WHERE role != 'master'
        ORDER BY organization, branch, role, username
        """,
        conn,
    )
    org_export_df = safe_read(
        "SELECT id, name, phone, email, location, status, created_at, expires_at FROM organizations ORDER BY name",
        conn,
    )
    compliance_df = build_master_user_compliance_export(conn)

    return {
        "Users": users_export_df,
        "Organizations": org_export_df,
        "Compliance": compliance_df,
    }


def _safe_pct(numerator, denominator):
    try:
        if not denominator:
            return 0.0
        return round((float(numerator) / float(denominator)) * 100, 2)
    except Exception:
        return 0.0


def _parse_dt(series):
    try:
        return pd.to_datetime(series, errors="coerce")
    except Exception:
        return pd.Series(dtype="datetime64[ns]")


def summarize_attendance_lateness(attendance_df, lateness_df):
    if attendance_df.empty:
        return {
            "attendance_count": 0,
            "true_late_count": 0,
            "approved_late_count": 0,
            "true_late_rate": 0.0,
            "approved_late_rate": 0.0,
        }

    att = attendance_df.copy()
    att["date_key"] = pd.to_datetime(att["date"], errors="coerce").dt.strftime("%Y-%m-%d") if "date" in att.columns else ""
    att["status_upper"] = att["status"].astype(str).str.upper() if "status" in att.columns else ""

    approved_keys = set()
    if not lateness_df.empty:
        approved_rows = lateness_df[lateness_df["status"].astype(str).str.lower().isin(["approved", "used"])].copy()
        approved_rows["approved_for_date"] = approved_rows["approved_for_date"].astype(str)
        approved_keys = {
            (str(row.get("username", "")), str(row.get("approved_for_date", "")))
            for _, row in approved_rows.iterrows()
        }

    att["approved_late"] = att.apply(
        lambda row: (str(row.get("username", "")), str(row.get("date_key", ""))) in approved_keys,
        axis=1,
    )
    att["true_late"] = (att["status_upper"] == "LATE") & (~att["approved_late"])

    attendance_count = len(att)
    true_late_count = int(att["true_late"].sum())
    approved_late_count = int(att["approved_late"].sum())

    return {
        "attendance_count": attendance_count,
        "true_late_count": true_late_count,
        "approved_late_count": approved_late_count,
        "true_late_rate": (true_late_count / attendance_count) * 100 if attendance_count else 0.0,
        "approved_late_rate": (approved_late_count / attendance_count) * 100 if attendance_count else 0.0,
    }


def _normalize_business_type(value):
    raw = str(value or "Office").strip().lower()
    if any(token in raw for token in ["salon", "spa", "barber", "beauty", "restaurant", "hotel", "hospitality", "service", "clinic", "school", "cafe"]):
        return "Service"
    if any(token in raw for token in ["retail", "shop", "store", "boutique", "supermarket", "merch", "pharmacy"]):
        return "Merchandiser"
    if any(token in raw for token in ["manufact", "factory", "production", "plant", "industrial", "warehouse"]):
        return "Manufacturer"
    return "Office"


def get_business_playbook(value):
    normalized = _normalize_business_type(value)
    playbooks = {
        "Office": {
            "keep_doing": [
                "Run weekly manager score reviews and short team check-ins so issues are caught early.",
                "Keep clear service/quality standards documented and reviewed branch by branch.",
            ],
            "introduce": [
                "Introduce monthly branch scorecards with one owner per improvement area.",
                "Introduce customer/staff feedback review in management meetings.",
            ],
            "remove": [
                "Remove slow follow-up on warnings, leave approvals, and attendance issues.",
            ],
            "improve": [
                "Improve manager coaching consistency and cross-branch knowledge sharing.",
            ],
            "research": [
                "Research high-performing office teams that use clear KPIs, quick 1:1 coaching, and meeting discipline.",
            ],
        },
        "Service": {
            "keep_doing": [
                "Top service branches protect customer wait time, greeting quality, and shift punctuality every day.",
                "Strong service organizations use quick huddles before opening and end-of-day issue reviews.",
            ],
            "introduce": [
                "Introduce service recovery scripts and branch-level customer experience tracking.",
                "Introduce upsell or repeat-customer routines for strong staff at busy branches.",
            ],
            "remove": [
                "Remove long customer queues, inconsistent handovers, and late opening habits.",
            ],
            "improve": [
                "Improve cleanliness, service speed, and visible branch leadership during peak hours.",
            ],
            "research": [
                "Research hospitality and salon case studies on wait-time reduction, customer loyalty, and repeat visits.",
            ],
        },
        "Merchandiser": {
            "keep_doing": [
                "Strong retail-style branches keep shelves, stock visibility, and branch discipline tight every day.",
                "Top-performing merchandisers use branch scoreboards for conversion, shrinkage, and attendance.",
            ],
            "introduce": [
                "Introduce weekly merchandising audits and fast-moving product reviews by branch.",
                "Introduce cross-branch learning from the best-selling or best-disciplined branches.",
            ],
            "remove": [
                "Remove poor stock visibility, weak shift handovers, and unclear branch accountability.",
            ],
            "improve": [
                "Improve demand forecasting, customer flow management, and manager floor presence.",
            ],
            "research": [
                "Research retail case studies on merchandising discipline, repeat customers, and queue management.",
            ],
        },
        "Manufacturer": {
            "keep_doing": [
                "High-performing operations protect standard work, punctual start times, and daily quality checks.",
                "Top manufacturing teams review defects, downtime, and attendance at the start of each shift.",
            ],
            "introduce": [
                "Introduce visible KPI boards for defects, uptime, throughput, and late arrivals.",
                "Introduce short continuous-improvement reviews led by line or branch supervisors.",
            ],
            "remove": [
                "Remove repeated process drift, unclear ownership, and delayed corrective action.",
            ],
            "improve": [
                "Improve preventive planning, shift handover discipline, and manager response speed.",
            ],
            "research": [
                "Research lean operations and quality-improvement case studies from similar production teams.",
            ],
        },
    }
    selected = playbooks.get(normalized, playbooks["Office"])
    return {
        "business_type": str(value or normalized).strip() or normalized,
        "feed_type": normalized,
        **selected,
    }


def build_peer_benchmark_context(conn, org_name, business_type, avg_score, true_late_rate):
    orgs_df = safe_read(
        "SELECT name, business_type, status FROM organizations WHERE name != ? ORDER BY name",
        conn,
        params=(org_name,),
    )
    if orgs_df.empty:
        return [], []

    peer_type = _normalize_business_type(business_type)
    orgs_df["peer_type"] = orgs_df["business_type"].apply(_normalize_business_type)
    peers = orgs_df[orgs_df["peer_type"] == peer_type].copy()
    if peers.empty:
        peers = orgs_df.copy()

    rows = []
    for _, row in peers.iterrows():
        peer_name = str(row.get("name", "")).strip()
        if not peer_name:
            continue

        ratings_df = safe_read("SELECT score, branch FROM ratings WHERE organization=?", conn, params=(peer_name,))
        attendance_df = safe_read("SELECT status, branch FROM attendance WHERE organization=?", conn, params=(peer_name,))
        branches_df = safe_read("SELECT name, status FROM branches WHERE organization=?", conn, params=(peer_name,))

        peer_avg_score = round(float(pd.to_numeric(ratings_df.get("score"), errors="coerce").mean()), 2) if not ratings_df.empty and "score" in ratings_df.columns else 0.0
        late_count = int((attendance_df["status"].astype(str).str.upper() == "LATE").sum()) if not attendance_df.empty and "status" in attendance_df.columns else 0
        attendance_count = int(len(attendance_df))
        late_rate = round(_safe_pct(late_count, attendance_count), 1)
        active_branches = int((branches_df["status"].astype(str).str.lower() == "active").sum()) if not branches_df.empty and "status" in branches_df.columns else len(branches_df)

        rows.append({
            "Organization": peer_name,
            "Business Type": str(row.get("business_type", peer_type) or peer_type),
            "Status": str(row.get("status", "active") or "active"),
            "Avg Score": peer_avg_score,
            "True Late %": late_rate,
            "Active Branches": active_branches,
        })

    peer_df = pd.DataFrame(rows)
    if peer_df.empty:
        return [], []

    peer_df = peer_df.sort_values(["Avg Score", "True Late %", "Active Branches"], ascending=[False, True, False]).head(5)
    peer_avg_score = float(peer_df["Avg Score"].mean()) if not peer_df.empty else 0.0
    peer_avg_late = float(peer_df["True Late %"].mean()) if not peer_df.empty else 0.0
    top_names = ", ".join(peer_df["Organization"].head(3).astype(str).tolist())

    notes = []
    if top_names:
        notes.append(f"Top {peer_type.lower()} organisations currently leading in the system include {top_names}.")
    notes.append(f"Their average performance is about {peer_avg_score:.1f} with true lateness around {peer_avg_late:.1f}%.")
    if avg_score + 2 < peer_avg_score:
        notes.append(f"This organisation is trailing peer leaders by about {peer_avg_score - avg_score:.1f} performance points; copy the strongest branch routines first.")
    if true_late_rate > peer_avg_late + 5:
        notes.append("Peer leaders keep attendance tighter; add opening huddles, supervisor follow-up, and schedule discipline.")

    return notes[:4], peer_df.to_dict("records")


def generate_org_ai_intelligence(conn, org_name, include_peer_benchmark=True):
    org_df = safe_read("SELECT * FROM organizations WHERE name=?", conn, params=(org_name,))
    users_df = safe_read(
        """
        SELECT username, role, branch, status, gender, phone
        FROM users
        WHERE organization=? AND role!='master'
        """,
        conn,
        params=(org_name,),
    )
    branches_df = safe_read(
        "SELECT name, status FROM branches WHERE organization=?",
        conn,
        params=(org_name,),
    )
    ratings_df = safe_read(
        "SELECT score, branch, created_at, rated, rater FROM ratings WHERE organization=?",
        conn,
        params=(org_name,),
    )
    attendance_df = safe_read(
        "SELECT username, status, date, branch FROM attendance WHERE organization=?",
        conn,
        params=(org_name,),
    )
    lateness_df = safe_read(
        "SELECT username, approved_for_date, status FROM lateness_approvals WHERE organization=?",
        conn,
        params=(org_name,),
    )
    payments_df = safe_read(
        "SELECT amount, created_at FROM payments WHERE organization=?",
        conn,
        params=(org_name,),
    )
    warnings_df = safe_read(
        "SELECT type, created_at FROM warnings WHERE organization=?",
        conn,
        params=(org_name,),
    )
    leaves_df = safe_read(
        "SELECT status, start_date, end_date FROM leaves WHERE organization=?",
        conn,
        params=(org_name,),
    )
    messages_df = safe_read(
        "SELECT id, created_at FROM messages WHERE organization=?",
        conn,
        params=(org_name,),
    )

    total_users = len(users_df)
    total_admins = int((users_df["role"].astype(str).str.lower().isin(["superadmin", "admin"])).sum()) if not users_df.empty else 0
    total_employees = int((users_df["role"].astype(str).str.lower() == "employee").sum()) if not users_df.empty else 0
    active_users = int((users_df["status"].astype(str).str.lower() == "active").sum()) if not users_df.empty else 0

    male_count = int((users_df["gender"].astype(str).str.lower() == "male").sum()) if not users_df.empty and "gender" in users_df.columns else 0
    female_count = int((users_df["gender"].astype(str).str.lower() == "female").sum()) if not users_df.empty and "gender" in users_df.columns else 0
    phone_count = int(users_df["phone"].astype(str).str.strip().ne("").sum()) if not users_df.empty and "phone" in users_df.columns else 0

    active_branches = int((branches_df["status"].astype(str).str.lower() == "active").sum()) if not branches_df.empty and "status" in branches_df.columns else len(branches_df)
    total_branches = len(branches_df)

    avg_score = float(ratings_df["score"].mean()) if not ratings_df.empty else 0.0
    ratings_count = len(ratings_df)

    now = datetime.now()
    cutoff_30 = now - timedelta(days=30)
    cutoff_60 = now - timedelta(days=60)

    score_recent_30 = 0.0
    score_prev_30 = 0.0
    score_trend = "stable"
    if not ratings_df.empty and "created_at" in ratings_df.columns:
        ratings_df = ratings_df.copy()
        ratings_df["created_at"] = _parse_dt(ratings_df["created_at"])
        recent = ratings_df[ratings_df["created_at"] >= cutoff_30]
        prev = ratings_df[(ratings_df["created_at"] >= cutoff_60) & (ratings_df["created_at"] < cutoff_30)]
        score_recent_30 = float(recent["score"].mean()) if not recent.empty else 0.0
        score_prev_30 = float(prev["score"].mean()) if not prev.empty else 0.0
        if score_recent_30 > score_prev_30:
            score_trend = "improving"
        elif score_recent_30 < score_prev_30:
            score_trend = "declining"

    true_late_rate = 0.0
    approved_late_rate = 0.0
    true_late_count = 0
    approved_late_count = 0
    attendance_recent_30 = 0
    if not attendance_df.empty:
        lateness_summary = summarize_attendance_lateness(attendance_df, lateness_df)
        true_late_count = int(lateness_summary.get("true_late_count", 0))
        approved_late_count = int(lateness_summary.get("approved_late_count", 0))
        true_late_rate = float(lateness_summary.get("true_late_rate", 0.0))
        approved_late_rate = float(lateness_summary.get("approved_late_rate", 0.0))

        if "date" in attendance_df.columns:
            attendance_df = attendance_df.copy()
            attendance_df["date"] = _parse_dt(attendance_df["date"])
            attendance_recent_30 = int((attendance_df["date"] >= cutoff_30).sum())

    warnings_recent_30 = 0
    if not warnings_df.empty and "created_at" in warnings_df.columns:
        warnings_df = warnings_df.copy()
        warnings_df["created_at"] = _parse_dt(warnings_df["created_at"])
        warnings_recent_30 = int((warnings_df["created_at"] >= cutoff_30).sum())

    open_leaves = int((leaves_df["status"].astype(str).str.lower().isin(["pending", "reapply"])).sum()) if not leaves_df.empty and "status" in leaves_df.columns else 0

    payments_recent_30 = 0.0
    payments_prev_30 = 0.0
    payment_trend = "stable"
    if not payments_df.empty and "created_at" in payments_df.columns:
        payments_df = payments_df.copy()
        payments_df["created_at"] = _parse_dt(payments_df["created_at"])
        payments_df["amount"] = pd.to_numeric(payments_df["amount"], errors="coerce").fillna(0)
        pay_recent = payments_df[payments_df["created_at"] >= cutoff_30]
        pay_prev = payments_df[(payments_df["created_at"] >= cutoff_60) & (payments_df["created_at"] < cutoff_30)]
        payments_recent_30 = float(pay_recent["amount"].sum()) if not pay_recent.empty else 0.0
        payments_prev_30 = float(pay_prev["amount"].sum()) if not pay_prev.empty else 0.0
        if payments_recent_30 > payments_prev_30:
            payment_trend = "growing"
        elif payments_recent_30 < payments_prev_30:
            payment_trend = "declining"

    expires_days = None
    org_status = "unknown"
    business_type_raw = "Office"
    if not org_df.empty:
        org_status = str(org_df.iloc[0].get("status", "unknown"))
        business_type_raw = str(org_df.iloc[0].get("business_type", "Office") or "Office").strip() or "Office"
        expires_raw = org_df.iloc[0].get("expires_at")
        try:
            exp_dt = pd.to_datetime(expires_raw, errors="coerce")
            if pd.notna(exp_dt):
                expires_days = int((exp_dt.to_pydatetime() - now).days)
        except Exception:
            expires_days = None

    playbook = get_business_playbook(business_type_raw)

    message_activity_recent_30 = 0
    if not messages_df.empty and "created_at" in messages_df.columns:
        messages_df = messages_df.copy()
        messages_df["created_at"] = _parse_dt(messages_df["created_at"])
        message_activity_recent_30 = int((messages_df["created_at"] >= cutoff_30).sum())

    relationship_flags = []
    favoritism_flags = []
    power_flags = []
    peer_gangup_flags = []
    isolation_flags = []
    if ANALYTICS_AVAILABLE and not ratings_df.empty:
        try:
            relationship_flags = analyze_relationships(ratings_df, users_df, attendance_df)
        except Exception:
            relationship_flags = []
        try:
            favoritism_flags = detect_favoritism(ratings_df, users_df)
        except Exception:
            favoritism_flags = []
        try:
            power_flags = detect_power_abuse_and_retaliation(ratings_df, users_df, leaves_df, warnings_df, messages_df)
        except Exception:
            power_flags = []
        try:
            peer_gangup_flags = detect_peer_gangup_and_targeting(ratings_df, users_df)
        except Exception:
            peer_gangup_flags = []
        try:
            isolation_flags = detect_isolation_and_low_performers(ratings_df, users_df)
        except Exception:
            isolation_flags = []

    branch_variance = 0.0
    weakest_branch = "-"
    strongest_branch = "-"
    if not ratings_df.empty and "branch" in ratings_df.columns:
        by_branch = ratings_df.groupby("branch", dropna=False)["score"].mean().reset_index()
        by_branch.columns = ["branch", "avg_score"]
        if not by_branch.empty:
            branch_variance = float(by_branch["avg_score"].std()) if len(by_branch) > 1 else 0.0
            weakest_branch = str(by_branch.sort_values("avg_score", ascending=True).iloc[0]["branch"])
            strongest_branch = str(by_branch.sort_values("avg_score", ascending=False).iloc[0]["branch"])

    workforce_health = min(100.0, (float(active_users) / max(total_users, 1)) * 100)
    quality_health = min(100.0, avg_score)
    discipline_health = max(0.0, 100.0 - true_late_rate)
    financial_health = 80.0 if payment_trend == "growing" else 65.0 if payment_trend == "stable" else 45.0
    if expires_days is not None and expires_days < 7:
        financial_health = max(0.0, financial_health - 20.0)
    data_health = min(100.0, _safe_pct(phone_count, max(total_users, 1)) * 0.6 + (100.0 if ratings_count > 0 else 0.0) * 0.4)

    org_health_score = round(
        quality_health * 0.32 +
        workforce_health * 0.20 +
        discipline_health * 0.18 +
        financial_health * 0.20 +
        data_health * 0.10,
        2,
    )

    if org_health_score >= 78:
        org_health_status = "Strong"
    elif org_health_score >= 60:
        org_health_status = "Stable"
    else:
        org_health_status = "Needs Intervention"

    advice = []
    growth_moves = []
    risk_flags = []
    benchmark_notes = []
    benchmark_examples = []

    if include_peer_benchmark:
        benchmark_notes, benchmark_examples = build_peer_benchmark_context(
            conn,
            org_name,
            business_type_raw,
            avg_score,
            true_late_rate,
        )

    if avg_score < 65:
        risk_flags.append("Internal performance quality is low; coaching and standards enforcement are needed.")
        advice.append("ACTION: Run a 30-day manager-led quality improvement cycle with weekly score checkpoints.")
    else:
        growth_moves.append("Leverage current quality level to scale best practices across all branches.")

    if true_late_rate > 25:
        risk_flags.append(f"High true lateness rate ({true_late_rate:.1f}%).")
        advice.append("ACTION: Tighten attendance discipline and schedule adherence with branch-level accountability.")

    if approved_late_rate > 10:
        risk_flags.append(f"Approved lateness is elevated ({approved_late_rate:.1f}%).")
        advice.append("GUIDE: Review transport, shift timing, or staffing pressure because many late arrivals are being pre-excused.")

    if warnings_recent_30 > max(5, total_users // 2):
        risk_flags.append("Warning frequency is elevated in the last 30 days.")
        advice.append("SUPPORT: Investigate root causes such as leadership conflict, workload stress, or policy gaps.")

    if payment_trend == "declining":
        risk_flags.append("Payment momentum is declining.")
        advice.append("ACTION: Trigger a commercial recovery plan and verify billing follow-up cadence.")
    elif payment_trend == "growing":
        growth_moves.append("Payment trend is growing; explore controlled expansion and branch strengthening.")

    if expires_days is not None and expires_days <= 7:
        risk_flags.append(f"Subscription urgency: {expires_days} day(s) remaining.")
        advice.append("ACTION: Prioritize retention call and payment closure before expiry.")

    phone_coverage = _safe_pct(phone_count, max(total_users, 1))
    if phone_coverage < 90:
        advice.append("ACTION: Complete missing user phone contacts to improve communication reliability.")

    if branch_variance > 10:
        advice.append(
            f"GUIDE: Branch performance gap is high (variance {branch_variance:.2f}). Coach weakest branch '{weakest_branch}' using the strongest branch '{strongest_branch}' playbook."
        )

    if open_leaves > max(3, total_users // 6):
        advice.append("SUPPORT: Pending or reapply leaves are high; review staffing pressure and manager responsiveness.")

    if relationship_flags:
        risk_flags.append("Relationship conflict or team tension signals are showing in ratings patterns.")
        advice.append("ACTION: Review repeated conflict pairs and mediate branch-level tension before it spreads.")
    if favoritism_flags:
        risk_flags.append("Favoritism or scoring bias signals are present.")
        advice.append("ACTION: Audit rating fairness by managers/admins and rebalance branch accountability standards.")
    if power_flags:
        risk_flags.append("Manager or admin pressure / retaliation signals need review.")
        advice.append("SUPPORT: Check warnings, leave decisions, and manager follow-up for fairness and consistency.")
    if peer_gangup_flags:
        risk_flags.append("Peer gang-up or clique behavior may be weakening trust and morale.")
        advice.append("ACTION: Break up clique dynamics with closer supervision, rotation, and documented coaching.")
    if isolation_flags:
        risk_flags.append("Isolation or exclusion signals are affecting some staff.")
        advice.append("SUPPORT: Confirm whether weak scores reflect performance issues or social exclusion and bias.")

    root_causes = []
    if avg_score < 65:
        root_causes.append("low service or output quality")
    if true_late_rate > 20:
        root_causes.append("poor clock-in and attendance discipline")
    if open_leaves > max(3, total_users // 6):
        root_causes.append("staffing strain and leave backlog")
    if warnings_recent_30 > max(5, total_users // 2):
        root_causes.append("behavioral friction or policy issues")
    if total_admins < max(1, total_branches):
        root_causes.append("thin management/admin coverage")
    if payment_trend == "declining":
        root_causes.append("commercial or retention pressure")
    if relationship_flags or favoritism_flags or power_flags or peer_gangup_flags or isolation_flags:
        root_causes.append("relationship, bias, favoritism, or gang-up risk")
    if branch_variance > 10:
        root_causes.append("large execution gap between branches")

    if playbook.get("keep_doing"):
        growth_moves.append(f"KEEP: {playbook['keep_doing'][0]}")
    if org_health_status != "Strong" and playbook.get("introduce"):
        advice.append(f"ADD: {playbook['introduce'][0]}")
    if playbook.get("remove") and (true_late_rate > 15 or warnings_recent_30 > max(3, total_users // 3)):
        advice.append(f"REMOVE: {playbook['remove'][0]}")
    if playbook.get("improve"):
        advice.append(f"IMPROVE: {playbook['improve'][0]}")
    if playbook.get("research"):
        growth_moves.append(f"RESEARCH: {playbook['research'][0]}")

    for note in benchmark_notes[:3]:
        if "trailing" in note.lower() or "attendance tighter" in note.lower():
            advice.append(f"BENCHMARK: {note}")
        else:
            growth_moves.append(f"BENCHMARK: {note}")

    if not root_causes:
        root_causes.append("no major systemic root cause detected")
    if not advice:
        advice.append("KEEP: Continue current operating rhythm; no high-severity intervention detected.")

    return {
        "organization": org_name,
        "business_type": business_type_raw,
        "org_status": org_status,
        "org_health_score": org_health_score,
        "org_health_status": org_health_status,
        "users": {
            "total": total_users,
            "admins": total_admins,
            "employees": total_employees,
            "active": active_users,
            "male": male_count,
            "female": female_count,
            "phone_coverage_pct": phone_coverage,
        },
        "operations": {
            "branches_total": total_branches,
            "branches_active": active_branches,
            "attendance_recent_30": attendance_recent_30,
            "true_late_count": true_late_count,
            "approved_late_count": approved_late_count,
            "late_rate_pct": true_late_rate,
            "approved_late_rate_pct": approved_late_rate,
            "warnings_recent_30": warnings_recent_30,
            "open_leaves": open_leaves,
            "message_activity_recent_30": message_activity_recent_30,
        },
        "performance": {
            "avg_score": round(avg_score, 2),
            "ratings_count": ratings_count,
            "score_recent_30": round(score_recent_30, 2),
            "score_prev_30": round(score_prev_30, 2),
            "score_trend": score_trend,
            "branch_variance": round(branch_variance, 2),
            "strongest_branch": strongest_branch,
            "weakest_branch": weakest_branch,
        },
        "finance": {
            "payments_recent_30": round(payments_recent_30, 2),
            "payments_prev_30": round(payments_prev_30, 2),
            "payment_trend": payment_trend,
            "expires_in_days": expires_days,
        },
        "playbook": {
            "business_type": playbook.get("business_type", business_type_raw),
            "feed_type": playbook.get("feed_type", "Office"),
            "keep_doing": playbook.get("keep_doing", [])[:3],
            "introduce": playbook.get("introduce", [])[:3],
            "remove": playbook.get("remove", [])[:3],
            "improve": playbook.get("improve", [])[:3],
            "research": playbook.get("research", [])[:3],
        },
        "root_causes": root_causes[:10],
        "people_signals": {
            "relationships": relationship_flags[:8],
            "favoritism": favoritism_flags[:8],
            "management_pressure": power_flags[:8],
            "peer_gangups": peer_gangup_flags[:8],
            "isolation": isolation_flags[:8],
        },
        "benchmark_notes": benchmark_notes[:6],
        "benchmark_examples": benchmark_examples[:5],
        "risk_flags": risk_flags[:10],
        "growth_moves": growth_moves[:10],
        "advice": advice[:16],
    }


def build_branch_performance_table(ratings_df, attendance_df, users_df, branches_df=None):
    branch_names = set()

    for df, col in [(ratings_df, "branch"), (attendance_df, "branch"), (users_df, "branch")]:
        if df is not None and not df.empty and col in df.columns:
            branch_names.update(
                str(v).strip() for v in df[col].dropna().tolist() if str(v).strip()
            )

    if branches_df is not None and not branches_df.empty and "name" in branches_df.columns:
        branch_names.update(
            str(v).strip() for v in branches_df["name"].dropna().tolist() if str(v).strip()
        )

    if not branch_names:
        return pd.DataFrame()

    rows = []
    for branch in sorted(branch_names):
        branch_status = "active"
        if branches_df is not None and not branches_df.empty and {"name", "status"}.issubset(branches_df.columns):
            match = branches_df[branches_df["name"].astype(str).str.strip() == branch]
            if not match.empty:
                branch_status = str(match.iloc[0].get("status", "active") or "active").strip().lower()

        branch_ratings = ratings_df[ratings_df["branch"].astype(str).str.strip() == branch].copy() if ratings_df is not None and not ratings_df.empty and "branch" in ratings_df.columns else pd.DataFrame()
        branch_attendance = attendance_df[attendance_df["branch"].astype(str).str.strip() == branch].copy() if attendance_df is not None and not attendance_df.empty and "branch" in attendance_df.columns else pd.DataFrame()
        branch_users = users_df[users_df["branch"].astype(str).str.strip() == branch].copy() if users_df is not None and not users_df.empty and "branch" in users_df.columns else pd.DataFrame()

        avg_score = round(float(pd.to_numeric(branch_ratings.get("score"), errors="coerce").mean()), 2) if not branch_ratings.empty and "score" in branch_ratings.columns else 0.0
        rating_count = int(len(branch_ratings))
        attendance_count = int(len(branch_attendance))
        late_count = int((branch_attendance["status"].astype(str).str.upper() == "LATE").sum()) if not branch_attendance.empty and "status" in branch_attendance.columns else 0
        late_rate = round(_safe_pct(late_count, attendance_count), 1)
        total_users = int(len(branch_users))
        active_users = int((branch_users["status"].astype(str).str.lower() == "active").sum()) if not branch_users.empty and "status" in branch_users.columns else total_users

        if branch_status != "active":
            health = "Inactive"
            recommendation = "Branch is inactive; only reactivate when staffing and leadership are ready."
        elif rating_count == 0 and attendance_count == 0:
            health = "No Data"
            recommendation = "No branch activity data yet; verify rollout, usage, and reporting discipline."
        elif avg_score >= 80 and late_rate <= 10:
            health = "Strong"
            recommendation = "Use this branch as a benchmark and replicate its working playbook elsewhere."
        elif avg_score >= 65 and late_rate <= 20:
            health = "Stable"
            recommendation = "Maintain branch rhythm with light coaching and attendance follow-up."
        elif avg_score < 65 and late_rate > 20:
            health = "Needs Support"
            recommendation = "Urgent coaching needed: improve service quality and tighten attendance control."
        elif avg_score < 65:
            health = "Needs Support"
            recommendation = "Performance quality is low; schedule manager coaching and branch skill refresh."
        else:
            health = "Needs Support"
            recommendation = "Attendance discipline needs attention to protect branch performance."

        rows.append({
            "Branch": branch,
            "Status": branch_status,
            "Avg Score": avg_score,
            "Ratings": rating_count,
            "Attendance": attendance_count,
            "Late Cases": late_count,
            "Late %": late_rate,
            "Active Users": active_users,
            "Total Users": total_users,
            "Branch Health": health,
            "Recommended Action": recommendation,
        })

    return pd.DataFrame(rows).sort_values(["Status", "Avg Score", "Branch"], ascending=[True, False, True])


def build_cross_org_branch_benchmark(conn, business_type=None, exclude_org=None):
    orgs_df = safe_read("SELECT name, business_type, status FROM organizations ORDER BY name", conn)
    if orgs_df.empty:
        return pd.DataFrame()

    normalized_type = _normalize_business_type(business_type)
    rows = []

    for _, org_row in orgs_df.iterrows():
        org_name = str(org_row.get("name", "") or "").strip()
        if not org_name:
            continue
        if exclude_org and org_name == exclude_org:
            continue

        peer_type = _normalize_business_type(org_row.get("business_type", "Office"))
        if business_type and peer_type != normalized_type:
            continue

        ratings_df = safe_read("SELECT score, branch FROM ratings WHERE organization=?", conn, params=(org_name,))
        attendance_df = safe_read("SELECT status, branch FROM attendance WHERE organization=?", conn, params=(org_name,))
        users_df = safe_read("SELECT username, role, branch, status FROM users WHERE organization=?", conn, params=(org_name,))
        branches_df = safe_read("SELECT name, status FROM branches WHERE organization=?", conn, params=(org_name,))

        branch_df = build_branch_performance_table(ratings_df, attendance_df, users_df, branches_df)
        if branch_df.empty:
            continue

        branch_df = branch_df.copy()
        branch_df["Organization"] = org_name
        branch_df["Business Type"] = str(org_row.get("business_type", peer_type) or peer_type)
        rows.append(branch_df)

    if not rows:
        return pd.DataFrame()

    benchmark_df = pd.concat(rows, ignore_index=True)
    if benchmark_df.empty:
        return benchmark_df

    health_rank = {"Strong": 0, "Stable": 1, "Needs Support": 2, "No Data": 3, "Inactive": 4}
    benchmark_df["health_rank"] = benchmark_df["Branch Health"].map(health_rank).fillna(9)
    benchmark_df = benchmark_df.sort_values(
        ["health_rank", "Avg Score", "Late %", "Active Users"],
        ascending=[True, False, True, False],
    ).drop(columns=["health_rank"])
    return benchmark_df.reset_index(drop=True)


def build_branch_diagnostic_table(
    conn,
    org_name,
    business_type,
    ratings_df,
    attendance_df,
    users_df,
    leaves_df=None,
    warnings_df=None,
    messages_df=None,
    branches_df=None,
):
    branch_view = build_branch_performance_table(ratings_df, attendance_df, users_df, branches_df)
    if branch_view.empty:
        return pd.DataFrame()

    peer_benchmark = build_cross_org_branch_benchmark(conn, business_type=business_type)
    diagnostics = []

    for _, row in branch_view.iterrows():
        branch = str(row.get("Branch", "") or "").strip()
        if not branch:
            continue

        branch_ratings = ratings_df[ratings_df["branch"].astype(str).str.strip() == branch].copy() if ratings_df is not None and not ratings_df.empty and "branch" in ratings_df.columns else pd.DataFrame()
        branch_attendance = attendance_df[attendance_df["branch"].astype(str).str.strip() == branch].copy() if attendance_df is not None and not attendance_df.empty and "branch" in attendance_df.columns else pd.DataFrame()
        branch_users = users_df[users_df["branch"].astype(str).str.strip() == branch].copy() if users_df is not None and not users_df.empty and "branch" in users_df.columns else pd.DataFrame()
        branch_leaves = leaves_df[leaves_df["branch"].astype(str).str.strip() == branch].copy() if leaves_df is not None and not leaves_df.empty and "branch" in leaves_df.columns else pd.DataFrame()
        branch_warnings = warnings_df[warnings_df["branch"].astype(str).str.strip() == branch].copy() if warnings_df is not None and not warnings_df.empty and "branch" in warnings_df.columns else pd.DataFrame()
        branch_messages = messages_df[messages_df["branch"].astype(str).str.strip() == branch].copy() if messages_df is not None and not messages_df.empty and "branch" in messages_df.columns else pd.DataFrame()

        total_users = int(len(branch_users))
        admin_count = int(branch_users["role"].astype(str).str.lower().isin(["admin", "manager", "superadmin", "super_admin"]).sum()) if not branch_users.empty and "role" in branch_users.columns else 0
        absent_count = int(branch_attendance["status"].astype(str).str.upper().str.contains("ABSENT|NO SHOW|NOSHOW|MISS", regex=True, na=False).sum()) if not branch_attendance.empty and "status" in branch_attendance.columns else 0
        pending_leaves = int(branch_leaves["status"].astype(str).str.lower().isin(["pending", "reapply"]).sum()) if not branch_leaves.empty and "status" in branch_leaves.columns else 0
        warning_count = int(len(branch_warnings))
        message_count = int(len(branch_messages))

        people_risks = []
        if ANALYTICS_AVAILABLE and not branch_ratings.empty:
            try:
                if detect_favoritism(branch_ratings, branch_users):
                    people_risks.append("favoritism/bias")
            except Exception:
                pass
            try:
                if detect_power_abuse_and_retaliation(branch_ratings, branch_users, branch_leaves, branch_warnings, branch_messages):
                    people_risks.append("management pressure")
            except Exception:
                pass
            try:
                if detect_peer_gangup_and_targeting(branch_ratings, branch_users):
                    people_risks.append("gang-up/clique")
            except Exception:
                pass
            try:
                if detect_isolation_and_low_performers(branch_ratings, branch_users):
                    people_risks.append("isolation/low morale")
            except Exception:
                pass
            try:
                if analyze_relationships(branch_ratings, branch_users, branch_attendance):
                    people_risks.append("relationship conflict")
            except Exception:
                pass

        root_causes = []
        if float(row.get("Avg Score", 0)) < 65:
            root_causes.append("low service/performance quality")
        if float(row.get("Late %", 0)) > 20 or absent_count > 0:
            root_causes.append("attendance or absenteeism discipline")
        if pending_leaves > max(2, total_users // 3):
            root_causes.append("staffing pressure and leave backlog")
        if warning_count > max(2, total_users // 2):
            root_causes.append("behavior or policy friction")
        if admin_count == 0 and total_users > 0:
            root_causes.append("weak branch management coverage")
        if message_count == 0 and float(row.get("Avg Score", 0)) < 65 and total_users >= 3:
            root_causes.append("low management follow-up or communication")
        if people_risks:
            root_causes.append("people-risk tension: " + ", ".join(sorted(set(people_risks))[:3]))
        if not root_causes:
            root_causes.append("no major weakness detected; maintain standards and benchmark discipline")

        partner_label = "-"
        same_org_partner = branch_view[
            (branch_view["Branch"].astype(str) != branch)
            & (branch_view["Status"].astype(str).str.lower() == "active")
            & (pd.to_numeric(branch_view["Avg Score"], errors="coerce") >= float(row.get("Avg Score", 0)) + 5)
        ].sort_values(["Avg Score", "Late %"], ascending=[False, True]).head(1)
        if not same_org_partner.empty:
            partner_label = f"{same_org_partner.iloc[0]['Branch']} ({org_name})"
        elif not peer_benchmark.empty:
            peer_partner = peer_benchmark[
                (peer_benchmark["Organization"].astype(str) != org_name)
                & (peer_benchmark["Status"].astype(str).str.lower() == "active")
                & (peer_benchmark["Branch Health"].astype(str).isin(["Strong", "Stable"]))
            ].head(1)
            if not peer_partner.empty:
                partner_label = f"{peer_partner.iloc[0]['Branch']} ({peer_partner.iloc[0]['Organization']})"

        action_parts = []
        if partner_label != "-" and str(row.get("Branch Health", "")) != "Strong":
            action_parts.append(f"Use {partner_label} as the learning partner branch.")
        if str(row.get("Recommended Action", "")).strip():
            action_parts.append(str(row.get("Recommended Action", "")).strip())
        if people_risks:
            action_parts.append("Review manager fairness, peer dynamics, and any clique or targeting patterns.")
        if float(row.get("Late %", 0)) > 20 or absent_count > 0:
            action_parts.append("Tighten daily clock-in, absence follow-up, and opening discipline.")

        diagnostics.append({
            "Branch": branch,
            "Branch Health": row.get("Branch Health", "Unknown"),
            "Avg Score": row.get("Avg Score", 0),
            "Late %": row.get("Late %", 0),
            "Absent Cases": absent_count,
            "Pending Leaves": pending_leaves,
            "Warnings (30d)": warning_count,
            "Admins": admin_count,
            "People Risks": ", ".join(sorted(set(people_risks))) if people_risks else "None detected",
            "Root Cause Summary": "; ".join(root_causes[:4]),
            "Learning Partner": partner_label,
            "Recommended Intervention": " ".join(dict.fromkeys(action_parts)),
        })

    diag_df = pd.DataFrame(diagnostics)
    if not diag_df.empty:
        severity_rank = {"Needs Support": 0, "No Data": 1, "Inactive": 2, "Stable": 3, "Strong": 4}
        diag_df["severity_rank"] = diag_df["Branch Health"].map(severity_rank).fillna(9)
        diag_df = diag_df.sort_values(["severity_rank", "Avg Score", "Late %"], ascending=[True, True, False]).drop(columns=["severity_rank"])
    return diag_df


def build_priority_action_flow(intel, branch_diag=None):
    root_causes = [str(x).strip() for x in intel.get("root_causes", []) if str(x).strip()]
    risk_flags = [str(x).strip() for x in intel.get("risk_flags", []) if str(x).strip()]
    urgent_actions = []
    for item in intel.get("advice", []):
        text = str(item).strip()
        upper = text.upper()
        if any(tag in upper for tag in ["ACTION:", "ADD:", "REMOVE:", "IMPROVE:", "BENCHMARK:"]):
            urgent_actions.append(text)
    urgent_actions = urgent_actions[:3]

    flow = {
        "problem": "No urgent organization problem detected right now.",
        "causes": root_causes[:3] if root_causes else risk_flags[:3],
        "learning_partner": "-",
        "learning_reason": "No clear benchmark partner identified yet.",
        "urgent_actions": urgent_actions,
    }

    if branch_diag is not None and not branch_diag.empty:
        watch_df = branch_diag[
            branch_diag["Branch Health"].astype(str).isin(["Needs Support", "No Data", "Inactive"])
        ].copy()
        if watch_df.empty:
            watch_df = branch_diag.sort_values(["Avg Score", "Late %"], ascending=[True, False]).head(1)

        if not watch_df.empty:
            row = watch_df.iloc[0]
            branch_name = str(row.get("Branch", "this branch"))
            flow["problem"] = (
                f"{branch_name} is the main watch branch: {row.get('Branch Health', 'Needs Review')} "
                f"(score {row.get('Avg Score', 0)}, late {row.get('Late %', 0)}%)."
            )
            branch_causes = [
                part.strip() for part in str(row.get("Root Cause Summary", "")).split(";") if part.strip()
            ]
            if branch_causes:
                flow["causes"] = branch_causes[:3]

            partner = str(row.get("Learning Partner", "") or "").strip()
            if partner and partner != "-":
                flow["learning_partner"] = partner
                flow["learning_reason"] = str(row.get("Recommended Intervention", "") or "").strip() or "Use this partner branch to copy stronger routines."
            elif str(row.get("Recommended Intervention", "") or "").strip():
                flow["learning_reason"] = str(row.get("Recommended Intervention", "") or "").strip()

    if not flow["causes"]:
        flow["causes"] = ["No major root cause is currently standing out."]
    if not flow["urgent_actions"]:
        flow["urgent_actions"] = ["Continue current operating rhythm and keep monitoring branch performance."]

    return flow


def build_master_advisor_benchmark(conn):
    orgs = safe_read("SELECT name, business_type FROM organizations ORDER BY name", conn)
    if orgs.empty:
        return pd.DataFrame(), []

    rows = []
    key_alerts = []
    for _, org_row in orgs.iterrows():
        org_name = str(org_row.get("name", "")).strip()
        if not org_name:
            continue

        intel = generate_org_ai_intelligence(conn, org_name, include_peer_benchmark=False)
        rows.append({
            "Organization": org_name,
            "Business Type": intel.get("business_type", "Office"),
            "Health Score": intel.get("org_health_score", 0),
            "Health Status": intel.get("org_health_status", "Unknown"),
            "Users": intel.get("users", {}).get("total", 0),
            "Avg Score": intel.get("performance", {}).get("avg_score", 0),
            "True Late %": intel.get("operations", {}).get("late_rate_pct", 0),
            "Approved Late %": intel.get("operations", {}).get("approved_late_rate_pct", 0),
            "Payment Trend": intel.get("finance", {}).get("payment_trend", "stable"),
            "Primary Root Cause": "; ".join(intel.get("root_causes", [])[:2]) or "Monitor",
            "Expiry (days)": intel.get("finance", {}).get("expires_in_days", "N/A"),
        })

        if intel.get("org_health_status") == "Needs Intervention":
            key_alerts.append(f"{org_name}: Needs intervention (health {intel.get('org_health_score', 0):.1f}).")
        if (intel.get("finance", {}).get("expires_in_days") is not None and
                int(intel.get("finance", {}).get("expires_in_days")) <= 7):
            key_alerts.append(f"{org_name}: Subscription expiring in {intel.get('finance', {}).get('expires_in_days')} day(s).")

    benchmark_df = pd.DataFrame(rows)
    if not benchmark_df.empty:
        benchmark_df = benchmark_df.sort_values("Health Score", ascending=False)

    return benchmark_df, key_alerts[:12]


# ==============================
# MASTER DASHBOARD
# ==============================
def master_admin_dashboard():

    apply_responsive_ui("default")

    conn = get_connection()
    run_migration(conn)

    st.title("🔐 Chief Administrator Control Panel")

    is_mobile = is_mobile_device()

    def _collapse_master_mobile_nav():
        if is_mobile:
            st.session_state["master_nav_open"] = False

    if "master_nav_open" not in st.session_state:
        st.session_state["master_nav_open"] = True

    def nav_selectbox(label, options, key, **kwargs):
        if is_mobile:
            return st.selectbox(label, options, key=key, **kwargs)
        with st.sidebar:
            return st.selectbox(label, options, key=key, **kwargs)

    def nav_multiselect(label, options, default, key, **kwargs):
        if is_mobile:
            return st.multiselect(label, options, default=default, key=key, **kwargs)
        with st.sidebar:
            return st.multiselect(label, options, default=default, key=key, **kwargs)

    menu_items = [
        "📊 Overview",
        "🏢 Organizations",
        "💰 Payments",
        "🌿 Branches",
        "👥 Employees",
        "📈 Analytics",
        "⚙️ Settings",
    ]

    if is_mobile:
        if st.button("Change Navigation", key="master_reopen_nav", use_container_width=True):
            st.session_state["master_nav_open"] = True
            st.rerun()
        with st.expander("Navigation", expanded=bool(st.session_state.get("master_nav_open", True))):
            menu = st.radio("Menu", menu_items, key="master_menu", on_change=_collapse_master_mobile_nav)
    else:
        with st.sidebar:
            st.markdown("### Navigation")
            menu = st.radio("Menu", menu_items, key="master_menu")

    # ==========================================================
    # OVERVIEW
    # ==========================================================
    if menu == "📊 Overview":

        st.subheader("📊 System Overview")

        orgs = safe_read("SELECT * FROM organizations", conn)
        branches = safe_read("SELECT * FROM branches", conn)
        users = safe_read("SELECT * FROM users WHERE role != 'master'", conn)

        now = datetime.now()

        c1, c2, c3, c4 = st.columns(4)
        total_orgs = len(orgs)
        total_branches = len(branches)
        active_orgs = len(orgs[orgs["status"] == "active"]) if not orgs.empty else 0
        expired_orgs = len(orgs[orgs["status"] != "active"]) if not orgs.empty else 0

        c1.metric("🏢 Organizations", total_orgs)
        c2.metric("🌿 Total Branches", total_branches)
        c3.metric("🟢 Active Orgs", active_orgs)
        c4.metric("🔴 Inactive / Expired", expired_orgs)

        c5, c6, c7 = st.columns(3)
        total_users = len(users)
        total_admins = len(users[users["role"].isin(["superadmin", "admin"])]) if not users.empty else 0
        total_employees = len(users[users["role"] == "employee"]) if not users.empty else 0

        c5.metric("👤 Total Users", total_users)
        c6.metric("🛡 Admins", total_admins)
        c7.metric("👷 Employees", total_employees)

        c8, c9 = st.columns(2)
        male_users = 0
        female_users = 0
        if not users.empty and "gender" in users.columns:
            male_users = int((users["gender"].astype(str).str.lower() == "male").sum())
            female_users = int((users["gender"].astype(str).str.lower() == "female").sum())
        c8.metric("👨 Male Users", male_users)
        c9.metric("👩 Female Users", female_users)

        st.divider()

        if orgs.empty:
            st.info("No organizations yet.")
        else:
            rows = []
            for _, row in orgs.iterrows():
                org_name = row["name"]
                b_count = len(branches[branches["organization"] == org_name]) if not branches.empty else 0
                u_count = len(users[users["organization"] == org_name]) if not users.empty else 0
                status = row.get("status", "unknown")
                expires = row.get("expires_at", "")
                days_left = "-"

                if expires:
                    try:
                        exp_dt = datetime.strptime(str(expires)[:19], "%Y-%m-%d %H:%M:%S")
                        days_left = (exp_dt - now).days
                    except Exception:
                        days_left = "N/A"

                rows.append({
                    "Organization": org_name,
                    "Branches": b_count,
                    "Users": u_count,
                    "Status": status,
                    "Expires At": str(expires)[:10] if expires else "-",
                    "Days Left": days_left,
                })

            df_summary = pd.DataFrame(rows)

            def color_row(r):
                d = r["Days Left"]
                if r["Status"] != "active":
                    return ["background-color: #ffcccc"] * len(r)
                if isinstance(d, int) and d <= 7:
                    return ["background-color: #fff3cd"] * len(r)
                return [""] * len(r)

            st.dataframe(df_summary.style.apply(color_row, axis=1), use_container_width=True)

        st.divider()
        st.markdown("### ⬇ Chief Administrator Data Exports")
        export_sources = get_master_export_sources(conn)
        users_export_df = export_sources["Users"]
        org_export_df = export_sources["Organizations"]
        compliance_df = export_sources["Compliance"]

        ex1, ex2, ex3 = st.columns(3)
        with ex1:
            st.download_button(
                "Download Users CSV",
                data=users_export_df.to_csv(index=False),
                file_name="master_users_export.csv",
                mime="text/csv",
                disabled=users_export_df.empty,
            )
        with ex2:
            st.download_button(
                "Download Organizations CSV",
                data=org_export_df.to_csv(index=False),
                file_name="master_organizations_export.csv",
                mime="text/csv",
                disabled=org_export_df.empty,
            )
        with ex3:
            st.download_button(
                "Download Compliance CSV",
                data=compliance_df.to_csv(index=False),
                file_name="master_user_compliance_export.csv",
                mime="text/csv",
                disabled=compliance_df.empty,
            )

        st.markdown("#### Custom Export Builder")
        export_kind = nav_selectbox(
            "Dataset",
            ["Users", "Organizations", "Compliance"],
            key="master_export_kind",
        )
        org_filter_opt = ["All"]
        if not users_export_df.empty and "organization" in users_export_df.columns:
            org_filter_opt += sorted(users_export_df["organization"].dropna().unique().tolist())
        selected_org_filter = nav_selectbox("Organization Filter", org_filter_opt, key="master_export_org_filter")

        base_df = export_sources.get(export_kind, pd.DataFrame()).copy()
        if selected_org_filter != "All" and not base_df.empty:
            if "organization" in base_df.columns:
                base_df = base_df[base_df["organization"] == selected_org_filter]
            elif export_kind == "Organizations" and "name" in base_df.columns:
                base_df = base_df[base_df["name"] == selected_org_filter]

        if base_df.empty:
            st.info("No rows available for selected export filters.")
        else:
            available_columns = base_df.columns.tolist()
            default_columns = available_columns[: min(len(available_columns), 8)]
            selected_columns = nav_multiselect(
                "Columns to Export",
                available_columns,
                default=default_columns,
                key="master_export_columns",
            )

            if selected_columns:
                custom_df = base_df[selected_columns].copy()
            else:
                custom_df = base_df.copy()

            st.caption(f"Preview: {len(custom_df)} row(s), {len(custom_df.columns)} column(s)")
            st.dataframe(custom_df.head(50), use_container_width=True)

            safe_name = export_kind.lower().replace(" ", "_")
            if selected_org_filter != "All":
                safe_name = f"{safe_name}_{str(selected_org_filter).replace(' ', '_')}"

            st.download_button(
                "Download Custom CSV",
                data=custom_df.to_csv(index=False),
                file_name=f"master_custom_export_{safe_name}.csv",
                mime="text/csv",
            )

    # ==========================================================
    # ORGANIZATIONS
    # ==========================================================
    elif menu == "🏢 Organizations":

        st.subheader("🏢 Manage Organizations")
        show_flash_message("master_org_flash")

        tab_list, tab_create, tab_edit, tab_delete, tab_passwd = st.tabs([
            "📋 List", "➕ Create", "✏️ Edit", "🗑️ Delete", "🔑 Reset Password"
        ])

        with tab_list:
            df = safe_read(
                "SELECT id, name, business_type, status, phone, email, location, created_at, expires_at FROM organizations",
                conn
            )
            if df.empty:
                st.info("No organizations yet.")
            else:
                st.dataframe(df, use_container_width=True)
                st.download_button(
                    "Export Organizations (CSV)",
                    data=df.to_csv(index=False),
                    file_name="organizations_list.csv",
                    mime="text/csv",
                )

        with tab_create:
            with st.form("create_org", clear_on_submit=False):
                st.markdown("#### New Organization")
                name             = st.text_input("Organization Name")
                superadmin       = st.text_input("Super Admin Username")
                org_phone        = st.text_input("Organization Phone (full format e.g. 2547XXXXXXXX)")
                superadmin_phone = st.text_input("Super Admin Phone (full format e.g. 2547XXXXXXXX)")
                password         = st.text_input("Password", type="password")
                confirm_pw    = st.text_input("Confirm Password", type="password")
                email         = st.text_input("Email (optional)")
                location      = st.text_input("Location")
                _BIZ_TYPES    = ["Office", "Service", "Merchandiser", "Manufacturer"]
                business_type = st.selectbox(
                    "Business Type",
                    _BIZ_TYPES,
                    help="Office – desk-based; Service – hospitality/retail service; Merchandiser – retail/distribution; Manufacturer – production/factory"
                )
                submitted  = st.form_submit_button("✅ Create Organization")

                if submitted:
                    if not name or not superadmin or not password or not org_phone.strip() or not superadmin_phone.strip():
                        st.error("Organization name, super admin username, password, organization phone, and super admin phone are mandatory.")
                    elif password != confirm_pw:
                        st.error("Passwords do not match.")
                    else:
                        try:
                            org_name = name.strip()
                            superadmin_name = superadmin.strip()
                            existing_org = safe_read(
                                "SELECT id FROM organizations WHERE lower(trim(name)) = lower(trim(?))",
                                conn,
                                params=(org_name,),
                            )
                            existing_user = safe_read(
                                "SELECT id FROM users WHERE lower(trim(username)) = lower(trim(?))",
                                conn,
                                params=(superadmin_name,),
                            )
                            normalized_org_phone, org_phone_error = get_phone_uniqueness_error(conn, org_phone)
                            normalized_superadmin_phone, superadmin_phone_error = get_phone_uniqueness_error(conn, superadmin_phone)

                            if not existing_org.empty:
                                st.error(f"Organization '{org_name}' already exists.")
                            elif not existing_user.empty:
                                st.error(f"Username '{superadmin_name}' already exists.")
                            elif org_phone_error:
                                st.error(org_phone_error)
                            elif superadmin_phone_error:
                                st.error(superadmin_phone_error)
                            elif normalized_org_phone == normalized_superadmin_phone:
                                st.error("Organization phone and super admin phone must be different. Phone numbers cannot be shared.")
                            else:
                                expiry = datetime.now() + timedelta(days=30)
                                execute_write(conn, """
                                    INSERT INTO organizations(name, status, phone, email, location, created_at, expires_at, business_type)
                                    VALUES (?,?,?,?,?,?,?,?)
                                """, (org_name, "active", normalized_org_phone, email.strip(), location.strip(),
                                      str(datetime.now()), str(expiry), business_type))
                                execute_write(conn, """
                                    INSERT INTO users(username, password, role, organization, status, phone)
                                    VALUES (?,?,?,?,?,?)
                                """, (superadmin_name, hash_password(password), "superadmin", org_name, "active", normalized_superadmin_phone))
                                conn.commit()
                                set_flash_message(
                                    "master_org_flash",
                                    "success",
                                    f"Organization '{org_name}' created. Active for 30 days.",
                                )
                                st.rerun()
                        except Exception as e:
                            st.error(str(e))

        with tab_edit:
            df_e = safe_read("SELECT * FROM organizations", conn)
            if df_e.empty:
                st.info("No organizations to edit.")
            else:
                org_sel = st.selectbox("Select Organization to Edit", df_e["name"].tolist(), key="edit_org_sel")
                row     = df_e[df_e["name"] == org_sel].iloc[0]

                with st.form("edit_org", clear_on_submit=False):
                    new_name   = st.text_input("Organization Name", value=str(row.get("name",     "") or ""))
                    new_phone  = st.text_input("Phone (full format e.g. 2547XXXXXXXX)", value=str(row.get("phone", "") or ""))
                    new_email  = st.text_input("Email",             value=str(row.get("email",    "") or ""))
                    new_loc    = st.text_input("Location",          value=str(row.get("location", "") or ""))
                    status_idx = 0 if row.get("status") == "active" else 1
                    new_status = st.selectbox("Status", ["active", "inactive"], index=status_idx)
                    _BIZ_TYPES_E   = ["Office", "Service", "Merchandiser", "Manufacturer"]
                    cur_biz        = str(row.get("business_type", "Office") or "Office")
                    biz_idx        = _BIZ_TYPES_E.index(cur_biz) if cur_biz in _BIZ_TYPES_E else 0
                    new_biz_type   = st.selectbox("Business Type", _BIZ_TYPES_E, index=biz_idx)
                    save_edit  = st.form_submit_button("💾 Save Changes")

                    if save_edit:
                        try:
                            normalized_org_phone, org_phone_error = get_phone_uniqueness_error(conn, new_phone, exclude_organization=org_sel)
                            name_conflict = safe_read(
                                "SELECT id FROM organizations WHERE lower(trim(name)) = lower(trim(?)) AND id<>?",
                                conn,
                                params=(new_name.strip(), int(row["id"])),
                            )
                            if org_phone_error:
                                st.error(org_phone_error)
                            elif not name_conflict.empty:
                                st.error(f"Organization '{new_name.strip()}' already exists.")
                            else:
                                execute_write(conn, """
                                    UPDATE organizations SET name=?, phone=?, email=?, location=?, status=?, business_type=?
                                    WHERE id=?
                                """, (new_name.strip(), normalized_org_phone, new_email.strip(), new_loc.strip(), new_status, new_biz_type, int(row["id"])))
                                if new_name.strip() != org_sel:
                                    for tbl in ["users", "branches", "attendance", "ratings",
                                                "leaves", "warnings", "messages", "kiosks",
                                                "payments", "schedules"]:
                                        try:
                                            execute_write(
                                                conn,
                                                f"UPDATE {tbl} SET organization=? WHERE organization=?",
                                                (new_name.strip(), org_sel)
                                            )
                                        except Exception:
                                            pass
                                execute_write(
                                    conn,
                                    "UPDATE branches SET status=? WHERE organization=?",
                                    (new_status, new_name.strip())
                                )
                                conn.commit()
                                st.success("✅ Organization updated successfully!")
                                st.rerun()
                        except Exception as e:
                            st.error(str(e))

        with tab_delete:
            df_d = safe_read("SELECT name FROM organizations", conn)
            if df_d.empty:
                st.info("No organizations to delete.")
            else:
                del_org = st.selectbox("Select Organization to Delete", df_d["name"].tolist(), key="del_org_sel")
                st.warning(
                    f"⚠️ This will **permanently delete** '{del_org}' and ALL its data — "
                    "users, branches, attendance, ratings, payments, etc. This cannot be undone."
                )
                confirm_text = st.text_input(f"Type **{del_org}** exactly to confirm deletion", key="del_confirm")

                if st.button("🗑️ Delete Organization", type="primary"):
                    if confirm_text.strip() == del_org:
                        try:
                            for tbl in ["users", "branches", "attendance", "ratings",
                                        "leaves", "warnings", "messages", "kiosks",
                                        "payments", "schedules"]:
                                try:
                                    execute_write(conn, f"DELETE FROM {tbl} WHERE organization=?", (del_org,))
                                except Exception:
                                    pass
                            execute_write(conn, "DELETE FROM organizations WHERE name=?", (del_org,))
                            conn.commit()
                            st.success(f"✅ '{del_org}' and all its data have been deleted.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
                    else:
                        st.error("Confirmation text does not match. Deletion cancelled.")

        with tab_passwd:
            df_p = safe_read("SELECT name FROM organizations", conn)
            if df_p.empty:
                st.info("No organizations.")
            else:
                pw_org = st.selectbox("Select Organization", df_p["name"].tolist(), key="pw_org_sel")
                with st.form("reset_org_pw", clear_on_submit=False):
                    new_pw  = st.text_input("New Super Admin Password", type="password")
                    conf_pw = st.text_input("Confirm Password",         type="password")
                    sub_pw  = st.form_submit_button("🔑 Reset Password")

                    if sub_pw:
                        if not new_pw:
                            st.error("Password cannot be empty.")
                        elif new_pw != conf_pw:
                            st.error("Passwords do not match.")
                        else:
                            try:
                                execute_write(
                                    conn,
                                    "UPDATE users SET password=? WHERE organization=? AND role='superadmin'",
                                    (hash_password(new_pw), pw_org)
                                )
                                conn.commit()
                                st.success(f"✅ Super admin password for '{pw_org}' has been reset.")
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))

    # ==========================================================
    # PAYMENTS
    # ==========================================================
    elif menu == "💰 Payments":

        st.subheader("💰 Payments")

        cfg = load_cfg(conn)

        tab_hist, tab_req, tab_manual, tab_price, tab_shutoff = st.tabs([
            "📋 History",
            "📲 Request Payment",
            "✏️ Manual Record",
            "💲 Pricing",
            "🔒 Auto-Shutoff",
        ])

        with tab_hist:
            df_pay = safe_read("SELECT * FROM payments ORDER BY created_at DESC", conn)
            if df_pay.empty:
                st.info("No payment records yet.")
            else:
                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    date_from = st.date_input("From", value=datetime.now().replace(day=1).date(), key="pay_from")
                with col_f2:
                    date_to = st.date_input("To", value=datetime.now().date(), key="pay_to")

                try:
                    df_pay["_dt"] = pd.to_datetime(df_pay["created_at"], errors="coerce")
                    mask = (df_pay["_dt"].dt.date >= date_from) & (df_pay["_dt"].dt.date <= date_to)
                    df_filtered = df_pay[mask].drop(columns=["_dt"], errors="ignore")
                except Exception:
                    df_filtered = df_pay

                cm1, cm2 = st.columns(2)
                total_amt = df_filtered["amount"].sum() if not df_filtered.empty else 0
                cm1.metric("💰 Total Collected (Filtered)", f"KES {total_amt:,.0f}")
                cm2.metric("Transactions", len(df_filtered))

                st.dataframe(df_filtered, use_container_width=True)

                if not df_filtered.empty:
                    st.divider()
                    st.markdown("**Per-Organization Totals**")
                    per_org = df_filtered.groupby("organization")["amount"].sum().reset_index()
                    per_org.columns = ["Organization", "Total (KES)"]
                    per_org = per_org.sort_values("Total (KES)", ascending=False)
                    st.dataframe(per_org, use_container_width=True)

        with tab_req:
            orgs_req = safe_read("SELECT name, phone FROM organizations ORDER BY name", conn)
            if orgs_req.empty:
                st.info("No organizations found.")
            else:
                req_org = st.selectbox("Select Organization", orgs_req["name"].tolist(), key="req_org")
                b_cnt_r = safe_read(
                    "SELECT COUNT(*) as cnt FROM branches WHERE organization=?",
                    conn, params=(req_org,)
                )
                b_count  = max(int(b_cnt_r.iloc[0]["cnt"]) if not b_cnt_r.empty else 1, 1)
                u_cnt_r  = safe_read(
                    "SELECT COUNT(*) as cnt FROM users WHERE organization=? AND role != 'master'",
                    conn, params=(req_org,)
                )
                u_count = int(u_cnt_r.iloc[0]["cnt"]) if not u_cnt_r.empty else 0
                price   = calc_price(b_count, cfg)

                ci1, ci2, ci3 = st.columns(3)
                ci1.info(f"**Branches:** {b_count}")
                ci2.info(f"**Users:** {u_count}")
                ci3.success(f"**Amount Due: KES {price:,}**")

                st.divider()
                st.markdown("### 💳 Payment Details")

                pc1, pc2, pc3 = st.columns(3)
                with pc1:
                    paybill_val = cfg.get("paybill") or "Not configured"
                    st.markdown(f"**📱 Paybill Number**\n\n### `{paybill_val}`")
                with pc2:
                    till_val = cfg.get("till_number") or "Not configured"
                    st.markdown(f"**🏪 Till Number**\n\n### `{till_val}`")
                with pc3:
                    bank_parts = [
                        cfg.get("bank_name", "") or "",
                        cfg.get("bank_account", "") or "",
                        cfg.get("bank_branch", "") or "",
                    ]
                    bank_str = " / ".join(p for p in bank_parts if p) or "Not configured"
                    st.markdown(f"**🏦 Bank Account**\n\n`{bank_str}`")

                st.divider()
                st.markdown("### 📲 Send M-Pesa STK Push")
                org_phone = orgs_req[orgs_req["name"] == req_org]["phone"].values[0]
                stk_phone = st.text_input(
                    "Client Phone (254...)",
                    value=str(org_phone) if org_phone else "",
                    key="stk_phone"
                )
                if st.button("📲 Send M-Pesa Payment Prompt", key="stk_btn"):
                    if stk_push:
                        result = stk_push(stk_phone, price)
                        if result and "error" in str(result).lower():
                            st.error(f"STK Push failed: {result}")
                        else:
                            st.success(f"✅ Payment prompt of KES {price:,} sent to {stk_phone}")
                    else:
                        st.warning("M-Pesa STK Push is not configured. Update credentials in payments/mpesa.py.")

        with tab_manual:
            orgs_man = safe_read("SELECT name FROM organizations ORDER BY name", conn)
            if orgs_man.empty:
                st.info("No organizations.")
            else:
                man_org   = st.selectbox("Select Organization", orgs_man["name"].tolist(), key="man_org")
                b_cnt_m   = safe_read(
                    "SELECT COUNT(*) as cnt FROM branches WHERE organization=?",
                    conn, params=(man_org,)
                )
                b_count_m = max(int(b_cnt_m.iloc[0]["cnt"]) if not b_cnt_m.empty else 1, 1)
                suggested = calc_price(b_count_m, cfg)

                with st.form("manual_payment", clear_on_submit=False):
                    man_amount = st.number_input("Amount (KES)", min_value=0, value=suggested, step=100)
                    man_method = st.selectbox("Payment Method", ["M-Pesa Manual", "Bank Transfer", "Cash", "Waiver"])
                    man_phone  = st.text_input("Phone (optional)")
                    man_sub    = st.form_submit_button("✅ Record Payment & Activate Org")

                    if man_sub:
                        try:
                            new_expiry = datetime.now() + timedelta(days=30)
                            execute_write(conn, """
                                INSERT INTO payments(organization, amount, method, phone, created_at)
                                VALUES (?,?,?,?,?)
                            """, (man_org, man_amount, man_method, man_phone, str(datetime.now())))
                            execute_write(conn, """
                                UPDATE organizations SET status='active', expires_at=? WHERE name=?
                            """, (str(new_expiry), man_org))
                            execute_write(conn, "UPDATE branches SET status='active' WHERE organization=?", (man_org,))
                            conn.commit()
                            st.success(f"✅ KES {man_amount:,} recorded. '{man_org}' is active for 30 more days!")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

        with tab_price:
            st.markdown("### 💲 Subscription Pricing")
            st.info("1 branch = flat rate. 2+ branches = per-branch rate × number of branches.")

            with st.form("pricing_form", clear_on_submit=False):
                p1 = st.number_input("Single Branch Org – KES / month",
                                     min_value=0, value=int(cfg.get("price_single_branch", 1000)), step=100)
                p2 = st.number_input("Multi-Branch Org – KES / branch / month",
                                     min_value=0, value=int(cfg.get("price_per_branch", 800)), step=100)
                st.divider()
                st.markdown("### 💳 Payment Details (shown to clients)")
                paybill = st.text_input("Paybill Number",     value=str(cfg.get("paybill",      "") or ""))
                till    = st.text_input("Till Number",         value=str(cfg.get("till_number",  "") or ""))
                bk_name = st.text_input("Bank Name",           value=str(cfg.get("bank_name",    "") or ""))
                bk_acc  = st.text_input("Bank Account Number", value=str(cfg.get("bank_account", "") or ""))
                bk_br   = st.text_input("Bank Branch",         value=str(cfg.get("bank_branch",  "") or ""))
                save_cfg = st.form_submit_button("💾 Save Pricing & Payment Config")

                if save_cfg:
                    try:
                        execute_write(conn, """
                            UPDATE payment_config
                            SET paybill=?, till_number=?, bank_name=?, bank_account=?,
                                bank_branch=?, price_single_branch=?, price_per_branch=?
                            WHERE id=1
                        """, (paybill, till, bk_name, bk_acc, bk_br, p1, p2))
                        conn.commit()
                        st.success("✅ Pricing and payment details saved!")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

        with tab_shutoff:
            st.markdown("### 🔒 Subscription Status Monitor")
            orgs_sh = safe_read("SELECT * FROM organizations ORDER BY name", conn)

            if orgs_sh.empty:
                st.info("No organizations.")
            else:
                now = datetime.now()
                expired_list  = []
                expiring_soon = []
                healthy_list  = []

                for _, row in orgs_sh.iterrows():
                    try:
                        exp_dt = datetime.strptime(str(row["expires_at"])[:19], "%Y-%m-%d %H:%M:%S")
                        delta  = (exp_dt - now).days
                        entry  = {**row.to_dict(), "days_left": delta}
                        if delta < 0 or row["status"] != "active":
                            expired_list.append(entry)
                        elif delta <= 7:
                            expiring_soon.append(entry)
                        else:
                            healthy_list.append(entry)
                    except Exception:
                        expired_list.append({**row.to_dict(), "days_left": "?"})

                sa, sb, sc = st.columns(3)
                sa.success(f"✅ Healthy: {len(healthy_list)}")
                sb.warning(f"⚠️ Expiring Soon: {len(expiring_soon)}")
                sc.error(f"🔴 Expired / Inactive: {len(expired_list)}")
                st.divider()

                def org_shutoff_row(entry, key_prefix):
                    c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
                    days = entry.get("days_left")
                    if isinstance(days, int) and days >= 0:
                        days_label = f"{days} day(s) left"
                    elif isinstance(days, int):
                        days_label = f"expired {abs(days)} day(s) ago"
                    else:
                        days_label = "unknown"
                    c1.write(f"**{entry['name']}** — {days_label}")
                    c2.write(f"Status: `{entry.get('status', '')}`")
                    if c3.button("🔄 Extend 30d", key=f"{key_prefix}_ext_{entry['name']}"):
                        new_exp = datetime.now() + timedelta(days=30)
                        execute_write(conn, "UPDATE organizations SET status='active', expires_at=? WHERE name=?",
                                     (str(new_exp), entry["name"]))
                        execute_write(conn, "UPDATE branches SET status='active' WHERE organization=?", (entry["name"],))
                        conn.commit()
                        st.rerun()
                    if c4.button("🔴 Deactivate", key=f"{key_prefix}_deact_{entry['name']}"):
                        execute_write(conn, "UPDATE organizations SET status='disabled' WHERE name=?", (entry["name"],))
                        execute_write(conn, "UPDATE branches SET status='inactive' WHERE organization=?", (entry["name"],))
                        execute_write(
                            conn,
                            "UPDATE user_sessions SET active=0 WHERE username IN (SELECT username FROM users WHERE organization=?)",
                            (entry["name"],),
                        )
                        conn.commit()
                        st.rerun()

                if expiring_soon:
                    st.warning(f"⚠️ Expiring within 7 days ({len(expiring_soon)})")
                    for e in expiring_soon:
                        org_shutoff_row(e, "soon")

                if expired_list:
                    st.error(f"🔴 Expired / Inactive ({len(expired_list)})")
                    for e in expired_list:
                        org_shutoff_row(e, "exp")

                if healthy_list:
                    with st.expander(f"✅ Healthy Organizations ({len(healthy_list)})"):
                        for e in healthy_list:
                            org_shutoff_row(e, "hlth")

    # ==========================================================
    # BRANCHES
    # ==========================================================
    elif menu == "🌿 Branches":

        st.subheader("🌿 All Branches")

        def sync_org_status_from_branches(org_name):
            active_df = safe_read(
                "SELECT COUNT(*) AS cnt FROM branches WHERE organization=? AND status='active'",
                conn,
                params=(org_name,)
            )
            active_cnt = int(active_df.iloc[0]["cnt"]) if not active_df.empty else 0

            org_df = safe_read(
                "SELECT status, expires_at FROM organizations WHERE name=? LIMIT 1",
                conn,
                params=(org_name,),
            )
            current_status = str(org_df.iloc[0].get("status", "active") if not org_df.empty else "active").strip().lower()
            expires_at = pd.to_datetime(org_df.iloc[0].get("expires_at"), errors="coerce") if not org_df.empty else pd.NaT

            if pd.notna(expires_at) and expires_at.to_pydatetime() < datetime.now():
                new_status = "suspended"
            elif current_status in {"disabled", "suspended"}:
                new_status = current_status
            else:
                new_status = "active" if active_cnt > 0 else "inactive"

            execute_write(conn, "UPDATE organizations SET status=? WHERE name=?", (new_status, org_name))
            conn.commit()

        branches = safe_read("""
            SELECT b.id, b.name, b.organization, b.status
            FROM branches b
            ORDER BY b.organization, b.name
        """, conn)
        users_b = safe_read("SELECT branch, organization FROM users WHERE role != 'master'", conn)

        if branches.empty:
            st.info("No branches registered yet.")
        else:
            rows = []
            for _, row in branches.iterrows():
                b_name = row["name"]
                b_org  = row.get("organization", "")
                b_stat = row.get("status", "active")
                u_cnt  = 0
                if not users_b.empty:
                    u_cnt = len(users_b[(users_b["branch"] == b_name) & (users_b["organization"] == b_org)])
                rows.append({
                    "ID":           row["id"],
                    "Branch":       b_name,
                    "Organization": b_org,
                    "Status":       b_stat,
                    "Users":        u_cnt,
                })
            df_br = pd.DataFrame(rows)

            # Optional manual branch status toggle (single action panel)
            st.markdown("### Manual Branch Status")
            manual_org = st.selectbox(
                "Organization (Manual Toggle)",
                sorted(df_br["Organization"].dropna().unique().tolist()),
                key="manual_branch_org"
            )

            org_subset = df_br[df_br["Organization"] == manual_org].copy()

            if org_subset.empty:
                st.info("No branches found for selected organization.")
            else:
                manual_branch = st.selectbox(
                    "Branch",
                    org_subset["Branch"].tolist(),
                    key="manual_branch_name"
                )
                manual_action = st.selectbox(
                    "Action",
                    ["Activate", "Deactivate"],
                    key="manual_branch_action"
                )

                if st.button("Apply Branch Status", key="manual_branch_apply"):
                    selected = org_subset[org_subset["Branch"] == manual_branch]
                    if selected.empty:
                        st.error("Selected branch not found.")
                    else:
                        b_id = int(selected.iloc[0]["ID"])
                        new_status = "active" if manual_action == "Activate" else "inactive"
                        org_state_df = safe_read("SELECT status FROM organizations WHERE name=? LIMIT 1", conn, params=(manual_org,))
                        org_state = str(org_state_df.iloc[0].get("status", "active") if not org_state_df.empty else "active").strip().lower()
                        if new_status == "active" and org_state in {"disabled", "suspended"}:
                            st.warning(f"{manual_org} is currently {org_state}. Activate the organization first before enabling its branches.")
                        else:
                            execute_write(conn, "UPDATE branches SET status=? WHERE id=?", (new_status, b_id))
                            if new_status != "active":
                                execute_write(
                                    conn,
                                    "UPDATE user_sessions SET active=0 WHERE username IN (SELECT username FROM users WHERE organization=? AND branch=?)",
                                    (manual_org, manual_branch),
                                )
                            sync_org_status_from_branches(manual_org)
                            conn.commit()
                            st.success(f"{manual_branch} set to {new_status}.")
                            st.rerun()

            st.markdown("### Organization Access Control")
            control_org = st.selectbox(
                "Organization (Entire Org)",
                sorted(df_br["Organization"].dropna().unique().tolist()),
                key="manual_org_control_name"
            )
            control_action = st.selectbox(
                "Organization Action",
                ["Activate Entire Organization", "Disable Entire Organization"],
                key="manual_org_control_action"
            )

            if st.button("Apply Organization Status", key="manual_org_apply"):
                if control_action == "Activate Entire Organization":
                    execute_write(conn, "UPDATE organizations SET status='active' WHERE name=?", (control_org,))
                    execute_write(conn, "UPDATE branches SET status='active' WHERE organization=?", (control_org,))
                    conn.commit()
                    st.success(f"{control_org} is now active and all its branches are active.")
                else:
                    execute_write(conn, "UPDATE organizations SET status='disabled' WHERE name=?", (control_org,))
                    execute_write(conn, "UPDATE branches SET status='inactive' WHERE organization=?", (control_org,))
                    execute_write(
                        conn,
                        "UPDATE user_sessions SET active=0 WHERE username IN (SELECT username FROM users WHERE organization=?)",
                        (control_org,),
                    )
                    conn.commit()
                    st.warning(f"{control_org} has been disabled together with all its branches.")
                st.rerun()

            for org_name in df_br["Organization"].unique():
                org_branches = df_br[df_br["Organization"] == org_name]
                active_cnt   = len(org_branches[org_branches["Status"] == "active"])
                st.markdown(
                    f"### 🌿 {org_name} &nbsp;&nbsp; "
                    f"<span style='font-size:0.85em;color:gray;'>{active_cnt}/{len(org_branches)} branches active</span>",
                    unsafe_allow_html=True
                )
                for _, br_row in org_branches.iterrows():
                    icon  = "🟢" if br_row["Status"] == "active" else "🔴"
                    b_id  = int(br_row["ID"])
                    col1, col2, col3 = st.columns([4, 2, 2])
                    col1.write(f"{icon} **{br_row['Branch']}** — {br_row['Users']} user(s)")
                    col2.write(f"`{br_row['Status']}`")
                    if br_row["Status"] == "active":
                        if col3.button("Deactivate", key=f"br_deact_{b_id}"):
                            execute_write(conn, "UPDATE branches SET status='inactive' WHERE id=?", (b_id,))
                            execute_write(
                                conn,
                                "UPDATE user_sessions SET active=0 WHERE username IN (SELECT username FROM users WHERE organization=? AND branch=?)",
                                (br_row["Organization"], br_row["Branch"]),
                            )
                            sync_org_status_from_branches(br_row["Organization"])
                            conn.commit()
                            st.rerun()
                    else:
                        if col3.button("Activate", key=f"br_act_{b_id}"):
                            org_state_df = safe_read("SELECT status FROM organizations WHERE name=? LIMIT 1", conn, params=(br_row["Organization"],))
                            org_state = str(org_state_df.iloc[0].get("status", "active") if not org_state_df.empty else "active").strip().lower()
                            if org_state in {"disabled", "suspended"}:
                                st.warning(f"{br_row['Organization']} is currently {org_state}. Activate the organization first.")
                            else:
                                execute_write(conn, "UPDATE branches SET status='active' WHERE id=?", (b_id,))
                                sync_org_status_from_branches(br_row["Organization"])
                                conn.commit()
                                st.rerun()

                st.divider()

    # ==========================================================
    # EMPLOYEES
    # ==========================================================
    elif menu == "👥 Employees":

        st.subheader("👥 Employees & Users")

        users_all = safe_read("""
            SELECT username, role, phone, gender, branch, organization, status
            FROM users
            WHERE role != 'master'
            ORDER BY organization, branch, role, username
        """, conn)

        if users_all.empty:
            st.info("No users in the system yet.")
        else:
            search = st.text_input("🔍 Search Username", placeholder="Type to search...")
            org_opts = ["All"] + sorted(users_all["organization"].dropna().unique().tolist())
            org_filter = nav_selectbox("Organization", org_opts, key="emp_org")
            branch_opts = ["All"] + sorted(users_all["branch"].dropna().unique().tolist())
            branch_filter = nav_selectbox("Branch", branch_opts, key="emp_branch")
            role_filter = nav_selectbox("Role", ["All", "superadmin", "admin", "employee", "kiosk"], key="emp_role")

            df_emp = users_all.copy()
            if search:
                df_emp = df_emp[df_emp["username"].str.contains(search, case=False, na=False)]
            if org_filter != "All":
                df_emp = df_emp[df_emp["organization"] == org_filter]
            if branch_filter != "All":
                df_emp = df_emp[df_emp["branch"] == branch_filter]
            if role_filter != "All":
                df_emp = df_emp[df_emp["role"] == role_filter]

            st.markdown(f"**{len(df_emp)} record(s) found**")
            st.dataframe(df_emp, use_container_width=True)

            st.download_button(
                "Export Filtered Users (CSV)",
                data=df_emp.to_csv(index=False),
                file_name="users_filtered_export.csv",
                mime="text/csv",
            )

    # ==========================================================
    # ANALYTICS
    # ==========================================================
    elif menu == "📈 Analytics":

        st.subheader("📈 Organization Performance Intelligence")

        tab_sys, tab_org, tab_ai = st.tabs(["🌐 System Overview", "🏢 Organization Performance", "🤖 Chief Administrator Advisory"])

        with tab_sys:
            ratings_all    = safe_read("SELECT * FROM ratings",    conn)
            attendance_all = safe_read("SELECT * FROM attendance", conn)
            payments_all   = safe_read("SELECT * FROM payments",   conn)
            orgs_a         = safe_read("SELECT * FROM organizations", conn)

            ca, cb, cc = st.columns(3)
            ca.metric("Total Ratings",            len(ratings_all))
            cb.metric("Total Attendance Records", len(attendance_all))
            cc.metric("Total Payments Recorded",  len(payments_all))

            if not ratings_all.empty:
                st.divider()
                st.markdown("**🏆 Top 5 Organizations by Avg Performance Score**")
                org_scores = ratings_all.groupby("organization")["score"].mean().reset_index()
                org_scores.columns = ["Organization", "Avg Score"]
                org_scores = org_scores.sort_values("Avg Score", ascending=False).head(5)
                if PLOTLY_AVAILABLE:
                    try:
                        fig = px.bar(org_scores, x="Organization", y="Avg Score",
                                     color="Avg Score", color_continuous_scale="Blues",
                                     title="Top 5 Org Performance")
                        st.plotly_chart(fig, use_container_width=True)
                    except Exception:
                        st.dataframe(org_scores)
                else:
                    st.dataframe(org_scores)

            if not payments_all.empty:
                st.divider()
                st.markdown("**📅 Monthly Revenue**")
                try:
                    payments_all["_dt"] = pd.to_datetime(payments_all["created_at"], errors="coerce")
                    pay_chart = (
                        payments_all.groupby(payments_all["_dt"].dt.to_period("M").astype(str))["amount"]
                        .sum().reset_index()
                    )
                    pay_chart.columns = ["Month", "Total KES"]
                    if PLOTLY_AVAILABLE:
                        fig2 = px.bar(pay_chart, x="Month", y="Total KES", title="Monthly Revenue")
                        st.plotly_chart(fig2, use_container_width=True)
                    else:
                        st.dataframe(pay_chart)
                except Exception:
                    pass

            if not orgs_a.empty:
                st.divider()
                st.markdown("**⚠️ Organizations with Zero Activity (Last 30 Days)**")
                cutoff = datetime.now() - timedelta(days=30)
                activity_orgs = set()
                if not ratings_all.empty and "created_at" in ratings_all.columns:
                    try:
                        r_recent = ratings_all[pd.to_datetime(ratings_all["created_at"], errors="coerce") >= cutoff]
                        activity_orgs.update(r_recent["organization"].dropna().unique())
                    except Exception:
                        pass
                if not attendance_all.empty and "date" in attendance_all.columns:
                    try:
                        a_recent = attendance_all[pd.to_datetime(attendance_all["date"], errors="coerce") >= cutoff]
                        activity_orgs.update(a_recent["organization"].dropna().unique())
                    except Exception:
                        pass
                inactive_activity = orgs_a[~orgs_a["name"].isin(activity_orgs)]
                if inactive_activity.empty:
                    st.success("✅ All organizations have recent activity.")
                else:
                    st.dataframe(inactive_activity[["name", "status", "expires_at"]], use_container_width=True)

        with tab_org:
            orgs_d = safe_read("SELECT name FROM organizations ORDER BY name", conn)
            if orgs_d.empty:
                st.info("No organizations yet.")
            else:
                sel_org = st.selectbox("Select Organization to Analyse", orgs_d["name"].tolist(), key="adive_org")

                ratings_o = safe_read("SELECT * FROM ratings WHERE organization=?", conn, params=(sel_org,))
                attendance_o = safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(sel_org,))
                users_o = safe_read("SELECT * FROM users WHERE organization=?", conn, params=(sel_org,))
                leaves_o = safe_read("SELECT * FROM leaves WHERE organization=?", conn, params=(sel_org,))
                warnings_o = safe_read("SELECT * FROM warnings WHERE organization=?", conn, params=(sel_org,))
                messages_o = safe_read("SELECT * FROM messages WHERE organization=?", conn, params=(sel_org,))
                branches_o = safe_read("SELECT name, status FROM branches WHERE organization=? ORDER BY name", conn, params=(sel_org,))
                intel = generate_org_ai_intelligence(conn, sel_org)
                branch_view = build_branch_performance_table(ratings_o, attendance_o, users_o, branches_o)
                branch_diag = build_branch_diagnostic_table(
                    conn,
                    sel_org,
                    intel.get("business_type", "Office"),
                    ratings_o,
                    attendance_o,
                    users_o,
                    leaves_o,
                    warnings_o,
                    messages_o,
                    branches_o,
                )

                ops_block = intel.get("operations", {})
                perf_block = intel.get("performance", {})
                finance_block = intel.get("finance", {})
                playbook = intel.get("playbook", {})
                benchmark_notes = intel.get("benchmark_notes", [])
                benchmark_examples_df = pd.DataFrame(intel.get("benchmark_examples", []))
                people_signals = intel.get("people_signals", {})
                live_data = get_cached_recommendations(business_type=playbook.get("feed_type", "Office"))

                st.caption(
                    f"Business Type: {intel.get('business_type', 'Office')} | Recommendations combine internal org/branch benchmarks with live industry guidance."
                )

                qc1, qc2, qc3, qc4 = st.columns(4)
                qc1.metric("Branches", ops_block.get("branches_total", 0))
                qc2.metric("Active Branches", f"{ops_block.get('branches_active', 0)}/{ops_block.get('branches_total', 0)}")
                qc3.metric("Avg Org Score", f"{perf_block.get('avg_score', 0):.1f}")
                qc4.metric("Health Status", intel.get("org_health_status", "Unknown"))

                qd1, qd2, qd3, qd4 = st.columns(4)
                qd1.metric("Strongest Branch", perf_block.get("strongest_branch", "-"))
                qd2.metric("Weakest Branch", perf_block.get("weakest_branch", "-"))
                qd3.metric("True Late Rate", f"{ops_block.get('late_rate_pct', 0):.1f}%")
                qd4.metric("Payment Trend", str(finance_block.get("payment_trend", "stable")).title())

                flow = build_priority_action_flow(intel, branch_diag)

                st.markdown("### 🔄 Improvement Flow")
                fl1, fl2, fl3, fl4 = st.columns(4)
                fl1.markdown("**1. Main Problem**")
                fl1.write(flow.get("problem", "-"))
                fl2.markdown("**2. Likely Causes**")
                for cause in flow.get("causes", [])[:3]:
                    fl2.write(f"- {cause}")
                fl3.markdown("**3. Best Branch to Copy**")
                fl3.write(flow.get("learning_partner", "-"))
                fl3.caption(flow.get("learning_reason", ""))
                fl4.markdown("**4. Top Actions**")
                for action in flow.get("urgent_actions", [])[:3]:
                    fl4.write(f"- {action}")

                st.markdown("### 🌿 Branch Health Summary")
                if branch_view.empty:
                    st.info(f"No branch-level data yet for '{sel_org}'.")
                else:
                    st.dataframe(branch_view, use_container_width=True)
                    if PLOTLY_AVAILABLE and "Avg Score" in branch_view.columns:
                        try:
                            chart_df = branch_view.copy()
                            fig = px.bar(
                                chart_df,
                                x="Branch",
                                y="Avg Score",
                                color="Branch Health",
                                title=f"{sel_org} Branch Performance"
                            )
                            st.plotly_chart(fig, use_container_width=True)
                        except Exception:
                            pass

                    needs_attention = branch_view[
                        branch_view["Branch Health"].astype(str).isin(["Needs Support", "Inactive", "No Data"])
                    ].copy()
                    if not needs_attention.empty:
                        st.markdown("### ⚠️ Branches Requiring Attention")
                        st.dataframe(needs_attention, use_container_width=True)

                rx, ry = st.columns(2)
                with rx:
                    st.markdown("### ⚠️ Organization Risk Summary")
                    risks = intel.get("risk_flags", [])
                    if risks:
                        for risk in risks:
                            st.warning(risk)
                    else:
                        st.success("No major organization-level risks flagged.")

                with ry:
                    st.markdown("### 🚀 Growth & Improvement Opportunities")
                    growth = intel.get("growth_moves", [])
                    if growth:
                        for move in growth:
                            st.success(move)
                    else:
                        st.info("No special growth opportunities surfaced yet.")

                st.markdown("### 🚨 Top 3 Urgent Actions")
                urgent_actions = []
                for item in intel.get("advice", []):
                    tag = str(item).upper()
                    if any(marker in tag for marker in ["ACTION:", "REMOVE:", "IMPROVE:", "ADD:", "BENCHMARK:"]):
                        urgent_actions.append(item)
                urgent_actions = urgent_actions[:3]
                if urgent_actions:
                    for idx, item in enumerate(urgent_actions, start=1):
                        st.warning(f"{idx}. {item}")
                else:
                    st.success("No urgent action is currently required.")

                st.markdown("### ✅ Recommended Organization Actions")
                advice = intel.get("advice", [])
                if advice:
                    for item in advice:
                        st.write(f"- {item}")
                else:
                    st.info("No organization-level recommendations yet.")

                st.markdown("### 🩺 Why This Organization or Its Branches May Be Underperforming")
                root_causes = intel.get("root_causes", [])
                if root_causes:
                    for cause in root_causes:
                        st.error(cause)
                else:
                    st.success("No major root-cause issues flagged yet.")

                if not branch_diag.empty:
                    st.markdown("### 🧭 Branch Diagnosis & Learning Partners")
                    st.dataframe(branch_diag, use_container_width=True)

                bx, by = st.columns(2)
                with bx:
                    st.markdown("### 🧠 What Strong Organizations Are Doing")
                    if benchmark_notes:
                        for note in benchmark_notes:
                            st.info(note)
                    else:
                        st.info("Peer benchmark notes will appear as more organization data grows.")

                    if not benchmark_examples_df.empty:
                        st.dataframe(benchmark_examples_df, use_container_width=True)

                with by:
                    st.markdown("### 🛠️ Add / Remove / Improve / Research Cases")
                    for label, items in [
                        ("Add", playbook.get("introduce", [])),
                        ("Remove", playbook.get("remove", [])),
                        ("Improve", playbook.get("improve", [])),
                        ("Research", playbook.get("research", [])),
                    ]:
                        if items:
                            st.markdown(f"**{label}**")
                            for item in items:
                                st.write(f"- {item}")

                st.markdown("### 🚨 Management, Bias & Relationship Signals")
                signal_sections = [
                    ("Management Pressure", people_signals.get("management_pressure", [])),
                    ("Favoritism / Bias", people_signals.get("favoritism", [])),
                    ("Peer Gang-Ups", people_signals.get("peer_gangups", [])),
                    ("Isolation", people_signals.get("isolation", [])),
                    ("Relationships / Conflict", people_signals.get("relationships", [])),
                ]
                shown_signal = False
                for label, items in signal_sections:
                    if items:
                        shown_signal = True
                        st.markdown(f"**{label}**")
                        for item in items[:4]:
                            st.write(f"- {item}")
                if not shown_signal:
                    st.success("No major people-risk signals detected at the moment.")

                st.markdown("### 🌐 Live Industry Advice")
                st.caption(
                    f"Tailored for {intel.get('business_type', 'Office')} organizations using live public feeds like SHRM, HR Dive, HBR, MIT Sloan and sector-specific sources."
                )
                if live_data.get("error"):
                    st.warning(live_data.get("error"))
                else:
                    st.success(
                        f"Live sources responding: {live_data.get('sources_ok', 0)} | Last refresh: {live_data.get('fetched_at', 'recently')}"
                    )
                    for article in live_data.get("articles", [])[:5]:
                        st.markdown(
                            f"- **{article.get('title', 'Untitled')}** ({article.get('source', 'Source')} - {article.get('category', 'Advice')})"
                        )
                        if article.get("summary"):
                            st.caption(article.get("summary"))

        with tab_ai:
            st.markdown("### 🤖 Chief Administrator Advisory Intelligence")
            st.caption("Organization-level guidance based on branch performance, operational risk, and growth opportunity.")

            benchmark_df, key_alerts = build_master_advisor_benchmark(conn)

            if benchmark_df.empty:
                st.info("No organizations available for AI advisory.")
            else:
                b1, b2, b3 = st.columns(3)
                b1.metric("Organizations Tracked", len(benchmark_df))
                b2.metric("Avg Health Score", f"{benchmark_df['Health Score'].mean():.1f}")
                b3.metric("Needs Intervention", int((benchmark_df["Health Status"] == "Needs Intervention").sum()))

                st.markdown("**Cross-Organization Performance Benchmark**")
                st.dataframe(benchmark_df, use_container_width=True)

                st.markdown("**Top Organizations to Learn From**")
                top_orgs = benchmark_df[[
                    "Organization", "Business Type", "Health Score", "Health Status", "Avg Score", "True Late %", "Payment Trend"
                ]].head(5)
                st.dataframe(top_orgs, use_container_width=True)

                turnaround_cases = benchmark_df[benchmark_df["Health Status"].astype(str) != "Strong"].copy()
                if not turnaround_cases.empty:
                    st.markdown("**Organizations Needing Turnaround Support**")
                    st.dataframe(
                        turnaround_cases[[
                            "Organization", "Business Type", "Health Score", "Primary Root Cause", "True Late %", "Payment Trend"
                        ]].sort_values("Health Score", ascending=True),
                        use_container_width=True,
                    )

                if key_alerts:
                    st.markdown("**Priority Organization Alerts**")
                    for alert in key_alerts:
                        st.warning(alert)

                st.divider()
                selected_org = st.selectbox(
                    "Select Organization for Advisory Review",
                    benchmark_df["Organization"].tolist(),
                    key="master_ai_org",
                )
                intel = generate_org_ai_intelligence(conn, selected_org)
                ratings_sel = safe_read("SELECT * FROM ratings WHERE organization=?", conn, params=(selected_org,))
                attendance_sel = safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(selected_org,))
                users_sel = safe_read("SELECT * FROM users WHERE organization=?", conn, params=(selected_org,))
                leaves_sel = safe_read("SELECT * FROM leaves WHERE organization=?", conn, params=(selected_org,))
                warnings_sel = safe_read("SELECT * FROM warnings WHERE organization=?", conn, params=(selected_org,))
                messages_sel = safe_read("SELECT * FROM messages WHERE organization=?", conn, params=(selected_org,))
                branches_sel = safe_read("SELECT name, status FROM branches WHERE organization=? ORDER BY name", conn, params=(selected_org,))
                branch_diag = build_branch_diagnostic_table(
                    conn,
                    selected_org,
                    intel.get("business_type", "Office"),
                    ratings_sel,
                    attendance_sel,
                    users_sel,
                    leaves_sel,
                    warnings_sel,
                    messages_sel,
                    branches_sel,
                )
                flow = build_priority_action_flow(intel, branch_diag)

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Health Score", f"{intel.get('org_health_score', 0):.1f}")
                m2.metric("Health Status", intel.get("org_health_status", "Unknown"))
                m3.metric("Avg Performance", f"{intel.get('performance', {}).get('avg_score', 0):.1f}")
                m4.metric("True Late Rate", f"{intel.get('operations', {}).get('late_rate_pct', 0):.1f}%")

                st.markdown("**Priority Improvement Flow**")
                af1, af2, af3, af4 = st.columns(4)
                af1.markdown("**1. Problem**")
                af1.write(flow.get("problem", "-"))
                af2.markdown("**2. Causes**")
                for cause in flow.get("causes", [])[:3]:
                    af2.write(f"- {cause}")
                af3.markdown("**3. Learning Partner**")
                af3.write(flow.get("learning_partner", "-"))
                af3.caption(flow.get("learning_reason", ""))
                af4.markdown("**4. Top Actions**")
                for action in flow.get("urgent_actions", [])[:3]:
                    af4.write(f"- {action}")

                ux, uy, uz = st.columns(3)
                users_block = intel.get("users", {})
                finance_block = intel.get("finance", {})
                ops_block = intel.get("operations", {})
                playbook = intel.get("playbook", {})
                benchmark_notes = intel.get("benchmark_notes", [])
                benchmark_examples_df = pd.DataFrame(intel.get("benchmark_examples", []))
                live_data = get_cached_recommendations(business_type=playbook.get("feed_type", "Office"))

                ux.markdown("**People Intelligence**")
                ux.write(f"Users: {users_block.get('total', 0)}")
                ux.write(f"Admins: {users_block.get('admins', 0)}")
                ux.write(f"Employees: {users_block.get('employees', 0)}")
                ux.write(f"Male/Female: {users_block.get('male', 0)}/{users_block.get('female', 0)}")
                ux.write(f"Phone Coverage: {users_block.get('phone_coverage_pct', 0):.1f}%")

                uy.markdown("**Operational Intelligence**")
                uy.write(f"Active Branches: {ops_block.get('branches_active', 0)}/{ops_block.get('branches_total', 0)}")
                uy.write(f"True Late: {ops_block.get('true_late_count', 0)} ({ops_block.get('late_rate_pct', 0):.1f}%)")
                uy.write(f"Approved Late: {ops_block.get('approved_late_count', 0)} ({ops_block.get('approved_late_rate_pct', 0):.1f}%)")
                uy.write(f"Warnings (30d): {ops_block.get('warnings_recent_30', 0)}")
                uy.write(f"Open Leaves: {ops_block.get('open_leaves', 0)}")
                uy.write(f"Attendance Records (30d): {ops_block.get('attendance_recent_30', 0)}")

                uz.markdown("**Financial Intelligence**")
                uz.write(f"Payments (30d): KES {finance_block.get('payments_recent_30', 0):,.0f}")
                uz.write(f"Payments Prev (30d): KES {finance_block.get('payments_prev_30', 0):,.0f}")
                uz.write(f"Payment Trend: {finance_block.get('payment_trend', 'stable')}" )
                uz.write(f"Expiry (days): {finance_block.get('expires_in_days', 'N/A')}" )

                st.divider()
                st.markdown("**Risk Flags**")
                risks = intel.get("risk_flags", [])
                if risks:
                    for risk in risks:
                        st.error(risk)
                else:
                    st.success("No critical risks flagged for this organization.")

                st.markdown("**Growth Moves**")
                growth = intel.get("growth_moves", [])
                if growth:
                    for move in growth:
                        st.success(move)
                else:
                    st.info("No immediate growth moves suggested yet.")

                st.markdown("**Top 3 Urgent Actions**")
                urgent_actions = []
                for item in intel.get("advice", []):
                    tag = str(item).upper()
                    if any(marker in tag for marker in ["ACTION:", "REMOVE:", "IMPROVE:", "ADD:", "BENCHMARK:"]):
                        urgent_actions.append(item)
                urgent_actions = urgent_actions[:3]
                if urgent_actions:
                    for idx, item in enumerate(urgent_actions, start=1):
                        st.warning(f"{idx}. {item}")
                else:
                    st.success("No urgent action is currently required.")

                st.markdown("**Advisory Actions for Super Admin**")
                for tip in intel.get("advice", []):
                    if "ACTION:" in tip:
                        st.warning(tip)
                    elif "GUIDE:" in tip or "SUPPORT:" in tip or "BENCHMARK:" in tip or "ADD:" in tip or "REMOVE:" in tip or "IMPROVE:" in tip:
                        st.info(tip)
                    else:
                        st.write(f"- {tip}")

                st.markdown("**Top Root Causes Detected**")
                for cause in intel.get("root_causes", []):
                    st.write(f"- {cause}")

                ax1, ax2 = st.columns(2)
                with ax1:
                    st.markdown("**What Leading Organizations Are Doing**")
                    if benchmark_notes:
                        for note in benchmark_notes:
                            st.info(note)
                    else:
                        st.info("Benchmark guidance will appear as more peer organization data becomes available.")
                    if not benchmark_examples_df.empty:
                        st.dataframe(benchmark_examples_df, use_container_width=True)

                with ax2:
                    st.markdown("**What to Add / Remove / Improve / Research**")
                    for label, items in [
                        ("Add", playbook.get("introduce", [])),
                        ("Remove", playbook.get("remove", [])),
                        ("Improve", playbook.get("improve", [])),
                        ("Research", playbook.get("research", [])),
                    ]:
                        if items:
                            st.markdown(f"**{label}**")
                            for item in items:
                                st.write(f"- {item}")

                st.markdown("**Live Industry Advisory Feed**")
                st.caption(
                    f"Tailored for {intel.get('business_type', 'Office')} organizations using live public management and industry feeds."
                )
                if live_data.get("error"):
                    st.warning(live_data.get("error"))
                else:
                    st.success(
                        f"Live sources responding: {live_data.get('sources_ok', 0)} | Last refresh: {live_data.get('fetched_at', 'recently')}"
                    )
                    for article in live_data.get("articles", [])[:5]:
                        st.markdown(
                            f"- **{article.get('title', 'Untitled')}** ({article.get('source', 'Source')} - {article.get('category', 'Advice')})"
                        )
                        if article.get("summary"):
                            st.caption(article.get("summary"))

    # ==========================================================
    # SETTINGS
    # ==========================================================
    elif menu == "⚙️ Settings":

        st.subheader("⚙️ Settings")

        stab1, stab2, stab3, stab4, stab5 = st.tabs([
            "🔑 Chief Administrator Password",
            "🔑 Org Superadmin Password",
            "🗑️ Reset Org Data",
            "⚠️ Full System Reset",
            "💳 Payment Config",
        ])

        with stab1:
            st.markdown("**Change Chief Administrator Password**")
            with st.form("master_pw_form", clear_on_submit=False):
                curr_pw  = st.text_input("Current Password",     type="password")
                new_mpw  = st.text_input("New Password",         type="password")
                conf_mpw = st.text_input("Confirm New Password", type="password")
                save_mpw = st.form_submit_button("💾 Update Password")

                if save_mpw:
                    curr_row = safe_read("SELECT password FROM users WHERE username='master'", conn)
                    if curr_row.empty:
                        st.error("Master user not found.")
                    elif not verify_password(curr_pw, curr_row.iloc[0]["password"]):
                        st.error("Current password is incorrect.")
                    elif not new_mpw:
                        st.error("New password cannot be empty.")
                    elif new_mpw != conf_mpw:
                        st.error("Passwords do not match.")
                    else:
                        execute_write(conn, "UPDATE users SET password=? WHERE username='master'", (hash_password(new_mpw),))
                        conn.commit()
                        st.success("✅ Chief Administrator password updated successfully.")

        with stab2:
            orgs_sp = safe_read("SELECT name FROM organizations ORDER BY name", conn)
            if orgs_sp.empty:
                st.info("No organizations.")
            else:
                sp_org = st.selectbox("Select Organization", orgs_sp["name"].tolist(), key="sp_org_sel")
                with st.form("sp_pw_form", clear_on_submit=False):
                    new_sp  = st.text_input("New Password",         type="password")
                    conf_sp = st.text_input("Confirm New Password", type="password")
                    save_sp = st.form_submit_button("💾 Reset Superadmin Password")

                    if save_sp:
                        if not new_sp:
                            st.error("Password cannot be empty.")
                        elif new_sp != conf_sp:
                            st.error("Passwords do not match.")
                        else:
                            execute_write(
                                conn,
                                "UPDATE users SET password=? WHERE organization=? AND role='superadmin'",
                                (hash_password(new_sp), sp_org)
                            )
                            conn.commit()
                            st.success(f"✅ Superadmin password for '{sp_org}' has been reset.")
                            st.rerun()

        with stab3:
            orgs_ro = safe_read("SELECT name FROM organizations ORDER BY name", conn)
            if orgs_ro.empty:
                st.info("No organizations.")
            else:
                ro_org = st.selectbox("Select Organization to Reset", orgs_ro["name"].tolist(), key="ro_org_sel")
                st.warning(
                    f"⚠️ This will permanently delete all **transactional data** for **{ro_org}** — "
                    "attendance, ratings, leaves, warnings, messages, schedules. "
                    "The organization record and super admin account will be kept."
                )
                ro_confirm = st.text_input(f"Type **{ro_org}** exactly to confirm", key="ro_confirm")
                if st.button("🗑️ Reset Organization Data", type="primary"):
                    if ro_confirm.strip() == ro_org:
                        for tbl in ["attendance", "ratings", "leaves", "warnings", "messages", "schedules"]:
                            try:
                                execute_write(conn, f"DELETE FROM {tbl} WHERE organization=?", (ro_org,))
                            except Exception:
                                pass
                        conn.commit()
                        st.success(f"✅ All transactional data for '{ro_org}' has been cleared.")
                        st.rerun()
                    else:
                        st.error("Confirmation text does not match. No data was deleted.")

        with stab4:
            st.error(
                "⚠️ **DANGER ZONE** — This permanently deletes the entire database and recreates it "
                "from scratch. ALL organizations, users, data and payments will be lost forever."
            )
            if not is_full_reset_enabled():
                st.info("Full system reset is disabled. Set environment variable TEAM_AI_ALLOW_FULL_RESET=1 to enable this action.")
            full_confirm = st.checkbox("✅ I understand this action is irreversible")
            type_confirm = st.text_input("Type  RESET  to confirm")

            if st.button("🗑️ Full System Reset", type="primary"):
                if not is_full_reset_enabled():
                    st.error("Full system reset is disabled on this deployment.")
                elif full_confirm and type_confirm.strip() == "RESET":
                    result = reset_database()
                    if result is True:
                        st.success("✅ System reset complete.")
                        st.session_state.clear()
                        st.rerun()
                    else:
                        st.error(f"Reset failed: {result}")
                elif not full_confirm:
                    st.warning("Tick the confirmation checkbox first.")
                else:
                    st.error("Type RESET exactly to proceed.")

        with stab5:
            cfg_s = load_cfg(conn)
            st.markdown("**Payment Details shown to clients when requesting payment**")
            with st.form("pay_cfg_settings", clear_on_submit=False):
                s_paybill = st.text_input("Paybill Number",       value=str(cfg_s.get("paybill",      "") or ""))
                s_till    = st.text_input("Till Number",           value=str(cfg_s.get("till_number",  "") or ""))
                s_bk_name = st.text_input("Bank Name",             value=str(cfg_s.get("bank_name",    "") or ""))
                s_bk_acc  = st.text_input("Bank Account Number",   value=str(cfg_s.get("bank_account", "") or ""))
                s_bk_br   = st.text_input("Bank Branch",           value=str(cfg_s.get("bank_branch",  "") or ""))
                save_pcfg = st.form_submit_button("💾 Save Payment Details")

                if save_pcfg:
                    try:
                        execute_write(conn, """
                            UPDATE payment_config
                            SET paybill=?, till_number=?, bank_name=?, bank_account=?, bank_branch=?
                            WHERE id=1
                        """, (s_paybill, s_till, s_bk_name, s_bk_acc, s_bk_br))
                        conn.commit()
                        st.success("✅ Payment details saved!")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            


#If you later want to disable it again for safety, set:Reset the whole System (All Data)
#TEAM_AI_ALLOW_FULL_RESET=0 in environment, or
#revert that fallback line back to "0".