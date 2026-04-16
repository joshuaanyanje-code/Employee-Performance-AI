import streamlit as st
import pandas as pd
import secrets
import importlib
import time
from datetime import datetime, timedelta
try:
    from Dashboards.ui_responsive import apply_responsive_ui, is_mobile_device
except Exception:
    try:
        from Employee_Performance_System.Dashboards.ui_responsive import apply_responsive_ui, is_mobile_device
    except Exception:
        def apply_responsive_ui(mode="default"):
            return None

        def is_mobile_device():
            return False

# =====================================================
# PAGE CONFIG
# =====================================================
st.set_page_config(
    page_title="Team Intelligence System",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =====================================================
# 🎨 UI STYLE
# =====================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700;900&family=Roboto+Flex:opsz,wght@8..144,400;8..144,500;8..144,700&display=swap');

:root {
    --bg: #f5f5f7;
    --bg-soft: #fbfbfd;
    --card: rgba(255, 255, 255, 0.88);
    --line: rgba(15, 23, 42, 0.08);
    --text: #1d1d1f;
    --muted: #6e6e73;
    --accent-a: #0071e3;
    --accent-b: #2d8cff;
    --shadow: 0 14px 40px rgba(15, 23, 42, 0.07);
}

html, body, [class*="css"] {
    font-family: 'Roboto', 'Roboto Flex', 'Segoe UI', sans-serif;
    color: var(--text);
}

.block-container {
    max-width: 1240px;
    margin: auto;
    padding-top: 0.9rem;
    padding-bottom: calc(1rem + env(safe-area-inset-bottom));
}

.login-container {
    max-width: 440px;
    margin: auto;
}

body {
    background:
        radial-gradient(900px 520px at 12% -10%, rgba(0, 113, 227, 0.07), transparent 60%),
        radial-gradient(820px 420px at 90% 0%, rgba(45, 140, 255, 0.05), transparent 55%),
        linear-gradient(180deg, #fbfbfd 0%, #f5f5f7 100%);
    color: var(--text);
}

[data-testid="stAppViewContainer"],
[data-testid="stMain"] {
    background: transparent;
}

[data-testid="stMain"] div[data-testid="stVerticalBlock"] {
    background: var(--card);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    padding: 22px;
    border: 1px solid var(--line);
    border-radius: 20px;
    box-shadow: var(--shadow);
}

[data-testid="stSidebar"] {
    background: rgba(255, 255, 255, 0.78);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border-right: 1px solid rgba(15, 23, 42, 0.06);
}

@media (min-width: 761px) {
    [data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        background: transparent;
        padding: 0;
        border: 0;
        border-radius: 0;
        box-shadow: none;
    }

    [data-testid="stSidebar"] .stRadio > div,
    [data-testid="stSidebar"] .stRadio label,
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] .stSelectbox,
    [data-testid="stSidebar"] .stDateInput {
        text-align: left;
        justify-content: flex-start;
        align-items: flex-start;
    }
}

button,
.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button {
    background: linear-gradient(135deg, var(--accent-a), var(--accent-b));
    color: #ffffff !important;
    border-radius: 999px;
    border: 1px solid rgba(0, 113, 227, 0.14);
    font-family: 'Roboto', 'Roboto Flex', sans-serif;
    font-weight: 500;
    letter-spacing: 0;
    box-shadow: 0 8px 18px rgba(0, 113, 227, 0.18);
}

button:hover,
.stButton > button:hover,
.stDownloadButton > button:hover,
.stFormSubmitButton > button:hover {
    border-color: rgba(0, 113, 227, 0.18);
    filter: brightness(1.02);
}

.global-sidebar-toggle {
    position: fixed;
    left: 12px;
    top: 12px;
    z-index: 100000;
    min-width: 180px;
    min-height: 60px;
    border-radius: 999px;
    border: 1px solid rgba(0, 113, 227, 0.16);
    background: rgba(255, 255, 255, 0.92);
    color: var(--text);
    font-family: 'Roboto', 'Roboto Flex', sans-serif;
    font-size: 1.02rem;
    font-weight: 600;
    letter-spacing: 0;
    box-shadow: var(--shadow);
    cursor: pointer;
}

.global-sidebar-toggle:hover {
    background: #ffffff;
}

.global-sidebar-toggle:active {
    transform: translateY(1px);
}

h1, h2, h3 {
    font-family: 'Roboto', 'Roboto Flex', sans-serif;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text);
}

