
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import os
import hashlib
import time
import importlib
from html import escape

try:
    holiday_lib = importlib.import_module("holidays")
except Exception:
    holiday_lib = None

from database.db import get_connection, execute_write, verify_password
from Dashboards.ui_responsive import apply_responsive_ui

# ==============================
# DEVICE FINGERPRINT
# ==============================
def _get_device_fingerprint(ctx):
    """Generate device fingerprint from IP + user agent hash."""
    try:
        headers = getattr(ctx, "headers", {}) if ctx else {}
        client_ip = str(headers.get("x-forwarded-for", "").split(",")[0].strip() or headers.get("x-client-ip", "") or "0.0.0.0")
        user_agent = str(headers.get("user-agent", "unknown"))
        fingerprint_str = f"{client_ip}:{user_agent}"
        fingerprint_hash = hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]
        return fingerprint_hash
    except Exception:
        return "unknown"

# ==============================
# REFRESH
# ==============================
def refresh():
    st.session_state["_k"] = st.session_state.get("_k", 0) + 1
    st.rerun()


def clear_kiosk_pin_input():
    st.session_state.pop("kiosk_pin_input", None)


def clear_kiosk_photo_state():
    st.session_state["kiosk_photo_bytes"] = None
    st.session_state["kiosk_photo_confirmed"] = False
    st.session_state["kiosk_cam_key"] = st.session_state.get("kiosk_cam_key", 0) + 1


def clear_kiosk_staff_transient_state():
    st.session_state["kiosk_show_lateness_request"] = False
    st.session_state["kiosk_show_early_request"] = False
    st.session_state.pop("kiosk_lateness_request_reason", None)
    st.session_state.pop("kiosk_early_request_reason", None)
    st.session_state.pop("kiosk_early_clockout_reason", None)
    st.session_state.pop("kiosk_selected_user", None)
    st.session_state.pop("kiosk_user_role", None)
    st.session_state["kiosk_lateness_request_date"] = datetime.now().date()


def compact_kiosk_button(label, key, *, use_container_width=True, **kwargs):
    return st.button(label, key=key, use_container_width=use_container_width, **kwargs)


def compact_kiosk_form_submit_button(label, *, use_container_width=True, **kwargs):
    return st.form_submit_button(label, use_container_width=use_container_width, **kwargs)


def home_kiosk_button(label, key):
    return st.button(label, key=key, use_container_width=True, type="primary")


