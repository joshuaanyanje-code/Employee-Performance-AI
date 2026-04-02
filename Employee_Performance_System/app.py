import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import secrets
import importlib
from datetime import datetime, timedelta
from Dashboards.ui_responsive import apply_responsive_ui

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
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700&display=swap');

:root {
    --bg: #0b1220;
    --bg-soft: #111a2e;
    --card: linear-gradient(160deg, rgba(20, 30, 52, 0.92), rgba(12, 20, 36, 0.92));
    --line: rgba(148, 163, 184, 0.24);
    --text: #e7edf8;
    --muted: #9fb0cc;
    --accent-a: #1f6feb;
    --accent-b: #3aa0ff;
}

html, body, [class*="css"]  {
    font-family: 'Manrope', sans-serif;
}

.block-container {
    max-width: 1200px;
    margin: auto;
    padding-top: 0.9rem;
    padding-bottom: calc(1rem + env(safe-area-inset-bottom));
}

.login-container {
    max-width: 420px;
    margin: auto;
}

body {
    background:
        radial-gradient(1200px 700px at 12% -10%, rgba(58, 160, 255, 0.22), transparent 62%),
        radial-gradient(1000px 560px at 90% 0%, rgba(32, 95, 201, 0.16), transparent 58%),
        var(--bg);
    color: var(--text);
}

[data-testid="stMain"] div[data-testid="stVerticalBlock"] {
    background: var(--card);
    padding: 25px;
    border: 1px solid var(--line);
    border-radius: 14px;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.28);
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

button {
    background: linear-gradient(135deg, var(--accent-a), var(--accent-b));
    color: white !important;
    border-radius: 10px;
    border: 1px solid rgba(160, 204, 255, 0.28);
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-weight: 600;
    letter-spacing: 0.2px;
}

.global-sidebar-toggle {
    position: fixed;
    left: 12px;
    top: 12px;
    z-index: 100000;
    min-width: 230px;
    min-height: 66px;
    border-radius: 16px;
    border: 1px solid rgba(160, 204, 255, 0.34);
    background: linear-gradient(135deg, var(--accent-a), var(--accent-b));
    color: #fff;
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 1.14rem;
    font-weight: 800;
    letter-spacing: 0.2px;
    box-shadow: 0 14px 30px rgba(11, 25, 49, 0.45);
    cursor: pointer;
}

.global-sidebar-toggle:hover {
    filter: brightness(1.04);
}

.global-sidebar-toggle:active {
    transform: translateY(1px);
}

h1, h2, h3 {
    font-family: 'Plus Jakarta Sans', sans-serif;
    letter-spacing: 0.2px;
}

p, label, span, div {
    color: var(--text);
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
        border-radius: 10px;
    }

    button {
        min-height: 44px;
        width: 100%;
    }

    .global-sidebar-toggle {
        left: 10px;
        right: 10px;
        top: 10px;
        width: auto;
        min-height: 58px;
        min-width: 0;
        font-size: 1.06rem;
    }
}
</style>
""", unsafe_allow_html=True)


def render_global_sidebar_toggle_button():
    components.html(
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
        """,
        height=0,
        width=0,
    )


# =====================================================
# IMPORTS (SAFE 🔥)
# =====================================================
try:
    from database.db import (
        get_connection,
        create_tables,
        restore_sqlite_from_mongo_if_empty,
        backup_sqlite_to_mongo,
    )
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

    sess = pd.read_sql(
        """
        SELECT token, username, role, organization, expires_at, active
        FROM user_sessions
        WHERE token=?
        LIMIT 1
        """,
        conn,
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

    user_df = pd.read_sql(
        "SELECT username, role, organization, status FROM users WHERE username=? LIMIT 1",
        conn,
        params=(username,),
    )
    if user_df.empty:
        _invalidate_login_session(conn, token)
        return False

    urow = user_df.iloc[0]
    if str(urow.get("status", "active")).lower() == "suspended":
        _invalidate_login_session(conn, token)
        return False

    st.session_state.update({
        "logged": True,
        "role": str(urow.get("role", role)),
        "username": str(urow.get("username", username)),
        "organization": str(urow.get("organization", org)),
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


# =====================================================
# DASHBOARDS (LAZY-LOADED)
# =====================================================
DASHBOARD_TARGETS = {
    "super_admin": ("Dashboards.super_admin", "super_admin_dashboard"),
    "master_admin": ("Dashboards.master_admin", "master_admin_dashboard"),
    "admin": ("Dashboards.admin", "admin_dashboard"),
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


# =====================================================
# SUBSCRIPTION CHECK
# =====================================================
def check_subscriptions(conn):
    try:
        orgs = pd.read_sql("SELECT * FROM organizations", conn)

        for _, row in orgs.iterrows():
            try:
                expiry = pd.to_datetime(row["expires_at"])
            except:
                continue

            if expiry < datetime.now():
                conn.execute("UPDATE organizations SET status='suspended' WHERE name=?", (row["name"],))
            else:
                conn.execute("UPDATE organizations SET status='active' WHERE name=?", (row["name"],))

        conn.commit()
    except:
        pass


# =====================================================
# SESSION INIT
# =====================================================
for key in ["logged","username","role","organization","auth_token"]:
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
    create_tables()
    return True


try:
    _ensure_db_schema_ready()

    if not st.session_state.get("_mongo_restore_checked", False):
        restore_sqlite_from_mongo_if_empty()
        st.session_state["_mongo_restore_checked"] = True

    conn = get_connection()
    if not st.session_state.get("_startup_maintenance_done", False):
        _cleanup_expired_sessions(conn)
        check_subscriptions(conn)
        backup_sqlite_to_mongo()
        st.session_state["_startup_maintenance_done"] = True
except Exception as e:
    st.error(f"Database initialization failed: {e}")
    st.stop()


# =====================================================
# PUBLIC KIOSK ACCESS (BYPASS LOGIN)
# =====================================================
qp = st.query_params
qp_kiosk = qp.get("kiosk")
qp_org = qp.get("org")
if qp_kiosk and qp_org:
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

    st.markdown('<div class="login-container">', unsafe_allow_html=True)

    st.title("🔐 Team Intelligence")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        if st.form_submit_button("Login"):

            if not username or not password:
                st.warning("Enter credentials")
                return

            # MASTER LOGIN
            if username == "master" and password == "1234":
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
            user_df = pd.read_sql(
                "SELECT * FROM users WHERE username=?",
                conn,
                params=(username,)
            )

            if user_df.empty:
                st.error("User not found")
                return

            if not verify_password(password, user_df.iloc[0]["password"]):
                st.error("Wrong password")
                return

            role = user_df.iloc[0]["role"]
            org = user_df.iloc[0]["organization"]
            token = _create_login_session(conn, username, role, org)

            st.session_state.update({
                "logged": True,
                "role": role,
                "username": username,
                "organization": org,
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

    menu = st.sidebar.radio("Navigate", [
        "Super Admin",
        "Attendance Dashboard",
        "Staff Check In"
    ], key="superadmin_nav")

    log_action(conn, st.session_state.username, "NAVIGATE", menu, st.session_state.organization)

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


# ================= EMPLOYEE =================
elif role == "employee":
    employee_dashboard = load_dashboard("employee")
    if employee_dashboard:
        employee_dashboard()


# ================= UNKNOWN =================
else:
    st.error("Unknown role")