p, label, span, div {
    color: var(--text);
}

small, .stCaption {
    color: var(--muted) !important;
}

[data-testid="stMetric"] {
    background: rgba(255, 255, 255, 0.94);
    border: 1px solid rgba(15, 23, 42, 0.06);
    border-radius: 18px;
    padding: 0.45rem 0.65rem;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04);
}

.stTextInput input,
.stTextArea textarea,
.stDateInput > div,
.stNumberInput input {
    background: rgba(255, 255, 255, 0.95) !important;
    border: 1px solid rgba(15, 23, 42, 0.08) !important;
    border-radius: 14px !important;
    color: var(--text) !important;
}

.stSelectbox div[data-baseweb="select"] > div,
.stMultiSelect div[data-baseweb="select"] > div {
    background: #ffffff !important;
    border: 1px solid rgba(15, 23, 42, 0.10) !important;
    border-radius: 14px !important;
    color: var(--text) !important;
}

.stSelectbox div[data-baseweb="select"] input,
.stMultiSelect div[data-baseweb="select"] input,
.stSelectbox div[data-baseweb="select"] span,
.stMultiSelect div[data-baseweb="select"] span {
    color: var(--text) !important;
    -webkit-text-fill-color: var(--text) !important;
}

.stSelectbox svg,
.stMultiSelect svg {
    fill: var(--text) !important;
    color: var(--text) !important;
}

div[data-baseweb="popover"],
div[data-baseweb="popover"] ul,
div[role="listbox"] {
    background: #ffffff !important;
    color: var(--text) !important;
    border: 1px solid rgba(15, 23, 42, 0.10) !important;
    box-shadow: 0 12px 28px rgba(15, 23, 42, 0.10) !important;
}

div[role="option"] {
    background: #ffffff !important;
    color: var(--text) !important;
}

div[role="option"]:hover {
    background: rgba(0, 113, 227, 0.08) !important;
}

div[role="option"][aria-selected="true"] {
    background: rgba(0, 113, 227, 0.14) !important;
    color: #005bb5 !important;
}

[data-testid="stExpander"] {
    border: 1px solid rgba(15, 23, 42, 0.08);
    border-radius: 18px;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.95);
}

[data-testid="stExpander"] summary {
    font-weight: 600;
    color: var(--text);
    background: linear-gradient(90deg, rgba(0, 113, 227, 0.07), rgba(255, 255, 255, 0.96));
}

@media (max-width: 760px) {
    .block-container {
        max-width: 100%;
        padding-left: 0.72rem;
        padding-right: 0.72rem;
    }

    .login-container {
        max-width: 100%;
    }

    div[data-testid="stVerticalBlock"] {
        padding: 14px;
        border-radius: 16px;
    }

    button,
    .stButton > button,
    .stDownloadButton > button,
    .stFormSubmitButton > button {
        min-height: 44px;
        width: 100%;
    }

    .global-sidebar-toggle {
        left: 10px;
        right: 10px;
        top: 10px;
        width: auto;
        min-height: 56px;
        min-width: 0;
        font-size: 1rem;
    }
}
</style>
""", unsafe_allow_html=True)


def render_global_sidebar_toggle_button():
    st.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const BTN_ID = "global-sidebar-toggle-btn";

            function findToggleControl() {
                const selectors = [
                    '[data-testid="stSidebarCollapseButton"]',
                    '[data-testid="stSidebarCollapseButton"] button',
                    '[data-testid="collapsedControl"]',
                    '[data-testid="collapsedControl"] button',
                    'button[aria-label="Close sidebar"]',
                    'button[aria-label="Open sidebar"]',
                    'button[title="Close sidebar"]',
                    'button[title="Open sidebar"]'
                ];

                for (const sel of selectors) {
                    const el = doc.querySelector(sel);
                    if (!el) continue;
                    const style = window.parent.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        return el;
                    }
                }
                return null;
            }

            function triggerSidebarToggle() {
                const control = findToggleControl();
                if (control) {
                    control.click();
                }
            }

            let btn = doc.getElementById(BTN_ID);
            if (!btn) {
                btn = doc.createElement('button');
                btn.id = BTN_ID;
                btn.type = 'button';
                btn.className = 'global-sidebar-toggle';
                btn.innerText = 'Show/Hide Menu';
                btn.onclick = triggerSidebarToggle;
                doc.body.appendChild(btn);
            } else {
                btn.onclick = triggerSidebarToggle;
            }
        })();
        </script>
        """
    )