def render_kiosk_hero(primary_title, secondary_title="", tertiary_title=""):
    primary_safe = escape(str(primary_title or ""))
    secondary_safe = escape(str(secondary_title or ""))
    tertiary_safe = escape(str(tertiary_title or ""))
    secondary_html = f"<div class='branch-name'>{secondary_safe}</div>" if secondary_safe else ""
    tertiary_html = f"<div class='manager-name'>{tertiary_safe}</div>" if tertiary_safe else ""
    st.markdown(
        f"""
        <div class='home-kiosk-hero'>
            <div class='home-kiosk-kicker'>Workplace kiosk</div>
            <div class='home-kiosk-title'>
                <div class='org-name'>{primary_safe}</div>
                {secondary_html}
                {tertiary_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _safe_read(conn, query, params=None):
    try:
        if params is None:
            return pd.read_sql(query, conn)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()


def _parse_time_value(value, fallback):
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except Exception:
        return datetime.strptime(fallback, "%H:%M").time()


def _get_auto_holiday_name(conn, org, today_tag):
    if holiday_lib is None:
        return ""

    holiday_profile = _safe_read(
        conn,
        """
        SELECT country_code, subdivision, auto_detect_holidays
        FROM organization_holiday_profiles
        WHERE organization=?
        LIMIT 1
        """,
        params=(org,),
    )
    if holiday_profile.empty or int(holiday_profile.iloc[0].get("auto_detect_holidays", 0)) != 1:
        return ""

    profile = holiday_profile.iloc[0]
    country_code = str(profile.get("country_code", "KE") or "KE").strip().upper()
    subdivision = str(profile.get("subdivision", "") or "").strip()

    try:
        target_date = datetime.strptime(today_tag, "%Y-%m-%d").date()
    except Exception:
        return ""

    try:
        if subdivision:
            holiday_set = holiday_lib.country_holidays(country_code, subdiv=subdivision, years=[target_date.year])
        else:
            holiday_set = holiday_lib.country_holidays(country_code, years=[target_date.year])
    except Exception:
        try:
            holiday_set = holiday_lib.country_holidays(country_code, years=[target_date.year])
        except Exception:
            return ""

    holiday_name = holiday_set.get(target_date)
    return str(holiday_name).strip() if holiday_name else ""


def _resolve_effective_work_hours(conn, org, branch, user, day_name, today_tag, default_start, default_end):
    work_start = default_start
    work_end = default_end
    off_day = False
    status_message = ""

    holiday_df = _safe_read(
        conn,
        """
        SELECT branch, holiday_name, is_closed, work_start, work_end
        FROM organization_holidays
        WHERE organization=? AND holiday_date=? AND (branch='' OR branch=? )
        ORDER BY CASE WHEN branch=? THEN 0 ELSE 1 END, id DESC
        LIMIT 1
        """,
        params=(org, today_tag, branch, branch),
    )
    if not holiday_df.empty:
        hrow = holiday_df.iloc[0]
        holiday_name = str(hrow.get("holiday_name", "Holiday")).strip() or "Holiday"
        if int(hrow.get("is_closed", 1)) == 1:
            return work_start, work_end, True, f"{holiday_name}: this branch is closed today."
        work_start = _parse_time_value(hrow.get("work_start", "09:00"), work_start.strftime("%H:%M"))
        work_end = _parse_time_value(hrow.get("work_end", "18:00"), work_end.strftime("%H:%M"))
        status_message = f"{holiday_name}: special holiday working hours apply today."
    else:
        auto_holiday_name = _get_auto_holiday_name(conn, org, today_tag)
        if auto_holiday_name:
            return work_start, work_end, True, f"{auto_holiday_name}: official holiday detected automatically for this organization."

    branch_hours = _safe_read(
        conn,
        """
        SELECT work_start, work_end, off_day
        FROM branch_working_hours
        WHERE organization=? AND branch=? AND day_name=?
        LIMIT 1
        """,
        params=(org, branch, day_name),
    )
    if not branch_hours.empty:
        brow = branch_hours.iloc[0]
        work_start = _parse_time_value(brow.get("work_start", "09:00"), work_start.strftime("%H:%M"))
        work_end = _parse_time_value(brow.get("work_end", "18:00"), work_end.strftime("%H:%M"))
        off_day = int(brow.get("off_day", 0)) == 1
        if off_day:
            return work_start, work_end, True, "This branch is closed today based on branch working hours."

    schedule = _safe_read(
        conn,
        """
        SELECT work_start, work_end, off_day
        FROM schedules
        WHERE username=? AND branch=? AND organization=? AND day=?
        LIMIT 1
        """,
        params=(user, branch, org, day_name),
    )
    if not schedule.empty:
        srow = schedule.iloc[0]
        work_start = _parse_time_value(srow.get("work_start", "09:00"), work_start.strftime("%H:%M"))
        work_end = _parse_time_value(srow.get("work_end", "18:00"), work_end.strftime("%H:%M"))
        off_day = int(srow.get("off_day", 0)) == 1
        if off_day:
            return work_start, work_end, True, "Today is marked as your off-day in schedule."
        status_message = "Your personal schedule overrides the branch default for today."

    return work_start, work_end, off_day, status_message


def _save_photo(org, branch, user, photo_bytes):
    folder = f"attendance_photos/{org}/{branch}/{user}"
    os.makedirs(folder, exist_ok=True)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    filepath = f"{folder}/{filename}"
    with open(filepath, "wb") as f:
        f.write(photo_bytes)
    return filepath


def _photo_hash(photo_bytes):
    return hashlib.sha256(photo_bytes).hexdigest()


def _notify_branch_admins_for_early_request(conn, org, branch, employee, reason):
    admins = _safe_read(
        conn,
        """
        SELECT username FROM users
        WHERE organization=? AND branch=? AND role='admin' AND status='active'
        """,
        params=(org, branch),
    )
    if admins.empty:
        return

    subject = f"Early clock-out request: {employee}"
    body = f"{employee} requested early clock-out for today. Reason: {reason}"

    for _, row in admins.iterrows():
        admin_user = str(row.get("username", "")).strip()
        if not admin_user:
            continue

        # Prevent noisy duplicates for the same employee/day/admin trio.
        existing = _safe_read(
            conn,
            """
            SELECT id FROM system_messages
            WHERE from_user=? AND to_user=? AND organization=? AND branch=?
            AND subject=? AND created_at >= datetime('now', '-1 day')
            ORDER BY id DESC LIMIT 1
            """,
            params=(employee, admin_user, org, branch, subject),
        )
        if existing.empty:
            execute_write(
                conn,
                """
                INSERT INTO system_messages(
                    from_user, to_user, organization, branch,
                    message_type, subject, body, priority, read_at, created_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                """,
                (employee, admin_user, org, branch, "early_clockout_request", subject, body, "high", ""),
            )

        execute_write(
            conn,
            """
            INSERT INTO warnings(username, organization, branch, type, message, created_at)
            VALUES (?,?,?,?,?,datetime('now'))
            """,
            (
                admin_user,
                org,
                branch,
                "early_clockout_request",
                f"Employee {employee} requested early clock-out: {reason}",
            ),
        )


def _notify_branch_admins_for_lateness_request(conn, org, branch, employee, request_date, reason):
    admins = _safe_read(
        conn,
        """
        SELECT username FROM users
        WHERE organization=? AND branch=? AND role='admin' AND status='active'
        """,
        params=(org, branch),
    )
    if admins.empty:
        return

    subject = f"Lateness request: {employee}"
    body = f"{employee} requested lateness approval for {request_date}. Reason: {reason}"

    for _, row in admins.iterrows():
        admin_user = str(row.get("username", "")).strip()
        if not admin_user:
            continue

        existing = _safe_read(
            conn,
            """
            SELECT id FROM system_messages
            WHERE from_user=? AND to_user=? AND organization=? AND branch=?
            AND subject=? AND created_at >= datetime('now', '-1 day')
            ORDER BY id DESC LIMIT 1
            """,
            params=(employee, admin_user, org, branch, subject),
        )
        if existing.empty:
            execute_write(
                conn,
                """
                INSERT INTO system_messages(
                    from_user, to_user, organization, branch,
                    message_type, subject, body, priority, read_at, created_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                """,
                (employee, admin_user, org, branch, "lateness_request", subject, body, "high", ""),
            )

        execute_write(
            conn,
            """
            INSERT INTO warnings(username, organization, branch, type, message, created_at)
            VALUES (?,?,?,?,?,datetime('now'))
            """,
            (
                admin_user,
                org,
                branch,
                "lateness_request",
                f"Employee {employee} requested lateness approval for {request_date}: {reason}",
            ),
        )


def _notify_super_admin_policy_breach(conn, org, branch, staff_user, staff_role, breach_type, subject, detail, recommendation):
    super_admins = _safe_read(
        conn,
        """
        SELECT username FROM users
        WHERE organization=?
          AND lower(coalesce(role,'')) IN ('superadmin','super_admin','master','owner')
          AND lower(coalesce(status,'active'))='active'
        """,
        params=(org,),
    )
    if super_admins.empty:
        return

    event_day = datetime.now().strftime("%Y-%m-%d")
    body = (
        f"Policy signal from kiosk.\n"
        f"User: {staff_user} ({staff_role})\n"
        f"Branch: {branch}\n"
        f"Date: {event_day}\n"
        f"Detail: {detail}\n"
        f"Recommendation: {recommendation}"
    )

    for _, row in super_admins.iterrows():
        sa_user = str(row.get("username", "")).strip()
        if not sa_user:
            continue

        existing = _safe_read(
            conn,
            """
            SELECT id FROM system_messages
            WHERE from_user=? AND to_user=? AND organization=?
              AND message_type=? AND subject=?
              AND created_at >= datetime('now', '-1 day')
            ORDER BY id DESC LIMIT 1
            """,
            params=(staff_user, sa_user, org, breach_type, subject),
        )
        if existing.empty:
            execute_write(
                conn,
                """
                INSERT INTO system_messages(
                    from_user, to_user, organization, branch,
                    message_type, subject, body, priority, read_at, created_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                """,
                (staff_user, sa_user, org, branch, breach_type, subject, body, "high", ""),
            )

    execute_write(
        conn,
        """
        INSERT INTO warnings(username, organization, branch, type, message, created_at)
        VALUES (?,?,?,?,?,datetime('now'))
        """,
        (
            staff_user,
            org,
            branch,
            breach_type,
            f"{subject} | {detail} | Recommendation: {recommendation}",
        ),
    )


# ==============================
# MAIN KIOSK DASHBOARD
# ==============================
def kiosk_dashboard():

    apply_responsive_ui("kiosk")

    conn = get_connection()

    # ==============================
    # UI STYLE
    # ==============================
    st.markdown("""
        <style>
        .block-container {max-width: 700px; padding-top: 0.9rem; padding-bottom: 2rem;}
        button {height:58px; font-size:20px; font-weight:600;}
        .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
            width: min(100%, 340px);
            display: block;
            margin: 0 auto;
            box-sizing: border-box;
            background: linear-gradient(135deg, #0071e3 0%, #2d8cff 100%) !important;
            color: #ffffff !important;
            border: 1px solid rgba(0, 113, 227, 0.14) !important;
            border-radius: 999px;
            box-shadow: 0 10px 20px rgba(0, 113, 227, 0.16) !important;
        }
        .stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
            filter: brightness(1.03);
        }
        .home-kiosk-hero {
            text-align: center;
            padding: 0.9rem 1rem 0.8rem;
            margin: 0.2rem 0 1rem;
            border-radius: 28px;
            border: 1px solid rgba(15, 23, 42, 0.07);
            background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(244,248,255,0.96));
            box-shadow: 0 14px 34px rgba(15, 23, 42, 0.06);
        }
        .home-kiosk-kicker {
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: #0071e3;
            margin-bottom: 0.35rem;
        }
        .home-kiosk-title {
            text-align: center;
            padding: 0.2rem 0 0.1rem;
        }
        .home-kiosk-title .org-name {
            font-size: 2.25rem;
            font-weight: 900;
            letter-spacing: -0.04em;
            line-height: 1.05;
            margin-bottom: 0.3rem;
            color: #1d1d1f;
        }
        .home-kiosk-title .branch-name {
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            line-height: 1.1;
            color: #1d1d1f;
        }
        .home-kiosk-title .manager-name {
            font-size: 1rem;
            font-weight: 500;
            color: #6e6e73;
            margin-top: 0.45rem;
        }
        .stSelectbox, .stTextInput, .stTextArea, .stDateInput {width: 100%;}

        [data-testid="stImage"] {
            border-radius: 14px;
            overflow: hidden;
            margin: 1rem 0;
            border: 1px solid rgba(15, 23, 42, 0.08);
        }

        @media (max-width: 640px) {
            .block-container {max-width: 100%; padding-left: 0.75rem; padding-right: 0.75rem;}
            button {height: 54px; font-size: 17px;}
            .home-kiosk-title .org-name {font-size: 2rem;}
            .home-kiosk-title .branch-name {font-size: 1.35rem;}
            .home-kiosk-title .manager-name {font-size: 0.98rem;}
        }
        </style>
    """, unsafe_allow_html=True)

    # ==============================
    # PARAMS (SAFE)
    # ==============================
    params = st.query_params

    def _param_value(key):
        val = params.get(key)
        if isinstance(val, list):
            return val[0] if val else None
        return val

    branch = _param_value("kiosk")
    org = _param_value("org")

    # SESSION FALLBACK
    if not branch:
        branch = st.session_state.get("kiosk_branch")

    if not org:
        org = st.session_state.get("organization")

    if branch:
        branch = str(branch).strip()
    if org:
        org = str(org).strip()

    # ==============================
    # 🔥 SAFE BRANCH DETECTION
    # ==============================
    if not branch:
        try:
            branch_df = pd.read_sql(
                "SELECT name FROM branches WHERE organization=?",
                conn,
                params=(org,)
            )

            if branch_df.empty:
                st.error("❌ No branches found. Create one in Super Admin.")
                return

            branch = branch_df.iloc[0]["name"]

        except Exception as e:
            st.error(f"❌ Branch load failed: {e}")
            return

    # SAVE SESSION
    st.session_state["kiosk_branch"] = branch
    st.session_state["organization"] = org

    # ==============================
    # VIEW ROUTING
    # ==============================
    if "kiosk_view" not in st.session_state:
        st.session_state["kiosk_view"] = "home"
    kiosk_view = st.session_state["kiosk_view"]

    # ==============================
    # 🔒 DEVICE LOCK (FINAL)
    # ==============================
    if "locked_branch" not in st.session_state:
        st.session_state["locked_branch"] = branch

    if st.session_state["locked_branch"] != branch:
        st.error(f"🚫 This device is permanently locked to '{st.session_state['locked_branch']}'")
        st.stop()

    # ==============================
    # VALIDATE BRANCH
    # ==============================
    valid = pd.read_sql(
        "SELECT name FROM branches WHERE name=? AND organization=?",
        conn,
        params=(branch, org)
    )

    if valid.empty:
        st.error(f"❌ Branch '{branch}' not found in organization '{org}'")
        return

    # ==============================
    # BRANCH MANAGER & GUEST SETTINGS
    # ==============================
    manager_row = _safe_read(
        conn,
        """
        SELECT username FROM users
        WHERE branch=? AND organization=? AND role='admin' AND status='active'
        ORDER BY username LIMIT 1
        """,
        params=(branch, org),
    )
    manager_name = str(manager_row.iloc[0]["username"]) if not manager_row.empty else "—"

    fb_settings = _safe_read(
        conn,
        "SELECT enabled, allow_named FROM client_feedback_settings WHERE organization=? LIMIT 1",
        params=(org,),
    )
    feedback_enabled = bool(int(fb_settings.iloc[0].get("enabled", 0))) if not fb_settings.empty else False
    allow_named_feedback = bool(int(fb_settings.iloc[0].get("allow_named", 1))) if not fb_settings.empty else True

    today = datetime.now().strftime("%Y-%m-%d")

    # ==============================================================
    # HOME
    # ==============================================================
    if kiosk_view == "home":
        render_kiosk_hero(
            org,
            branch,
            f"Branch Manager: {manager_name}",
        )

        st.markdown("<div style='height:1.5rem;'></div>", unsafe_allow_html=True)

        if home_kiosk_button("Guest Experience", key="home_btn_guest"):
            st.session_state["kiosk_view"] = "guest"
            refresh()

        st.markdown("<div style='height:0.75rem;'></div>", unsafe_allow_html=True)

        if home_kiosk_button("Staff Check In", key="home_btn_staff"):
            st.session_state["kiosk_view"] = "staff"
            refresh()

    # ==============================================================
    # GUEST EXPERIENCE
    # ==============================================================
    elif kiosk_view == "guest":
        render_kiosk_hero(
            org,
            "Guest Experience",
            "",
        )

        if compact_kiosk_button("← Back", key="guest_back"):
            st.session_state["kiosk_view"] = "home"
            refresh()

        feedback_targets = _safe_read(
            conn,
            """
            SELECT username FROM users
            WHERE branch=? AND organization=? AND role IN ('employee','admin') AND status='active'
            ORDER BY username
            """,
            params=(branch, org),
        )
        staff_names = feedback_targets["username"].astype(str).tolist() if not feedback_targets.empty else []
        ge_target_options = ["The whole team"] + staff_names

        with st.form("guest_experience_form", clear_on_submit=True):
            target_choice = st.selectbox(
                "Who is this about?",
                ge_target_options,
                key="kiosk_ge_target",
            )

            stars = st.select_slider(
                "Your Rating",
                options=["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"],
                value="⭐⭐⭐⭐⭐",
                key="kiosk_ge_stars",
            )

            message = st.text_area(
                "Your experience (optional)",
                placeholder="A few words go a long way…",
                key="kiosk_ge_message",
                height=90,
            )

            if allow_named_feedback:
                client_name = st.text_input(
                    "Your name (optional — leave blank to stay anonymous)",
                    key="kiosk_ge_name",
                )
            else:
                client_name = ""

            if compact_kiosk_form_submit_button("✅ Send Feedback"):
                feedback_scope = "general" if target_choice == "The whole team" else "individual"
                target_username = "" if feedback_scope == "general" else target_choice
                final_name = client_name.strip() if allow_named_feedback else ""
                is_anon = 1 if not final_name else 0
                star_count = len([c for c in stars if c == "⭐"])

                execute_write(
                    conn,
                    """
                    INSERT INTO client_feedback(
                        organization, branch, feedback_scope, target_username,
                        stars, message, is_anonymous, client_name, created_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,datetime('now'))
                    """,
                    (org, branch, feedback_scope, target_username, star_count,
                     str(message or "").strip(), is_anon, final_name),
                    commit=True,
                )
                st.success("✅ Feedback sent successfully. Returning to main menu...")
                time.sleep(1.5)
                st.session_state["kiosk_view"] = "home"
                refresh()

    # ==============================================================
    # STAFF CHECK IN
    # ==============================================================
    elif kiosk_view == "staff":
        render_kiosk_hero(
            org,
            "Staff Check In",
            "",
        )
        st.divider()

        if "kiosk_user" not in st.session_state:
            if compact_kiosk_button("← Back", key="staff_back"):
                clear_kiosk_photo_state()
                clear_kiosk_pin_input()
                clear_kiosk_staff_transient_state()
                st.session_state["kiosk_view"] = "home"
                refresh()

            users = _safe_read(conn, """
                SELECT username FROM users
                WHERE branch=? AND organization=? AND role IN ('employee','admin') AND status='active'
            """, params=(branch, org))

            if users.empty:
                st.error(f"❌ No active staff found in '{branch}'")
                return

            selected_user = st.selectbox("Select Your Name", users["username"], key="kiosk_selected_user")

            pin = st.text_input("Enter PIN (admin can use password)", type="password", key="kiosk_pin_input")

            if compact_kiosk_button("🔐 Verify & Continue", key="kiosk_verify_continue"):
                clear_kiosk_pin_input()
                if not pin:
                    st.warning("Please enter your PIN or password.")
                    return
                check = _safe_read(
                    conn,
                    "SELECT pin, password, role FROM users WHERE username=? AND branch=? AND organization=?",
                    params=(selected_user, branch, org),
                )
                if check.empty:
                    st.error("User not found in this branch")
                    return
                db_pin = str(check.iloc[0]["pin"]).strip()
                db_password = str(check.iloc[0].get("password", "") or "").strip()
                role_value = str(check.iloc[0].get("role", "employee") or "employee").strip().lower()
                entered_credential = str(pin).strip()
                pin_matches = bool(entered_credential) and entered_credential == db_pin
                password_matches = bool(entered_credential and db_password) and verify_password(entered_credential, db_password)
                admin_roles = {"admin", "superadmin", "super_admin", "master", "owner"}
                allow_password_login = role_value in admin_roles

                if not (pin_matches or (allow_password_login and password_matches)):
                    st.error("❌ Invalid PIN or password")
                    return
                st.session_state.kiosk_user = selected_user
                st.session_state.kiosk_user_role = role_value
                clear_kiosk_photo_state()
                st.success("✅ Verified")
                refresh()

        else:
            user = st.session_state.kiosk_user
            user_role = str(st.session_state.get("kiosk_user_role", "employee") or "employee").strip().lower()
            if not user_role:
                user_row = _safe_read(
                    conn,
                    "SELECT role FROM users WHERE username=? AND branch=? AND organization=? LIMIT 1",
                    params=(user, branch, org),
                )
                user_role = str(user_row.iloc[0].get("role", "employee") or "employee").strip().lower() if not user_row.empty else "employee"
                st.session_state.kiosk_user_role = user_role
            is_admin_user = user_role in {"admin", "superadmin", "super_admin", "master", "owner"}

            col_user, col_next = st.columns([3, 1])
            with col_user:
                role_badge = "Admin" if is_admin_user else "Employee"
                st.success(f"✅ {user} ({role_badge})")
            with col_next:
                if st.button("Next User", use_container_width=True):
                    st.session_state.pop("kiosk_user", None)
                    clear_kiosk_photo_state()
                    clear_kiosk_pin_input()
                    clear_kiosk_staff_transient_state()
                    st.session_state["kiosk_view"] = "home"
                    refresh()

            lateness_requests = _safe_read(
                conn,
                """
                SELECT id, approved_for_date, reason, approved_by, status, actual_reason, used_at, created_at
                FROM lateness_approvals
                WHERE username=? AND branch=? AND organization=?
                AND approved_for_date BETWEEN ? AND ?
                ORDER BY approved_for_date ASC, id DESC
                """,
                params=(user, branch, org, today, (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")),
            )

            if "kiosk_show_lateness_request" not in st.session_state:
                st.session_state["kiosk_show_lateness_request"] = False

            st.divider()
            if is_admin_user:
                st.info("Admin clock-in lateness does not require approval. Any breach against branch time policy is auto-reported to super admin.")
            else:
                if compact_kiosk_button("📩 Request Lateness Approval", key="kiosk_toggle_lateness_request"):
                    st.session_state["kiosk_show_lateness_request"] = not st.session_state["kiosk_show_lateness_request"]
                    refresh()

                if st.session_state.get("kiosk_show_lateness_request", False):
                    st.markdown("### Lateness Approval Request")
                    st.caption("Request for today, tomorrow, or the next day so approved lateness is not flagged as late.")

                    if not lateness_requests.empty:
                        request_view = lateness_requests.copy()
                        request_view["request_note"] = request_view["actual_reason"].fillna("")
                        st.dataframe(
                            request_view[["approved_for_date", "status", "reason", "approved_by", "request_note", "used_at"]],
                            use_container_width=True,
                        )

                    with st.form("kiosk_lateness_request_form", clear_on_submit=False):
                        lateness_request_date = st.date_input(
                            "Request lateness for",
                            value=datetime.now().date(),
                            min_value=datetime.now().date(),
                            max_value=(datetime.now() + timedelta(days=2)).date(),
                            key="kiosk_lateness_request_date",
                        )
                        lateness_reason = st.text_area(
                            "Reason for lateness request",
                            key="kiosk_lateness_request_reason",
                            placeholder="Explain why you may arrive late on the selected date.",
                        )
                        submit_lateness_request = compact_kiosk_form_submit_button("✅ Submit Lateness Request")

                        if submit_lateness_request:
                            request_date_tag = lateness_request_date.strftime("%Y-%m-%d")
                            existing_lateness = _safe_read(
                                conn,
                                """
                                SELECT id, status FROM lateness_approvals
                                WHERE username=? AND organization=? AND branch=? AND approved_for_date=?
                                ORDER BY id DESC LIMIT 1
                                """,
                                params=(user, org, branch, request_date_tag),
                            )
                            if not lateness_reason.strip():
                                st.error("Reason is required to request lateness approval.")
                            elif not existing_lateness.empty and str(existing_lateness.iloc[0].get("status", "")).lower() == "approved":
                                st.info("A lateness approval already exists for that date.")
                            else:
                                if existing_lateness.empty:
                                    execute_write(
                                        conn,
                                        """
                                        INSERT INTO lateness_approvals(
                                            username, organization, branch, approved_for_date,
                                            reason, approved_by, status, actual_reason, created_at
                                        )
                                        VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                                        """,
                                        (user, org, branch, request_date_tag, lateness_reason.strip(), "", "pending", ""),
                                    )
                                else:
                                    execute_write(
                                        conn,
                                        """
                                        UPDATE lateness_approvals
                                        SET reason=?, approved_by='', status='pending', actual_reason='', used_at=''
                                        WHERE id=?
                                        """,
                                        (lateness_reason.strip(), int(existing_lateness.iloc[0]["id"])),
                                    )
                                _notify_branch_admins_for_lateness_request(conn, org, branch, user, request_date_tag, lateness_reason.strip())
                                conn.commit()
                                st.success("Lateness request sent to admin for review.")
                                st.session_state.pop("kiosk_user", None)
                                clear_kiosk_photo_state()
                                clear_kiosk_pin_input()
                                clear_kiosk_staff_transient_state()
                                st.session_state["kiosk_view"] = "home"
                                refresh()

            st.markdown("### 📸 Capture Face")
            st.caption("Take a clear photo in good lighting. Your face should be centered and visible.")
            
            if "kiosk_cam_key" not in st.session_state:
                st.session_state["kiosk_cam_key"] = 0
            if "kiosk_photo_bytes" not in st.session_state:
                st.session_state["kiosk_photo_bytes"] = None
            if "kiosk_photo_confirmed" not in st.session_state:
                st.session_state["kiosk_photo_confirmed"] = False

            if st.session_state["kiosk_photo_bytes"] is None:
                photo = st.camera_input("Take Photo", key=f"kiosk_camera_{st.session_state['kiosk_cam_key']}")
                if photo is not None:
                    captured_photo_bytes = photo.getvalue()
                    if not captured_photo_bytes or len(captured_photo_bytes) < 15000:
                        st.error("❌ Photo quality too low. Retake clearly in good lighting.")
                    else:
                        st.session_state["kiosk_photo_bytes"] = captured_photo_bytes
                        st.session_state["kiosk_photo_confirmed"] = False
                        refresh()
                return

            photo_bytes = st.session_state["kiosk_photo_bytes"]
            st.image(photo_bytes, caption="📷 Photo Preview", use_container_width=True)

            st.markdown("#### Review & Confirm")
            cam_col1, cam_col2 = st.columns(2)
            with cam_col1:
                if st.button("🔄 Retake Photo", use_container_width=True, key="kiosk_retake_photo"):
                    clear_kiosk_photo_state()
                    refresh()
            with cam_col2:
                if st.button("✅ Save Photo", use_container_width=True, key="kiosk_confirm_photo"):
                    st.session_state["kiosk_photo_confirmed"] = True
                    refresh()

            if not st.session_state.get("kiosk_photo_confirmed", False):
                st.info("ℹ️ Tap **Save Photo** to confirm, or **Retake Photo** for a new picture.")
                return

            current_hash = _photo_hash(photo_bytes)
            image_size = len(photo_bytes)

            record = _safe_read(conn, """
                SELECT * FROM attendance
                WHERE username=? AND branch=? AND organization=? AND date=?
            """, params=(user, branch, org, today))

            settings = pd.read_sql("SELECT * FROM settings WHERE id=1", conn).iloc[0]

            today_day = datetime.now().strftime("%A")
            work_start = _parse_time_value(settings.get("work_start", "08:00"), "08:00")
            work_end = _parse_time_value(settings.get("work_end", "20:00"), "20:00")
            work_start, work_end, off_day, schedule_note = _resolve_effective_work_hours(
                conn,
                org,
                branch,
                user,
                today_day,
                today,
                work_start,
                work_end,
            )
            if schedule_note:
                st.caption(schedule_note)
            if off_day:
                st.info(schedule_note or "This branch is closed today.")
                return

            now = datetime.now()
            now_time = now.time()
            shift_start_dt = datetime.combine(now.date(), work_start)
            earliest_clockin_dt = shift_start_dt - timedelta(minutes=45)

            today_requests = _safe_read(
                conn,
                """
                SELECT id, approved_for_date, reason, approved_by, status, actual_reason, used_at, created_at
                FROM early_clockout_approvals
                WHERE username=? AND branch=? AND organization=? AND approved_for_date=?
                ORDER BY id DESC LIMIT 1
                """,
                params=(user, branch, org, today),
            )
            approved_early_request = pd.DataFrame()
            if not today_requests.empty:
                approved_early_request = today_requests[
                    today_requests["status"].astype(str).str.lower() == "approved"
                ].head(1)

            today_lateness_requests = _safe_read(
                conn,
                """
                SELECT id, approved_for_date, reason, approved_by, status, actual_reason, used_at, created_at
                FROM lateness_approvals
                WHERE username=? AND branch=? AND organization=? AND approved_for_date=?
                ORDER BY id DESC LIMIT 1
                """,
                params=(user, branch, org, today),
            )
            approved_lateness_request = pd.DataFrame()
            if not today_lateness_requests.empty:
                approved_lateness_request = today_lateness_requests[
                    today_lateness_requests["status"].astype(str).str.lower() == "approved"
                ].head(1)

            if not record.empty and record.iloc[0]["image"]:
                try:
                    old_size = os.path.getsize(record.iloc[0]["image"])
                    if abs(old_size - image_size) > 80000:
                        st.error("⚠ Face mismatch detected")
                        return
                except Exception:
                    pass

            latest_user_photo = _safe_read(
                conn,
                """
                SELECT image FROM attendance
                WHERE username=? AND branch=? AND organization=? AND image IS NOT NULL AND image!=''
                ORDER BY id DESC LIMIT 1
                """,
                params=(user, branch, org),
            )
            if not latest_user_photo.empty:
                old_path = str(latest_user_photo.iloc[0]["image"])
                if old_path and os.path.exists(old_path):
                    try:
                        with open(old_path, "rb") as f:
                            if _photo_hash(f.read()) == current_hash:
                                st.error("⚠ Duplicate photo detected. Retake a fresh live photo.")
                                return
                    except Exception:
                        pass

            latest_any_photo = _safe_read(
                conn,
                """
                SELECT username, image FROM attendance
                WHERE branch=? AND organization=? AND image IS NOT NULL AND image!=''
                ORDER BY id DESC LIMIT 10
                """,
                params=(branch, org),
            )
            if not latest_any_photo.empty:
                for _, recent in latest_any_photo.iterrows():
                    recent_path = str(recent.get("image", ""))
                    recent_uname = str(recent.get("username", ""))
                    if recent_path and os.path.exists(recent_path):
                        try:
                            with open(recent_path, "rb") as f:
                                if _photo_hash(f.read()) == current_hash and recent_uname != user:
                                    st.error("⚠ This photo appears to match another recent submission. Retake the photo.")
                                    return
                        except Exception:
                            pass

            # ==============================
            # DEVICE FINGERPRINT CHECK
            # ==============================
            try:
                ctx = getattr(st, "context", None)
                current_device_fp = _get_device_fingerprint(ctx)
                
                # Get any registered kiosk for this branch
                kiosk_check = _safe_read(
                    conn,
                    "SELECT device_fingerprint FROM kiosks WHERE branch=? AND organization=? LIMIT 1",
                    params=(branch, org),
                )
                
                if not kiosk_check.empty:
                    registered_fp = str(kiosk_check.iloc[0].get("device_fingerprint", "")).strip()
                    
                    # If no fingerprint registered yet, register it now on first use
                    if not registered_fp:
                        conn.execute(
                            "UPDATE kiosks SET device_fingerprint=? WHERE branch=? AND organization=?",
                            (current_device_fp, branch, org),
                        )
                        conn.commit()
                        st.info("✓ Device registered for secure check-in")
                    # If fingerprint exists and doesn't match, block
                    elif registered_fp != current_device_fp:
                        st.error(
                            "🚫 Access denied: This check-in was attempted from an unregistered device. "
                            "Kiosk devices are locked to prevent remote clock-in from personal phones. "
                            "Please use the authorized kiosk at your branch."
                        )
                        conn.execute(
                            "INSERT INTO warnings(username, organization, branch, type, message, created_at) "
                            "VALUES(?,?,?,?,?,datetime('now'))",
                            (user, org, branch, "unauthorized_device_clockin", 
                             f"{user} attempted check-in from unregistered device at {now.strftime('%H:%M')}"),
                        )
                        conn.commit()
                        st.stop()
            except Exception as e:
                st.warning(f"Device validation warning: {e}")

            # ==============================
            # CLOCK IN
            # ==============================
            if record.empty:
                if now < earliest_clockin_dt:
                    st.error(f"⏰ Clock-in opens at {earliest_clockin_dt.strftime('%H:%M')}. Maximum early clock-in is 30 minutes before shift.")
                elif now_time < work_start:
                    st.warning("⚠️ Early clock-in allowed within 30 minutes before shift start.")

                if compact_kiosk_button("🟢 CLOCK IN", key="kiosk_clock_in"):
                    if now < earliest_clockin_dt:
                        st.error(f"Too early. You can only clock in from {earliest_clockin_dt.strftime('%H:%M')}.")
                        return

                    filepath = _save_photo(org, branch, user, photo_bytes)

                    try:
                        late_minutes = int(settings["late_minutes"])
                    except Exception:
                        late_minutes = 15

                    delay = (now - shift_start_dt).total_seconds() / 60
                    status = "IN"

                    if delay > late_minutes:
                        if is_admin_user:
                            status = "LATE"
                            detail = (
                                f"Clocked in at {now.strftime('%H:%M')} against shift start {work_start.strftime('%H:%M')} "
                                f"(delay {int(delay)} min, grace {late_minutes} min)."
                            )
                            recommendation = "Review punctuality trend, issue coaching note, and enforce branch start-time compliance plan."
                            _notify_super_admin_policy_breach(
                                conn,
                                org,
                                branch,
                                user,
                                user_role,
                                "admin_late_clockin",
                                f"Admin late clock-in detected: {user}",
                                detail,
                                recommendation,
                            )
                            st.warning(f"⚠ Late by {int(delay)} minutes. This has been reported to super admin.")
                        elif approved_lateness_request.empty:
                            status = "LATE"
                            st.warning(f"⚠ Late by {int(delay)} minutes")
                        else:
                            st.info(
                                f"Approved lateness found for today by {approved_lateness_request.iloc[0]['approved_by'] or 'admin'}. Late flag skipped."
                            )

                    execute_write(conn, """
                    INSERT INTO attendance(username,branch,organization,clock_in,status,date,image)
                    VALUES (?,?,?,?,?,?,?)
                    """, (user, branch, org, now, status, today, filepath))

                    if (not is_admin_user) and (not approved_lateness_request.empty):
                        execute_write(
                            conn,
                            "UPDATE lateness_approvals SET status='used', used_at=datetime('now') WHERE id=?",
                            (int(approved_lateness_request.iloc[0]["id"]),),
                        )

                    conn.commit()

                    late_cnt = _safe_read(conn, """
                    SELECT COUNT(*) as cnt FROM attendance
                    WHERE username=? AND branch=? AND organization=? AND UPPER(status)='LATE'
                    """, params=(user, branch, org)).iloc[0]["cnt"]

                    if late_cnt >= 3:
                        try:
                            execute_write(
                                conn,
                                "INSERT INTO warnings(username,organization,branch,type,message,created_at) VALUES(?,?,?,?,?,datetime('now'))",
                                (user, org, branch, "late_pattern", f"Late {late_cnt} times"),
                            )
                        except Exception:
                            execute_write(
                                conn,
                                "INSERT INTO warnings(username,type,message) VALUES(?,?,?)",
                                (user, "late_pattern", f"Late {late_cnt} times"),
                            )
                        conn.commit()

                    st.success("✅ Clock In Successful. Returning to main menu...")
                    time.sleep(1.5)
                    st.session_state.pop("kiosk_user", None)
                    clear_kiosk_photo_state()
                    clear_kiosk_pin_input()
                    clear_kiosk_staff_transient_state()
                    st.session_state["kiosk_view"] = "home"
                    refresh()

            # ==============================
            # CLOCK OUT
            # ==============================
            else:
                st.info("✅ You are already clocked in today. Only clock-out is allowed now.")

                if record.iloc[0]["clock_out"]:
                    st.info("✔️ Already completed today")
                    st.session_state.pop("kiosk_user", None)
                    clear_kiosk_photo_state()
                    clear_kiosk_pin_input()
                    clear_kiosk_staff_transient_state()
                    st.session_state["kiosk_view"] = "home"
                    return

                reason = ""
                request_reason = ""

                if now_time < work_end:
                    st.warning("⚠️ Early clock-out")
                    if is_admin_user:
                        st.info("Admin early clock-out does not require approval. The event will be reported to super admin.")
                        st.session_state["kiosk_show_early_request"] = False
                    elif approved_early_request.empty:
                        st.error("❌ Early clock-out is blocked until admin approves it for today.")
                        if "kiosk_show_early_request" not in st.session_state:
                            st.session_state["kiosk_show_early_request"] = False

                        if compact_kiosk_button("📩 Request Early Clock Out", key="kiosk_toggle_early_request"):
                            st.session_state["kiosk_show_early_request"] = not st.session_state["kiosk_show_early_request"]
                            refresh()

                        if not today_requests.empty:
                            req_row = today_requests.iloc[0]
                            req_status = str(req_row.get("status", "pending")).lower()
                            req_note = str(req_row.get("actual_reason", "")).strip()
                            if req_status == "pending":
                                st.info("Your early clock-out request is pending admin review.")
                            elif req_status == "rejected":
                                st.error(f"Early clock-out request rejected: {req_note}" if req_note else "Your last early clock-out request was rejected.")
                            elif req_status == "used":
                                st.info("An approved early clock-out was already used today.")
                        if st.session_state.get("kiosk_show_early_request", False):
                            request_reason = st.text_area(
                                "Request early clock-out with reason",
                                key="kiosk_early_request_reason",
                                placeholder="Explain why you need to leave before end of shift.",
                            )
                            if compact_kiosk_button("✅ Submit Early Clock-Out Request", key="kiosk_request_early_out"):
                                if not request_reason.strip():
                                    st.error("Reason is required to request early clock-out.")
                                    return
                                if today_requests.empty:
                                    execute_write(
                                        conn,
                                        """
                                        INSERT INTO early_clockout_approvals(
                                            username, organization, branch, approved_for_date,
                                            reason, approved_by, status, actual_reason, created_at
                                        )
                                        VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                                        """,
                                        (user, org, branch, today, request_reason.strip(), "", "pending", ""),
                                    )
                                else:
                                    existing_id = int(today_requests.iloc[0]["id"])
                                    existing_status = str(today_requests.iloc[0].get("status", "pending")).lower()
                                    if existing_status == "approved":
                                        st.info("An approved early clock-out already exists for today.")
                                        return
                                    execute_write(
                                        conn,
                                        """
                                        UPDATE early_clockout_approvals
                                        SET reason=?, approved_by='', status='pending', actual_reason='', used_at=''
                                        WHERE id=?
                                        """,
                                        (request_reason.strip(), existing_id),
                                    )
                                _notify_branch_admins_for_early_request(conn, org, branch, user, request_reason.strip())
                                conn.commit()
                                st.success("✅ Early clock-out request sent to admin for review.")
                                st.session_state["kiosk_show_early_request"] = False
                                refresh()
                    else:
                        approval_row = approved_early_request.iloc[0]
                        st.success(f"✅ Admin approval found for today by {approval_row['approved_by']}. Reason: {approval_row['reason']}")
                        st.session_state["kiosk_show_early_request"] = False
                    reason = st.text_area("Enter reason for leaving early", key="kiosk_early_clockout_reason")

                if compact_kiosk_button("🔴 CLOCK OUT", key="kiosk_clock_out"):
                    if now_time < work_end and not reason.strip() and not is_admin_user:
                        st.error("❌ Reason required")
                        return
                    if now_time < work_end and approved_early_request.empty and not is_admin_user:
                        st.error("❌ Admin approval is required before early clock-out.")
                        return
                    if now_time < work_end and is_admin_user and not reason.strip():
                        reason = "No reason provided"

                    filepath = _save_photo(org, branch, user, photo_bytes)

                    execute_write(conn, """
                    UPDATE attendance
                    SET clock_out=?, status='OUT', image=?
                    WHERE username=? AND branch=? AND organization=? AND date=?
                    """, (now, filepath, user, branch, org, today))

                    if now_time < work_end and is_admin_user:
                        early_minutes = int((datetime.combine(now.date(), work_end) - now).total_seconds() / 60)
                        detail = (
                            f"Clocked out at {now.strftime('%H:%M')} before shift end {work_end.strftime('%H:%M')} "
                            f"({max(early_minutes, 0)} min early). Reason: {reason.strip() or 'No reason provided'}."
                        )
                        recommendation = "Review branch coverage risk, reinforce policy, and apply formal manager accountability follow-up."
                        _notify_super_admin_policy_breach(
                            conn,
                            org,
                            branch,
                            user,
                            user_role,
                            "admin_early_clockout",
                            f"Admin early clock-out detected: {user}",
                            detail,
                            recommendation,
                        )
                    elif now_time < work_end and not approved_early_request.empty:
                        execute_write(
                            conn,
                            "UPDATE early_clockout_approvals SET status='used', actual_reason=?, used_at=datetime('now') WHERE id=?",
                            (reason.strip(), int(approved_early_request.iloc[0]["id"])),
                        )

                    conn.commit()

                    st.success("✅ Clock Out Successful. Returning to main menu...")
                    time.sleep(1.5)
                    st.session_state.pop("kiosk_user", None)
                    clear_kiosk_photo_state()
                    clear_kiosk_pin_input()
                    clear_kiosk_staff_transient_state()
                    st.session_state["kiosk_view"] = "home"
                    refresh()