# =====================================================
# IMPORTS (SAFE 🔥)
# =====================================================
try:
    from database.db import create_tables, get_connection, refresh_env_from_streamlit_secrets
except Exception as e:
    st.error(f"Database import error: {e}")
    st.stop()

# SAFE FALLBACKS (🔥 prevents crash)
def verify_password(input_password, stored_password):
    import hashlib
    return hashlib.sha256(input_password.encode()).hexdigest() == stored_password

def log_action(conn, user, action, role, org):
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT,
            action TEXT,
            role TEXT,
            organization TEXT,
            time TEXT
        )
        """)
        conn.execute("""
        INSERT INTO logs(user,action,role,organization,time)
        VALUES (?,?,?,?,?)
        """,(user,action,role,org,str(datetime.now())))
        conn.commit()
    except:
        pass


def _qp_get_value(params, key):
    raw = params.get(key)
    if isinstance(raw, list):
        return raw[0] if raw else ""
    return str(raw or "")


def _set_auth_query_param(token):
    try:
        st.query_params["auth"] = token
    except Exception:
        pass


def _clear_auth_query_param():
    try:
        if "auth" in st.query_params:
            del st.query_params["auth"]
    except Exception:
        pass


def _create_login_session(conn, username, role, org):
    token = secrets.token_urlsafe(48)
    now = datetime.now()
    expires = now + timedelta(days=30)

    try:
        conn.execute(
            "DELETE FROM user_sessions WHERE username=?",
            (username,),
        )
    except Exception:
        pass

    conn.execute(
        """
        INSERT INTO user_sessions(token, username, role, organization, created_at, expires_at, active)
        VALUES (?,?,?,?,?,?,1)
        """,
        (
            token,
            username,
            role,
            org,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            expires.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    return token


def _invalidate_login_session(conn, token):
    if not token:
        return
    try:
        conn.execute("UPDATE user_sessions SET active=0 WHERE token=?", (token,))
        conn.commit()
    except Exception:
        pass


def _restore_session_from_token(conn, token):
    if not token:
        return False

    sess = _safe_read(
        conn,
        """
        SELECT token, username, role, organization, expires_at, active
        FROM user_sessions
        WHERE token=?
        LIMIT 1
        """,
        params=(token,),
    )

    if sess.empty:
        return False

    row = sess.iloc[0]
    if int(row.get("active", 0)) != 1:
        return False

    exp = pd.to_datetime(row.get("expires_at"), errors="coerce")
    if pd.isna(exp) or exp.to_pydatetime() < datetime.now():
        _invalidate_login_session(conn, token)
        return False

    username = str(row.get("username", "")).strip()
    role = str(row.get("role", "")).strip()
    org = str(row.get("organization", "")).strip()

    if username.lower() == "master":
        st.session_state.update({
            "logged": True,
            "role": "master",
            "username": "master",
            "organization": "MASTER",
            "auth_token": token,
        })
        return True

    urow = _fetch_single_db_row(
        conn,
        "SELECT username, role, organization, branch, status FROM users WHERE username=? AND organization=? LIMIT 1",
        (username, org),
    )
    if not urow:
        _invalidate_login_session(conn, token)
        return False
    user_status = str(urow.get("status", "active") or "active").lower()
    if user_status in {"suspended", "inactive", "disabled", "deactivated", "blocked", "locked"}:
        _set_login_blocked_message(f"Your account is currently {user_status}. Access is not authorized.")
        _invalidate_login_session(conn, token)
        return False

    current_org = str(urow.get("organization", org) or "").strip()
    current_branch = str(urow.get("branch", "") or "").strip()
    access_issue = _get_access_block_reason(conn, current_org, current_branch)
    if access_issue:
        _set_login_blocked_message(access_issue)
        _invalidate_login_session(conn, token)
        return False

    st.session_state.update({
        "logged": True,
        "role": str(urow.get("role", role)),
        "username": str(urow.get("username", username)),
        "organization": current_org,
        "branch": current_branch,
        "auth_token": token,
    })
    return True


def _cleanup_expired_sessions(conn):
    try:
        conn.execute(
            "DELETE FROM user_sessions WHERE expires_at IS NOT NULL AND datetime(expires_at) < datetime('now')"
        )
        conn.commit()
    except Exception:
        pass


def _set_login_blocked_message(message):
    text = str(message or "").strip()
    if text:
        st.session_state["login_blocked_message"] = text


def _get_access_block_reason(conn, organization, branch=""):
    org_name = str(organization or "").strip()
    branch_name = str(branch or "").strip()

    if not org_name or org_name.upper() == "MASTER":
        return ""

    org_row = _fetch_single_db_row(
        conn,
        "SELECT name, status, expires_at FROM organizations WHERE name=? LIMIT 1",
        (org_name,),
    )
    if not org_row:
        return f"Organization '{org_name}' is not authorized or no longer exists."

    org_status = str(org_row.get("status", "active") or "active").strip().lower()
    expires_at = pd.to_datetime(org_row.get("expires_at"), errors="coerce")

    if pd.notna(expires_at) and expires_at.to_pydatetime() < datetime.now():
        return f"Organization '{org_name}' subscription has expired. Access is not authorized."

    if org_status in {"inactive", "disabled", "suspended", "deactivated", "blocked", "locked"}:
        return f"Organization '{org_name}' is currently {org_status}. Access is not authorized."

    if branch_name:
        branch_row = _fetch_single_db_row(
            conn,
            "SELECT name, status FROM branches WHERE organization=? AND name=? LIMIT 1",
            (org_name, branch_name),
        )
        if not branch_row:
            return f"Branch '{branch_name}' is not authorized for organization '{org_name}'."

        branch_status = str(branch_row.get("status", "active") or "active").strip().lower()
        if branch_status in {"inactive", "disabled", "suspended", "deactivated", "blocked", "locked"}:
            return f"Branch '{branch_name}' is currently {branch_status}. Access is not authorized."

    return ""


def _fetch_single_db_row(conn, query, params=()):
    try:
        if params is None:
            params_tuple = ()
        elif isinstance(params, tuple):
            params_tuple = params
        elif isinstance(params, list):
            params_tuple = tuple(params)
        else:
            params_tuple = (params,)

        cur = conn.execute(query, params_tuple)
        row = cur.fetchone()
    except Exception:
        return None

    if row is None:
        return None

    columns = [col[0] for col in cur.description] if cur.description else []
    return dict(zip(columns, row))


def _safe_read(conn, query, params=None):
    try:
        if params is None:
            return pd.read_sql(query, conn)
        normalized_params = tuple(params) if isinstance(params, (list, tuple)) else (params,)
        return pd.read_sql(query, conn, params=normalized_params)
    except Exception:
        try:
            normalized_params = tuple(params) if isinstance(params, (list, tuple)) else ((params,) if params is not None else ())
            cur = conn.execute(query, normalized_params)
            rows = cur.fetchall()
            columns = [col[0] for col in cur.description] if cur.description else []
            return pd.DataFrame(rows, columns=columns)
        except Exception:
            return pd.DataFrame()


def _enforce_logged_in_access(conn):
    if not st.session_state.get("logged"):
        return False

    role = str(st.session_state.get("role", "") or "").strip().lower()
    if role == "master":
        return True

    username = str(st.session_state.get("username", "") or "").strip()
    org = str(st.session_state.get("organization", "") or "").strip()
    if not username or not org:
        return False

    urow = _fetch_single_db_row(
        conn,
        "SELECT username, role, organization, branch, status FROM users WHERE username=? AND organization=? LIMIT 1",
        (username, org),
    )
    if not urow:
        _set_login_blocked_message("Your account was not found. Please log in again.")
    else:
        user_status = str(urow.get("status", "active") or "active").strip().lower()
        if user_status in {"suspended", "inactive", "disabled", "deactivated", "blocked", "locked"}:
            _set_login_blocked_message(f"Your account is currently {user_status}. Access is not authorized.")
        else:
            org = str(urow.get("organization", st.session_state.get("organization", "")) or "").strip()
            branch = str(urow.get("branch", "") or "").strip()
            access_issue = _get_access_block_reason(conn, org, branch)
            if access_issue:
                _set_login_blocked_message(access_issue)
            else:
                st.session_state["organization"] = org
                st.session_state["branch"] = branch
                return True

    current_token = st.session_state.get("auth_token") or _qp_get_value(st.query_params, "auth")
    _invalidate_login_session(conn, current_token)
    _clear_auth_query_param()
    st.session_state["logged"] = False
    st.session_state["role"] = ""
    st.session_state["username"] = ""
    st.session_state["organization"] = ""
    st.session_state["auth_token"] = ""
    st.session_state["branch"] = ""
    st.rerun()


# =====================================================
# DASHBOARDS (LAZY-LOADED)
# =====================================================
DASHBOARD_TARGETS = {
    "super_admin": ("Dashboards.super_admin", "super_admin_dashboard"),
    "master_admin": ("Dashboards.master_admin", "master_admin_dashboard"),
    "admin": ("Dashboards.admin", "admin_dashboard"),
    "hr": ("Dashboards.hr", "hr_dashboard"),
    "employee": ("Dashboards.employee", "employee_dashboard"),
    "kiosk": ("Dashboards.kiosk", "kiosk_dashboard"),
    "attendance": ("Dashboards.attendance", "attendance_dashboard"),
}


def load_dashboard(name):
    target = DASHBOARD_TARGETS.get(name)
    if not target:
        return None

    module_name, func_name = target
    try:
        module = importlib.import_module(module_name)
        return getattr(module, func_name, None)
    except Exception as e:
        st.error(f"Failed to load {name} dashboard: {e}")
        return None


def log_navigation_once(conn, menu_label):
    marker = f"{str(st.session_state.get('role', '')).lower()}::{str(menu_label or '').strip()}"
    if st.session_state.get("_last_nav_marker") == marker:
        return
    st.session_state["_last_nav_marker"] = marker
    log_action(conn, st.session_state.username, "NAVIGATE", menu_label, st.session_state.organization)


# =====================================================
# SUBSCRIPTION CHECK
# =====================================================
def check_subscriptions(conn):
    try:
        orgs = _safe_read(conn, "SELECT * FROM organizations")

        for _, row in orgs.iterrows():
            try:
                expiry = pd.to_datetime(row["expires_at"])
            except:
                continue

            current_status = str(row.get("status", "active") or "active").strip().lower()
            if expiry < datetime.now():
                new_status = "suspended"
            elif current_status in {"inactive", "disabled"}:
                new_status = current_status
            else:
                new_status = "active"

            conn.execute("UPDATE organizations SET status=? WHERE name=?", (new_status, row["name"]))

        conn.commit()
    except:
        pass


# =====================================================
# SESSION INIT
# =====================================================
for key in ["logged","username","role","organization","branch","auth_token"]:
    if key not in st.session_state:
        if key == "logged":
            st.session_state[key] = False
        else:
            st.session_state[key] = ""


# =====================================================
# INIT DB (FIXED 🔥)
# =====================================================
@st.cache_resource(show_spinner=False)
def _ensure_db_schema_ready():
    refresh_env_from_streamlit_secrets()
    try:
        create_tables()
        return True
    except Exception as schema_error:
        # If schema is malformed, backup and recreate
        try:
            from database.db import DB_PATH
            import os
            import shutil
            if os.path.exists(DB_PATH):
                backup_path = f"{DB_PATH}.backup_{int(time.time())}"
                shutil.move(DB_PATH, backup_path)
                st.warning(f"⚠️ Corrupted database backed up to: {backup_path}")
            create_tables()
            return True
        except Exception as recovery_error:
            raise Exception(f"Database initialization failed: {schema_error}. Recovery also failed: {recovery_error}")


try:
    _ensure_db_schema_ready()

    conn = get_connection()
    if not st.session_state.get("_startup_maintenance_done", False):
        _cleanup_expired_sessions(conn)
        check_subscriptions(conn)
        st.session_state["_startup_maintenance_done"] = True
except Exception as e:
    st.error(f"Database initialization failed: {e}")
    st.stop()


# =====================================================
# PUBLIC KIOSK ACCESS (BYPASS LOGIN)
# =====================================================
qp = st.query_params
qp_kiosk = _qp_get_value(qp, "kiosk")
qp_org = _qp_get_value(qp, "org")
if qp_kiosk and qp_org:
    kiosk_access_issue = _get_access_block_reason(conn, qp_org, qp_kiosk)
    if kiosk_access_issue:
        apply_responsive_ui("kiosk")
        st.error(kiosk_access_issue)
        st.info("This kiosk is currently disabled. Contact the Organization Administrator or Super Admin.")
        st.stop()

    kiosk_dashboard = load_dashboard("kiosk")
    if kiosk_dashboard:
        kiosk_dashboard()
    else:
        st.error("Kiosk module missing")
    st.stop()


# =====================================================
# AUTO LOGIN RESTORE (REFRESH-SAFE)
# =====================================================
if not st.session_state.logged:
    auth_token = _qp_get_value(st.query_params, "auth")
    if auth_token:
        if _restore_session_from_token(conn, auth_token):
            _set_auth_query_param(auth_token)
            st.rerun()
        else:
            _clear_auth_query_param()


# =====================================================
# LOGIN
# =====================================================
def login():

    apply_responsive_ui("auth")

    blocked_message = str(st.session_state.pop("login_blocked_message", "") or "").strip()
    if blocked_message:
        st.error(blocked_message)

    st.markdown('<div class="login-container">', unsafe_allow_html=True)

    st.title("🔐 Team Intelligence")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        
        # Get available organizations for the username (if it exists)
        potential_orgs = []
        if username and username.lower() != "master":
            potential_orgs_df = _safe_read(
                conn,
                "SELECT DISTINCT organization FROM users WHERE lower(trim(username)) = lower(trim(?)) ORDER BY organization",
                params=(username,),
            )
            if not potential_orgs_df.empty:
                potential_orgs = potential_orgs_df["organization"].tolist()
        
        # Show organization selector only if multiple organizations have this username
        org_selector = None
        if len(potential_orgs) > 1:
            st.info(f"ℹ️ Username exists in multiple organizations. Please select your organization:")
            org_selector = st.selectbox("Organization", potential_orgs, key="login_org_selector")

        if st.form_submit_button("Login"):

            if not username or not password:
                st.warning("Enter credentials")
                return

            # MASTER LOGIN
            if username == "Master" and password == "Admin123":
                token = _create_login_session(conn, "master", "master", "MASTER")
                st.session_state.update({
                    "logged": True,
                    "role": "master",
                    "username": "master",
                    "organization": "MASTER",
                    "auth_token": token,
                })

                _set_auth_query_param(token)
                log_action(conn, "master", "LOGIN", "SYSTEM", "MASTER")
                st.rerun()

            # DATABASE LOGIN
            username_clean = username.strip()
            query = "SELECT * FROM users WHERE lower(trim(username)) = lower(trim(?))"
            params = [username_clean]
            
            # If multiple orgs and user selected one, filter by it
            if org_selector:
                query += " AND organization = ?"
                params.append(org_selector)
            
            user_df = _safe_read(conn, query, params=params)

            if user_df.empty:
                st.error("User not found in the selected organization" if org_selector else "User not found")
                return
            
            # If multiple rows but no org selector (shouldn't happen with org_selector logic above), take first
            if len(user_df) > 1 and not org_selector:
                st.error(f"Username '{username}' exists in multiple organizations. Please select your organization from the dropdown above and try again.")
                return

            if not verify_password(password, user_df.iloc[0]["password"]):
                st.error("Wrong password")
                return

            user_row = user_df.iloc[0]
            role = user_row["role"]
            org = user_row["organization"]
            branch = str(user_row.get("branch", "") or "").strip()
            user_status = str(user_row.get("status", "active") or "active").strip().lower()

            if user_status in {"suspended", "inactive", "disabled", "deactivated", "blocked", "locked"}:
                st.error(f"Your account is currently {user_status}. Access is not authorized.")
                return

            access_issue = _get_access_block_reason(conn, org, branch)
            if access_issue:
                st.error(access_issue)
                return

            token = _create_login_session(conn, username, role, org)

            st.session_state.update({
                "logged": True,
                "role": role,
                "username": username,
                "organization": org,
                "branch": branch,
                "auth_token": token,
            })

            _set_auth_query_param(token)
            log_action(conn, username, "LOGIN", role, org)
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


# =====================================================
# LOGIN FLOW
# =====================================================
if not st.session_state.logged:
    login()
    st.stop()

_enforce_logged_in_access(conn)
render_global_sidebar_toggle_button()


# =====================================================
# SIDEBAR
# =====================================================
st.sidebar.title(f"👤 {st.session_state.username}")
st.sidebar.success(st.session_state.role.upper())

if st.sidebar.button("Logout"):
    current_token = st.session_state.get("auth_token") or _qp_get_value(st.query_params, "auth")
    _invalidate_login_session(conn, current_token)
    _clear_auth_query_param()
    log_action(conn, st.session_state.username, "LOGOUT", "SYSTEM", st.session_state.organization)
    st.session_state.clear()
    st.rerun()


# =====================================================
# ROUTER
# =====================================================
role = st.session_state.role


# ================= MASTER =================
if role == "master":
    master_admin_dashboard = load_dashboard("master_admin")
    if master_admin_dashboard:
        master_admin_dashboard()


# ================= SUPER ADMIN =================
elif role == "superadmin":
    superadmin_nav_items = [
        "Super Admin",
        "Attendance Dashboard",
        "Staff Check In"
    ]
    if is_mobile_device():
        menu = st.radio("Navigate", superadmin_nav_items, key="superadmin_nav")
    else:
        menu = st.sidebar.radio("Navigate", superadmin_nav_items, key="superadmin_nav")

    log_navigation_once(conn, menu)

    if menu == "Super Admin":
        super_admin_dashboard = load_dashboard("super_admin")
        if super_admin_dashboard:
            super_admin_dashboard()

    elif menu == "Attendance Dashboard":
        attendance_dashboard = load_dashboard("attendance")
        if attendance_dashboard:
            branch = st.session_state.get("branch") or "Main"
            attendance_dashboard(conn, branch)
        else:
            st.error("Attendance module missing")

    elif menu == "Staff Check In":
        kiosk_dashboard = load_dashboard("kiosk")
        if kiosk_dashboard:
            kiosk_dashboard()


# ================= ADMIN =================
elif role == "admin":
    admin_dashboard = load_dashboard("admin")
    if admin_dashboard:
        admin_dashboard()


# ================= HR =================
elif role == "hr":
    hr_dashboard = load_dashboard("hr")
    if hr_dashboard:
        hr_dashboard()


# ================= EMPLOYEE =================
elif role == "employee":
    employee_dashboard = load_dashboard("employee")
    if employee_dashboard:
        employee_dashboard()


# ================= UNKNOWN =================
else:
    st.error("Unknown role")
