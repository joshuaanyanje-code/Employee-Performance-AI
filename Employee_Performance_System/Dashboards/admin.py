import streamlit as st
import pandas as pd
from datetime import datetime, date, time, timedelta
from urllib.parse import quote
from database.db import cached_read_sql, get_connection, get_hr_config, get_kpi_ai_config, hash_password, verify_password, log_action, is_recent_duplicate_message, get_phone_uniqueness_error
from Dashboards.ui_responsive import apply_responsive_ui, render_dashboard_banner
try:
    from Dashboards.ui_responsive import is_mobile_device
except Exception:
    def is_mobile_device():
        return False
try:
    from Analytics.late_fines import compute_lateness_fines, compute_lateness_fine_history, get_lateness_policy
except Exception:
    def compute_lateness_fines(*args, **kwargs):
        return pd.DataFrame(columns=["Username", "Role", "Branch", "Chargeable Late Minutes", "Approved Late Minutes", "Chargeable Hours", "Pending Minutes to Next Fine", "Fine Amount"])

    def compute_lateness_fine_history(*args, **kwargs):
        return pd.DataFrame(columns=["Month", "Username", "Role", "Branch", "Chargeable Late Minutes", "Approved Late Minutes", "Chargeable Hours", "Pending Minutes to Next Fine", "Fine Amount"])

    def get_lateness_policy(*args, **kwargs):
        return {"amount_per_hour": 0.0, "currency": "KES", "pending_request": None}
from Analytics.badges import compute_badges_for_organization, get_badge_icon
from Analytics.polls import (
    create_poll_batch,
    ensure_poll_tables,
    get_poll_results,
    get_user_poll_response,
    get_visible_polls,
    set_poll_status,
    submit_poll_response,
)


# ==============================
# HELPERS
# ==============================
def refresh():
    st.session_state["_admin_refresh"] = st.session_state.get("_admin_refresh", 0) + 1
    st.rerun()


def refresh_with_message(message, level="success"):
    st.session_state["_admin_flash"] = {"level": level, "text": str(message or "").strip()}
    refresh()


def show_flash_message():
    payload = st.session_state.pop("_admin_flash", None)
    if not payload:
        return

    text = str(payload.get("text", "")).strip()
    level = str(payload.get("level", "info")).lower()
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


def safe_read(query, conn, params=None):
    try:
        normalized_params = tuple(params) if isinstance(params, (list, tuple)) else ((params,) if params is not None else ())
        query_text = str(query or "").strip()
        if query_text.lower().startswith("select") and not getattr(conn, "in_transaction", False):
            return cached_read_sql(query_text, normalized_params)
        if params is None:
            return pd.read_sql(query, conn)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()




def parse_hhmm(value, fallback="09:00"):
    txt = str(value or fallback)
    try:
        return datetime.strptime(txt, "%H:%M").time()
    except Exception:
        return datetime.strptime(fallback, "%H:%M").time()


def build_kiosk_link(branch, org):
    b = quote(str(branch or ""), safe="")
    o = quote(str(org or ""), safe="")
    path = f"/?kiosk={b}&org={o}"
    try:
        ctx = getattr(st, "context", None)
        headers = getattr(ctx, "headers", None) if ctx is not None else None
        if headers:
            host = str(headers.get("x-forwarded-host") or headers.get("host") or "").strip()
            proto = str(headers.get("x-forwarded-proto") or "https").strip() or "https"
            if host:
                return f"{proto}://{host}{path}"
    except Exception:
        pass
    return path


def apply_date_range(df, col, range_sel, start_date, end_date):
    if df.empty or col not in df.columns:
        return df

    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="coerce")
    out = out[out[col].notna()]

    now = pd.Timestamp.now()
    if range_sel == "Day":
        return out[out[col].dt.date == date.today()]
    if range_sel == "Week":
        return out[out[col] >= (now - pd.Timedelta(days=6))]
    if range_sel == "Month":
        return out[out[col] >= (now - pd.Timedelta(days=29))]

    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return out[(out[col] >= start_ts) & (out[col] <= end_ts)]


def admin_dashboard():
    apply_responsive_ui("default")

    conn = get_connection()
    username = st.session_state.get("username")
    org = st.session_state.get("organization")

    if not username or not org:
        st.error("Session missing user or organization.")
        return

    user_row = safe_read(
        "SELECT * FROM users WHERE username=? AND organization=?",
        conn,
        params=(username, org),
    )

    if user_row.empty:
        st.error("Admin user not found.")
        return

        

    current_status = str(user_row.iloc[0].get("status", "active")).lower()
    if current_status == "suspended":
        st.error("Account suspended. Contact super admin.")
        return
    if current_status == "probation":
        st.warning("Your account is on probation and under management review.")

    admin_branch = str(user_row.iloc[0].get("branch", "")).strip()
    if not admin_branch:
        st.error("Admin branch not assigned.")
        return

    settings_df = safe_read("SELECT * FROM settings WHERE id=1", conn)
    if settings_df.empty:
        st.error("Settings row missing.")
        return
    settings = settings_df.iloc[0]

    work_start = parse_hhmm(settings.get("work_start", "09:00"), "09:00")
    work_end = parse_hhmm(settings.get("work_end", "18:00"), "18:00")
    ensure_poll_tables(conn)

    hr_config = get_hr_config(conn, org)
    kpi_ai_config = get_kpi_ai_config(conn, org)
    hr_mode_enabled = bool(int(hr_config.get("hr_mode_enabled", 0) or 0))
    hr_handles_leave = hr_mode_enabled and bool(int(hr_config.get("hr_handles_leave", 1) or 0))
    hr_handles_discipline = hr_mode_enabled and bool(int(hr_config.get("hr_handles_discipline", 1) or 0))
    hr_handles_performance = hr_mode_enabled and bool(int(hr_config.get("hr_handles_performance", 1) or 0))

    st.title("Admin Dashboard")
    render_dashboard_banner(
        "Branch leadership",
        f"{admin_branch} management dashboard",
        "Monitor staff, attendance, leaves, alerts, ratings, and branch operations from one clean workspace.",
        pills=[
            f"Manager {username}",
            f"Organization {org}",
            f"Hours {work_start.strftime('%H:%M')} - {work_end.strftime('%H:%M')}",
        ],
    )
    st.caption(f"Manager: {username} | Branch: {admin_branch} | Organization: {org}")
    show_flash_message()

    if hr_mode_enabled:
        delegated = []
        if hr_handles_leave:
            delegated.append("leave approvals")
        if hr_handles_discipline:
            delegated.append("discipline and warnings")
        if hr_handles_performance:
            delegated.append("performance governance")
        if delegated:
            st.info("HR mode is ON. HR now manages " + ", ".join(delegated) + ", while branch admin stays focused on daily operations and attendance.")

    is_mobile = is_mobile_device()

    def _collapse_admin_mobile_nav():
        if is_mobile:
            st.session_state["admin_nav_open"] = False

    if "admin_nav_open" not in st.session_state:
        st.session_state["admin_nav_open"] = True

    def nav_selectbox(label, options, key, **kwargs):
        if is_mobile:
            return st.selectbox(label, options, key=key, **kwargs)
        with st.sidebar:
            return st.selectbox(label, options, key=key, **kwargs)

    menu_items = [
        "Profile",
        "Users",
        "Schedules",
        "Attendance",
        "Leaves",
        "Alerts",
        "Warnings",
        "Rate",
        "My Score",
        "KPI & Service",
        "Analytics",
        "Badges",
        "Topics",
        "Messages",
        "Polls",
        "Staff Check In",
        "Settings",
    ]
    if hr_handles_discipline:
        menu_items = [item for item in menu_items if item != "Warnings"]
    if hr_handles_performance:
        menu_items = [item for item in menu_items if item != "Rate"]

    if is_mobile:
        if st.button("Change Menu / Filter", key="admin_reopen_nav", use_container_width=True):
            st.session_state["admin_nav_open"] = True
            st.rerun()
        with st.expander("Navigation and Filter", expanded=bool(st.session_state.get("admin_nav_open", True))):
            date_range = st.date_input(
                "Select Range",
                value=(date.today(), date.today()),
                key="admin_sidebar_date",
                on_change=_collapse_admin_mobile_nav,
            )
            menu = st.radio(
                "Menu",
                menu_items,
                key="admin_menu",
                on_change=_collapse_admin_mobile_nav,
            )
    else:
        with st.sidebar:
            st.markdown("### Navigation")
            date_range = st.date_input(
                "Select Range",
                value=(date.today(), date.today()),
                key="admin_sidebar_date",
            )
            menu = st.radio("Menu", menu_items, key="admin_menu")

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    fine_policy = get_lateness_policy(conn, org) if menu in {"Attendance", "Settings"} else {
        "amount_per_hour": 0.0,
        "currency": "KES",
        "pending_request": None,
    }

    # =====================================================
    # PROFILE
    # =====================================================
    if menu == "Profile":
        st.subheader("Profile")

        branch_users = safe_read(
            """
            SELECT username, role, status, pin, phone
            FROM users
            WHERE organization=? AND branch=?
            ORDER BY role, username
            """,
            conn,
            params=(org, admin_branch),
        )

        pending_early_requests = safe_read(
            """
            SELECT COUNT(*) AS cnt
            FROM early_clockout_approvals
            WHERE organization=? AND branch=? AND status='pending'
            """,
            conn,
            params=(org, admin_branch),
        )
        pending_count = int(pending_early_requests.iloc[0]["cnt"]) if not pending_early_requests.empty else 0
        pending_lateness_requests = safe_read(
            """
            SELECT COUNT(*) AS cnt
            FROM lateness_approvals
            WHERE organization=? AND branch=? AND status='pending'
            """,
            conn,
            params=(org, admin_branch),
        )
        pending_lateness_count = int(pending_lateness_requests.iloc[0]["cnt"]) if not pending_lateness_requests.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Branch", admin_branch)
        c2.metric("Total Users", len(branch_users))
        c3.metric("Employees", int((branch_users["role"] == "employee").sum()) if not branch_users.empty else 0)
        c4.metric("Pending Early Requests", pending_count)

        lc1, lc2 = st.columns(2)
        lc1.metric("Pending Lateness Requests", pending_lateness_count)
        lc2.metric("Pending Total", pending_count + pending_lateness_count)

        if pending_count > 0 or pending_lateness_count > 0:
            st.warning("There are pending early clock-out or lateness requests waiting for your review in Attendance.")

        st.markdown("### Branch Team")
        if branch_users.empty:
            st.info("No users in this branch yet.")
        else:
            # Passwords are intentionally excluded.
            st.dataframe(branch_users[["username", "role", "status", "pin", "phone"]], use_container_width=True)

    # =====================================================
    # USERS
    # =====================================================
    elif menu == "Users":
        st.subheader("Users (Branch Only)")

        users_df = safe_read(
            """
            SELECT id, username, role, status, pin, phone
            FROM users
            WHERE organization=? AND branch=?
            ORDER BY username
            """,
            conn,
            params=(org, admin_branch),
        )

        st.dataframe(users_df if not users_df.empty else pd.DataFrame({"Info": ["No branch users"]}), use_container_width=True)

        tab_create, tab_edit, tab_suspend = st.tabs(["Create Employee", "Edit User", "Status Requests"])

        with tab_create:
            st.info("Admins can create employees only. Only super admin can delete users.")
            with st.form("admin_create_user", clear_on_submit=False):
                new_user = st.text_input("Username", key="admin_create_username")
                new_pass = st.text_input("Password", type="password", key="admin_create_password")
                new_pin = st.text_input("PIN", value="1234", key="admin_create_pin")
                new_phone = st.text_input("Phone Number (required, full format e.g. 2547XXXXXXXX)", key="admin_create_phone")
                create_sub = st.form_submit_button("Create Employee")

                if create_sub:
                    if not new_user.strip():
                        st.error("Username is required.")
                    elif not new_phone.strip():
                        st.error("Phone number is required.")
                    elif len(new_pass) < 4:
                        st.error("Password must be at least 4 characters.")
                    else:
                        exists = safe_read(
                            "SELECT id FROM users WHERE username=? AND organization=? AND branch=?",
                            conn,
                            params=(new_user.strip(), org, admin_branch),
                        )
                        normalized_phone, phone_error = get_phone_uniqueness_error(conn, new_phone)
                        if not exists.empty:
                            st.error(f"Username '{new_user.strip()}' already exists in {org}/{admin_branch}.")
                        elif phone_error:
                            st.error(phone_error)
                        else:
                            conn.execute(
                                """
                                INSERT INTO users(username,password,role,branch,organization,status,pin,phone)
                                VALUES (?,?,?,?,?,?,?,?)
                                """,
                                (
                                    new_user.strip(),
                                    hash_password(new_pass),
                                    "employee",
                                    admin_branch,
                                    org,
                                    "active",
                                    new_pin.strip() or "1234",
                                    normalized_phone,
                                ),
                            )
                            conn.commit()
                            log_action(conn, username, "CREATE EMPLOYEE", new_user.strip(), org)
                            st.session_state["admin_create_username"] = ""
                            st.session_state["admin_create_password"] = ""
                            st.session_state["admin_create_pin"] = "1234"
                            st.session_state["admin_create_phone"] = ""
                            refresh_with_message(f"Employee '{new_user.strip()}' created.")

        with tab_edit:
            editable = users_df[users_df["username"] != username] if not users_df.empty else pd.DataFrame()
            if editable.empty:
                st.info("No editable users available.")
            else:
                selected_user = st.selectbox("Select User", editable["username"].tolist(), key="admin_edit_user_select")
                row = editable[editable["username"] == selected_user].iloc[0]

                with st.form("admin_edit_user_form", clear_on_submit=False):
                    new_pin_val = st.text_input("New PIN", value=str(row.get("pin", "1234")))
                    new_phone_val = st.text_input("Phone Number (full format e.g. 2547XXXXXXXX)", value=str(row.get("phone", "") or ""))
                    reset_pass = st.text_input("Reset Password (optional)", type="password")
                    save_edit = st.form_submit_button("Save Changes")

                    if save_edit:
                        if not new_phone_val.strip():
                            st.error("Phone number is required.")
                            return
                        normalized_phone_val, phone_error = get_phone_uniqueness_error(conn, new_phone_val, exclude_username=selected_user)
                        if phone_error:
                            st.error(phone_error)
                            return
                        conn.execute(
                            "UPDATE users SET pin=?, phone=? WHERE username=? AND organization=? AND branch=?",
                            (new_pin_val.strip() or "1234", normalized_phone_val, selected_user, org, admin_branch),
                        )
                        if reset_pass.strip():
                            if len(reset_pass) < 4:
                                st.error("Reset password must be at least 4 characters.")
                                return
                            conn.execute(
                                "UPDATE users SET password=? WHERE username=? AND organization=? AND branch=?",
                                (hash_password(reset_pass), selected_user, org, admin_branch),
                            )
                        conn.commit()
                        log_action(conn, username, "EDIT USER", selected_user, org)
                        refresh_with_message("User updated.")

        with tab_suspend:
            manageable = users_df[users_df["username"] != username] if not users_df.empty else pd.DataFrame()
            if manageable.empty:
                st.info("No users to manage.")
            else:
                selected = st.selectbox("Select User", manageable["username"].tolist(), key="admin_suspend_user_select")
                row = manageable[manageable["username"] == selected].iloc[0]
                current_status = str(row.get("status", "active") or "active")
                st.write(f"Current status: **{current_status}**")
                st.caption("Branch admins now recommend sensitive status changes. Super admin remains the final approver.")

                action_labels = {
                    "probation": "Recommend Probation",
                    "suspend": "Recommend Suspension",
                    "activate": "Recommend Reactivation",
                }
                default_action = "activate" if current_status.lower() in ["suspended", "probation"] else "probation"
                default_idx = list(action_labels.keys()).index(default_action)

                with st.form("admin_status_request_form", clear_on_submit=False):
                    action_type = st.selectbox(
                        "Recommended Action",
                        list(action_labels.keys()),
                        index=default_idx,
                        format_func=lambda value: action_labels.get(value, value.title()),
                    )
                    action_reason = st.text_area(
                        "Reason for recommendation",
                        help="This reason will be reviewed by super admin before any status change is applied.",
                    )
                    request_submit = st.form_submit_button("Send Recommendation")

                    if request_submit:
                        clean_reason = action_reason.strip()
                        if not clean_reason:
                            st.error("A reason is required before sending the recommendation.")
                        else:
                            pending = safe_read(
                                """
                                SELECT id FROM admin_action_requests
                                WHERE organization=? AND branch=? AND target_username=? AND action_type=? AND status='pending'
                                ORDER BY id DESC
                                LIMIT 1
                                """,
                                conn,
                                params=(org, admin_branch, selected, action_type),
                            )
                            if not pending.empty:
                                st.error("A similar pending recommendation already exists for this user.")
                            else:
                                conn.execute(
                                    """
                                    INSERT INTO admin_action_requests(
                                        organization, branch, target_username, target_role,
                                        requested_by, action_type, reason, status, created_at
                                    )
                                    VALUES (?,?,?,?,?,?,?,'pending',datetime('now'))
                                    """,
                                    (org, admin_branch, selected, str(row.get("role", "employee")), username, action_type, clean_reason),
                                )
                                conn.commit()
                                log_action(conn, username, "REQUEST USER STATUS ACTION", f"{selected}:{action_type}", org)
                                refresh_with_message(f"{action_labels.get(action_type, action_type.title())} sent for {selected}.")

                request_history = safe_read(
                    """
                    SELECT target_username, action_type, reason, status, reviewed_by, review_note, created_at, reviewed_at
                    FROM admin_action_requests
                    WHERE organization=? AND branch=? AND requested_by=?
                    ORDER BY id DESC
                    """,
                    conn,
                    params=(org, admin_branch, username),
                )
                if not request_history.empty:
                    st.markdown("### My Recommendation History")
                    st.dataframe(request_history, use_container_width=True)

        st.info("Delete user is disabled for admins. Only super admin can delete users, and sensitive status changes now require super admin approval.")

    # =====================================================
    # SCHEDULES
    # =====================================================
    elif menu == "Schedules":
        st.subheader("Schedules")
        st.caption("Set different working hours by day and mark off-days.")

        users_df = safe_read(
            "SELECT username FROM users WHERE organization=? AND branch=? AND role='employee' ORDER BY username",
            conn,
            params=(org, admin_branch),
        )

        if users_df.empty:
            st.info("No employees in your branch.")
            return

        schedules_df = safe_read(
            """
            SELECT id, username, day, work_start, work_end, off_day
            FROM schedules
            WHERE organization=? AND branch=?
            ORDER BY username, CASE day
                WHEN 'Monday' THEN 1
                WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3
                WHEN 'Thursday' THEN 4
                WHEN 'Friday' THEN 5
                WHEN 'Saturday' THEN 6
                WHEN 'Sunday' THEN 7
                ELSE 8 END
            """,
            conn,
            params=(org, admin_branch),
        )

        st.dataframe(schedules_df if not schedules_df.empty else pd.DataFrame({"Info": ["No schedules set"]}), use_container_width=True)

        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        with st.form("schedule_form", clear_on_submit=False):
            s_user = st.selectbox("Employee", users_df["username"].tolist())
            s_day = st.selectbox("Day", days)
            s_start = st.time_input("Start Time", value=time(9, 0))
            s_end = st.time_input("End Time", value=time(18, 0))
            s_off = st.checkbox("Off Day")
            s_save = st.form_submit_button("Save / Update")

            if s_save:
                existing = safe_read(
                    """
                    SELECT id FROM schedules
                    WHERE username=? AND organization=? AND branch=? AND day=?
                    """,
                    conn,
                    params=(s_user, org, admin_branch, s_day),
                )

                if existing.empty:
                    conn.execute(
                        """
                        INSERT INTO schedules(username,branch,organization,day,work_start,work_end,off_day)
                        VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            s_user,
                            admin_branch,
                            org,
                            s_day,
                            s_start.strftime("%H:%M"),
                            s_end.strftime("%H:%M"),
                            int(s_off),
                        ),
                    )
                    action = "CREATE SCHEDULE"
                else:
                    conn.execute(
                        """
                        UPDATE schedules
                        SET work_start=?, work_end=?, off_day=?
                        WHERE username=? AND organization=? AND branch=? AND day=?
                        """,
                        (
                            s_start.strftime("%H:%M"),
                            s_end.strftime("%H:%M"),
                            int(s_off),
                            s_user,
                            org,
                            admin_branch,
                            s_day,
                        ),
                    )
                    action = "UPDATE SCHEDULE"

                conn.commit()
                log_action(conn, username, action, s_user, org)
                refresh_with_message("Schedule saved.")

        if not schedules_df.empty:
            del_id = st.selectbox("Delete Schedule Row", schedules_df["id"].tolist(), key="delete_schedule_id")
            if st.button("Delete Schedule", key="delete_schedule_btn"):
                conn.execute(
                    "DELETE FROM schedules WHERE id=? AND organization=? AND branch=?",
                    (int(del_id), org, admin_branch),
                )
                conn.commit()
                log_action(conn, username, "DELETE SCHEDULE", str(del_id), org)
                refresh_with_message("Schedule deleted.", level="warning")

    # =====================================================
    # ATTENDANCE
    # =====================================================
    elif menu == "Attendance":
        st.subheader("Attendance")
        st.caption("Users clock in/out from kiosk. This view monitors lateness, absentism, and early clock-outs.")

        range_sel = nav_selectbox(
            "Range",
            ["Day", "Week", "Month", "Custom (Sidebar Date Filter)"],
            key="admin_attendance_range",
        )

        att_df = safe_read(
            """
            SELECT id, username, date, clock_in, clock_out, status, image
            FROM attendance
            WHERE organization=? AND branch=?
            ORDER BY date DESC
            """,
            conn,
            params=(org, admin_branch),
        )

        if att_df.empty:
            st.info("No attendance records.")
            st.code(f"Kiosk link: {build_kiosk_link(admin_branch, org)}")
            return

        att_df["date"] = pd.to_datetime(att_df["date"], errors="coerce")
        if range_sel == "Custom (Sidebar Date Filter)":
            filtered = apply_date_range(att_df, "date", "Custom", start_date, end_date)
        else:
            filtered = apply_date_range(att_df, "date", range_sel, start_date, end_date)

        today_str = date.today().strftime("%Y-%m-%d")
        users_df = safe_read(
            "SELECT username FROM users WHERE organization=? AND branch=? AND role='employee'",
            conn,
            params=(org, admin_branch),
        )
        todays_att = safe_read(
            "SELECT username FROM attendance WHERE organization=? AND branch=? AND date=?",
            conn,
            params=(org, admin_branch, today_str),
        )

        absent_count = 0
        if not users_df.empty:
            present_set = set(todays_att["username"].tolist()) if not todays_att.empty else set()
            expected = set(users_df["username"].tolist())
            absent_count = len(expected - present_set)

        late_count = int((filtered["status"].astype(str).str.upper() == "LATE").sum()) if not filtered.empty else 0

        def early_clockout_flag(row):
            out_raw = str(row.get("clock_out", "")).strip()
            if not out_raw or out_raw.lower() == "none":
                return False
            try:
                out_dt = pd.to_datetime(out_raw, errors="coerce")
                if pd.isna(out_dt):
                    return False
                return out_dt.time() < work_end
            except Exception:
                return False

        if filtered.empty:
            st.info("No attendance in selected range.")
        else:
            filtered = filtered.copy()
            filtered["early_clock_out"] = filtered.apply(early_clockout_flag, axis=1)

            approvals_df = safe_read(
                """
                SELECT username, approved_for_date, reason, approved_by, status, used_at, actual_reason
                FROM early_clockout_approvals
                WHERE organization=? AND branch=?
                ORDER BY approved_for_date DESC, id DESC
                """,
                conn,
                params=(org, admin_branch),
            )

            pending_requests_total = 0
            if not approvals_df.empty and "status" in approvals_df.columns:
                pending_requests_total = int((approvals_df["status"].astype(str).str.lower() == "pending").sum())

            lateness_approvals_df = safe_read(
                """
                SELECT username, approved_for_date, reason, approved_by, status, used_at, actual_reason
                FROM lateness_approvals
                WHERE organization=? AND branch=?
                ORDER BY approved_for_date DESC, id DESC
                """,
                conn,
                params=(org, admin_branch),
            )

            pending_lateness_requests_total = 0
            if not lateness_approvals_df.empty and "status" in lateness_approvals_df.columns:
                pending_lateness_requests_total = int((lateness_approvals_df["status"].astype(str).str.lower() == "pending").sum())

            filtered["approval_status"] = "not_needed"
            filtered["approval_reason"] = ""
            filtered["approved_by"] = ""
            filtered["approval_used_at"] = ""
            filtered["actual_reason"] = ""
            filtered["lateness_approval_status"] = "not_requested"
            filtered["lateness_approval_reason"] = ""
            filtered["lateness_approved_by"] = ""
            filtered["lateness_used_at"] = ""
            filtered["lateness_admin_note"] = ""

            if not approvals_df.empty:
                approvals_df = approvals_df.copy()
                approvals_df["approved_for_date"] = approvals_df["approved_for_date"].astype(str)
                approval_lookup = {
                    (str(row["username"]), str(row["approved_for_date"])): row
                    for _, row in approvals_df.iterrows()
                }

                for idx, row in filtered.iterrows():
                    row_date = row.get("date")
                    if pd.isna(row_date):
                        continue
                    key = (str(row.get("username", "")), row_date.strftime("%Y-%m-%d"))
                    approval_row = approval_lookup.get(key)
                    if approval_row is not None:
                        filtered.at[idx, "approval_status"] = str(approval_row.get("status", "approved"))
                        filtered.at[idx, "approval_reason"] = str(approval_row.get("reason", ""))
                        filtered.at[idx, "approved_by"] = str(approval_row.get("approved_by", ""))
                        filtered.at[idx, "approval_used_at"] = str(approval_row.get("used_at", ""))
                        filtered.at[idx, "actual_reason"] = str(approval_row.get("actual_reason", ""))

            if not lateness_approvals_df.empty:
                lateness_approvals_df = lateness_approvals_df.copy()
                lateness_approvals_df["approved_for_date"] = lateness_approvals_df["approved_for_date"].astype(str)
                lateness_lookup = {
                    (str(row["username"]), str(row["approved_for_date"])): row
                    for _, row in lateness_approvals_df.iterrows()
                }

                for idx, row in filtered.iterrows():
                    row_date = row.get("date")
                    if pd.isna(row_date):
                        continue
                    key = (str(row.get("username", "")), row_date.strftime("%Y-%m-%d"))
                    approval_row = lateness_lookup.get(key)
                    if approval_row is not None:
                        filtered.at[idx, "lateness_approval_status"] = str(approval_row.get("status", "pending"))
                        filtered.at[idx, "lateness_approval_reason"] = str(approval_row.get("reason", ""))
                        filtered.at[idx, "lateness_approved_by"] = str(approval_row.get("approved_by", ""))
                        filtered.at[idx, "lateness_used_at"] = str(approval_row.get("used_at", ""))
                        filtered.at[idx, "lateness_admin_note"] = str(approval_row.get("actual_reason", ""))

            early_count = int(filtered["early_clock_out"].sum())
            full_clock_count = int(filtered["clock_out"].astype(str).str.strip().ne("").sum())

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Records", len(filtered))
            c2.metric("Late", late_count)
            c3.metric("Early Clock Out", early_count)
            c4.metric("Absent Today", absent_count)

            pcol1, pcol2 = st.columns(2)
            pcol1.metric("Pending Early Requests", pending_requests_total)
            pcol2.metric("Pending Lateness Requests", pending_lateness_requests_total)

            pcol3, pcol4 = st.columns(2)
            pcol3.metric("Early Approvals Logged", len(approvals_df) if not approvals_df.empty else 0)
            pcol4.metric("Lateness Approvals Logged", len(lateness_approvals_df) if not lateness_approvals_df.empty else 0)

            branch_fines_df = compute_lateness_fines(conn, org, branch=admin_branch)
            branch_fine_history_df = compute_lateness_fine_history(conn, org, branch=admin_branch, months=6)
            branch_total_fines = float(branch_fines_df["Fine Amount"].sum()) if not branch_fines_df.empty else 0.0
            fined_people = int((branch_fines_df["Fine Amount"] > 0).sum()) if not branch_fines_df.empty else 0
            branch_chargeable_hours = int(branch_fines_df["Chargeable Hours"].sum()) if not branch_fines_df.empty else 0
            fine_rate = float(fine_policy.get("amount_per_hour", 0) or 0)

            fcol1, fcol2, fcol3, fcol4 = st.columns(4)
            fcol1.metric("Fine Rate", f"KES {fine_rate:,.0f}/hr")
            fcol2.metric("Branch Fine Total", f"KES {branch_total_fines:,.0f}")
            fcol3.metric("Employees With Fine", fined_people)
            fcol4.metric("Chargeable Hours", branch_chargeable_hours)

            if float(fine_policy.get("amount_per_hour", 0) or 0) <= 0:
                st.info("No approved lateness deduction amount is active yet. Use Settings to submit one for super admin approval.")
            else:
                st.caption(
                    f"Fine rule: each full accumulated hour of unapproved lateness is charged KES {float(fine_policy.get('amount_per_hour', 0) or 0):,.0f} for employees only. The branch due shown here is for the current payroll month and resets at month end."
                )

            st.markdown("### Lateness Fine Ledger")
            if branch_fines_df.empty:
                st.info("No staff fine records yet for this branch.")
            else:
                st.dataframe(
                    branch_fines_df[[
                        "Username", "Role", "Branch", "Chargeable Late Minutes",
                        "Chargeable Hours", "Pending Minutes to Next Fine", "Fine Amount"
                    ]],
                    use_container_width=True,
                    hide_index=True,
                )

            st.markdown("### Monthly Fine History Pull")
            if branch_fine_history_df.empty:
                st.info("No lateness fine history yet for this branch.")
            else:
                st.dataframe(
                    branch_fine_history_df[["Month", "Username", "Role", "Chargeable Late Minutes", "Chargeable Hours", "Fine Amount"]],
                    use_container_width=True,
                    hide_index=True,
                )

            st.dataframe(
                filtered[[
                    "id", "username", "date", "clock_in", "clock_out", "status",
                    "early_clock_out", "approval_status", "approved_by",
                    "approval_reason", "actual_reason", "approval_used_at",
                    "lateness_approval_status", "lateness_approved_by",
                    "lateness_approval_reason", "lateness_admin_note", "lateness_used_at"
                ]],
                use_container_width=True,
            )

            st.markdown("### Latecomers")
            late_df = filtered[filtered["status"].astype(str).str.upper() == "LATE"]
            if late_df.empty:
                st.success("No latecomers in this range.")
            else:
                st.dataframe(late_df[["id", "username", "date", "clock_in", "status"]], use_container_width=True)

            st.markdown("### Early Clock-Out Approvals")
            employees_for_approval = safe_read(
                "SELECT username FROM users WHERE organization=? AND branch=? AND role='employee' ORDER BY username",
                conn,
                params=(org, admin_branch),
            )

            with st.form("preapprove_early_clockout_form", clear_on_submit=False):
                if employees_for_approval.empty:
                    st.info("No employees available for early clock-out approval.")
                else:
                    target_user = st.selectbox("Employee", employees_for_approval["username"].tolist())
                    approve_date = st.date_input("Approved For Date", value=date.today(), key="admin_approve_early_date")
                    reason = st.text_area("Approval Reason")
                    approve = st.form_submit_button("Pre-Approve Early Clock Out")

                    if approve:
                        if not reason.strip():
                            st.error("Reason is required.")
                        else:
                            approved_tag = approve_date.strftime("%Y-%m-%d")
                            exists = safe_read(
                                """
                                SELECT id FROM early_clockout_approvals
                                WHERE username=? AND organization=? AND branch=? AND approved_for_date=?
                                ORDER BY id DESC LIMIT 1
                                """,
                                conn,
                                params=(target_user, org, admin_branch, approved_tag),
                            )
                            if exists.empty:
                                conn.execute(
                                    """
                                    INSERT INTO early_clockout_approvals(
                                        username, organization, branch, approved_for_date,
                                        reason, approved_by, status, actual_reason, created_at
                                    )
                                    VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                                    """,
                                    (target_user, org, admin_branch, approved_tag, reason.strip(), username, "approved", ""),
                                )
                            else:
                                conn.execute(
                                    """
                                    UPDATE early_clockout_approvals
                                    SET reason=?, approved_by=?, status='approved', actual_reason='', used_at=''
                                    WHERE id=?
                                    """,
                                    (reason.strip(), username, int(exists.iloc[0]["id"])),
                                )
                            conn.commit()
                            log_action(conn, username, "PRE-APPROVE EARLY CLOCK OUT", target_user, org)
                            refresh_with_message("Early clock-out approval saved.")

            st.markdown("### Lateness Approvals")
            with st.form("preapprove_lateness_form", clear_on_submit=False):
                if employees_for_approval.empty:
                    st.info("No employees available for lateness approval.")
                else:
                    target_late_user = st.selectbox("Employee for lateness", employees_for_approval["username"].tolist())
                    lateness_date = st.date_input("Approved Lateness Date", value=date.today(), key="admin_approve_lateness_date")
                    lateness_reason = st.text_area("Lateness Approval Reason")
                    approve_lateness = st.form_submit_button("Pre-Approve Lateness")

                    if approve_lateness:
                        if not lateness_reason.strip():
                            st.error("Reason is required.")
                        else:
                            approved_tag = lateness_date.strftime("%Y-%m-%d")
                            exists = safe_read(
                                """
                                SELECT id FROM lateness_approvals
                                WHERE username=? AND organization=? AND branch=? AND approved_for_date=?
                                ORDER BY id DESC LIMIT 1
                                """,
                                conn,
                                params=(target_late_user, org, admin_branch, approved_tag),
                            )
                            if exists.empty:
                                conn.execute(
                                    """
                                    INSERT INTO lateness_approvals(
                                        username, organization, branch, approved_for_date,
                                        reason, approved_by, status, actual_reason, created_at
                                    )
                                    VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                                    """,
                                    (target_late_user, org, admin_branch, approved_tag, lateness_reason.strip(), username, "approved", ""),
                                )
                            else:
                                conn.execute(
                                    """
                                    UPDATE lateness_approvals
                                    SET reason=?, approved_by=?, status='approved', actual_reason='', used_at=''
                                    WHERE id=?
                                    """,
                                    (lateness_reason.strip(), username, int(exists.iloc[0]["id"])),
                                )
                            conn.commit()
                            log_action(conn, username, "PRE-APPROVE LATENESS", target_late_user, org)
                            refresh_with_message("Lateness approval saved.")

            st.markdown("### Pending Early Clock-Out Requests")
            pending_requests_df = safe_read(
                """
                SELECT id, username, approved_for_date, reason, approved_by, status, actual_reason, created_at
                FROM early_clockout_approvals
                WHERE organization=? AND branch=? AND status='pending'
                ORDER BY approved_for_date ASC, id DESC
                """,
                conn,
                params=(org, admin_branch),
            )
            if pending_requests_df.empty:
                st.info("No pending early clock-out requests.")
            else:
                for _, req in pending_requests_df.iterrows():
                    request_id = int(req["id"])
                    request_user = str(req.get("username", ""))
                    request_date = str(req.get("approved_for_date", ""))
                    request_reason = str(req.get("reason", ""))
                    created_at = str(req.get("created_at", ""))[:16]

                    with st.expander(f"{request_user} | {request_date} | requested {created_at}"):
                        st.write(request_reason or "No reason provided.")
                        admin_response = st.text_area(
                            "Admin response note (optional for approval, recommended for rejection)",
                            key=f"admin_early_response_{request_id}",
                        )
                        pr1, pr2 = st.columns(2)
                        if pr1.button("Approve Request", key=f"approve_early_request_{request_id}"):
                            conn.execute(
                                """
                                UPDATE early_clockout_approvals
                                SET status='approved', approved_by=?, actual_reason=?
                                WHERE id=?
                                """,
                                (username, admin_response.strip(), request_id),
                            )
                            conn.commit()
                            log_action(conn, username, "APPROVE EARLY CLOCK OUT REQUEST", request_user, org)
                            st.success("Early clock-out request approved.")
                            refresh()
                        if pr2.button("Reject Request", key=f"reject_early_request_{request_id}"):
                            conn.execute(
                                """
                                UPDATE early_clockout_approvals
                                SET status='rejected', approved_by=?, actual_reason=?, used_at=''
                                WHERE id=?
                                """,
                                (username, admin_response.strip(), request_id),
                            )
                            conn.commit()
                            log_action(conn, username, "REJECT EARLY CLOCK OUT REQUEST", request_user, org)
                            st.warning("Early clock-out request rejected.")
                            refresh()

            st.markdown("### Pending Lateness Requests")
            pending_lateness_df = safe_read(
                """
                SELECT id, username, approved_for_date, reason, approved_by, status, actual_reason, created_at
                FROM lateness_approvals
                WHERE organization=? AND branch=? AND status='pending'
                ORDER BY approved_for_date ASC, id DESC
                """,
                conn,
                params=(org, admin_branch),
            )
            if pending_lateness_df.empty:
                st.info("No pending lateness requests.")
            else:
                for _, req in pending_lateness_df.iterrows():
                    request_id = int(req["id"])
                    request_user = str(req.get("username", ""))
                    request_date = str(req.get("approved_for_date", ""))
                    request_reason = str(req.get("reason", ""))
                    created_at = str(req.get("created_at", ""))[:16]

                    with st.expander(f"{request_user} | {request_date} | requested {created_at}"):
                        st.write(request_reason or "No reason provided.")
                        admin_response = st.text_area(
                            "Admin response note (optional for approval, recommended for rejection)",
                            key=f"admin_lateness_response_{request_id}",
                        )
                        lr1, lr2 = st.columns(2)
                        if lr1.button("Approve Request", key=f"approve_lateness_request_{request_id}"):
                            conn.execute(
                                """
                                UPDATE lateness_approvals
                                SET status='approved', approved_by=?, actual_reason=?, used_at=''
                                WHERE id=?
                                """,
                                (username, admin_response.strip(), request_id),
                            )
                            conn.commit()
                            log_action(conn, username, "APPROVE LATENESS REQUEST", request_user, org)
                            st.success("Lateness request approved.")
                            refresh()
                        if lr2.button("Decline Request", key=f"reject_lateness_request_{request_id}"):
                            conn.execute(
                                """
                                UPDATE lateness_approvals
                                SET status='rejected', approved_by=?, actual_reason=?, used_at=''
                                WHERE id=?
                                """,
                                (username, admin_response.strip(), request_id),
                            )
                            conn.commit()
                            log_action(conn, username, "REJECT LATENESS REQUEST", request_user, org)
                            st.warning("Lateness request rejected.")
                            refresh()

            early_df = filtered[filtered["early_clock_out"] == True]  # noqa: E712
            if early_df.empty:
                st.success("No early clock-outs in this range.")
            else:
                st.dataframe(early_df[["id", "username", "date", "clock_in", "clock_out"]], use_container_width=True)

            approvals_df = safe_read(
                """
                SELECT username, approved_for_date, reason, approved_by, status, used_at, actual_reason, created_at
                FROM early_clockout_approvals
                WHERE organization=? AND branch=?
                ORDER BY approved_for_date DESC, id DESC
                """,
                conn,
                params=(org, admin_branch),
            )
            if not approvals_df.empty:
                st.markdown("### Stored Early Clock-Out Approvals")
                st.dataframe(approvals_df, use_container_width=True)

            if not lateness_approvals_df.empty:
                st.markdown("### Stored Lateness Approvals")
                st.dataframe(lateness_approvals_df, use_container_width=True)

        st.code(f"Kiosk link: {build_kiosk_link(admin_branch, org)}")

    # =====================================================
    # LEAVES
    # =====================================================
    elif menu == "Leaves":
        st.subheader("Leaves")

        tab_request, tab_branch = st.tabs(["Request My Leave", "Branch Leave Requests"])

        with tab_request:
            if st.session_state.pop("_reset_admin_leave_form", False):
                st.session_state["admin_leave_start"] = date.today()
                st.session_state["admin_leave_end"] = date.today()
                st.session_state["admin_leave_reason"] = ""
            st.session_state.setdefault("admin_leave_start", date.today())
            st.session_state.setdefault("admin_leave_end", date.today())
            st.session_state.setdefault("admin_leave_reason", "")

            with st.form("admin_leave_request", clear_on_submit=False):
                lv_start = st.date_input("Start Date", key="admin_leave_start")
                lv_end = st.date_input("End Date", key="admin_leave_end")
                lv_reason = st.text_area("Reason", key="admin_leave_reason")
                lv_sub = st.form_submit_button("Submit Leave Request")

                if lv_sub:
                    clean_reason = lv_reason.strip()
                    if lv_end < lv_start:
                        st.error("End date cannot be before start date.")
                    elif not clean_reason:
                        st.error("Reason is required.")
                    else:
                        duplicate_leave = safe_read(
                            """
                            SELECT id
                            FROM leaves
                            WHERE username=? AND organization=? AND branch=?
                              AND start_date=? AND end_date=? AND reason=?
                              AND lower(coalesce(status, 'pending')) IN ('pending', 'approved', 'reapply')
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            conn,
                            params=(
                                username,
                                org,
                                admin_branch,
                                lv_start.strftime("%Y-%m-%d"),
                                lv_end.strftime("%Y-%m-%d"),
                                clean_reason,
                            ),
                        )
                        if not duplicate_leave.empty:
                            refresh_with_message("Duplicate leave request blocked. The same leave request already exists.", level="warning")
                        else:
                            conn.execute(
                                """
                                INSERT INTO leaves(username,organization,branch,start_date,end_date,reason,status)
                                VALUES (?,?,?,?,?,?,?)
                                """,
                                (
                                    username,
                                    org,
                                    admin_branch,
                                    lv_start.strftime("%Y-%m-%d"),
                                    lv_end.strftime("%Y-%m-%d"),
                                    clean_reason,
                                    "pending",
                                ),
                            )
                            conn.commit()
                            log_action(conn, username, "REQUEST LEAVE", username, org)
                            st.session_state["_reset_admin_leave_form"] = True
                            refresh_with_message("Leave request submitted.")

        with tab_branch:
            status_filter = nav_selectbox(
                "Status",
                ["All", "pending", "approved", "rejected", "reapply"],
                key="admin_leave_status_filter",
            )

            leaves_df = safe_read(
                """
                SELECT id, username, start_date, end_date, reason, status, approved_by, admin_note, reviewed_at
                FROM leaves
                WHERE organization=? AND branch=?
                ORDER BY id DESC
                """,
                conn,
                params=(org, admin_branch),
            )

            if leaves_df.empty:
                st.info("No leave requests in this branch.")
            else:
                if status_filter != "All":
                    leaves_df = leaves_df[leaves_df["status"].astype(str).str.lower() == status_filter]
                display_cols = [
                    c for c in [
                        "id", "username", "start_date", "end_date", "reason",
                        "status", "approved_by", "admin_note", "reviewed_at"
                    ] if c in leaves_df.columns
                ]
                st.dataframe(leaves_df[display_cols], use_container_width=True)

                pending_df = leaves_df[leaves_df["status"].astype(str).str.lower() == "pending"].copy()
                if pending_df.empty:
                    st.info("No pending leave requests in this branch.")
                elif hr_handles_leave:
                    st.info("HR mode is ON. Branch managers can view leave requests here, but HR reviews them and submits the final recommendation to super admin.")
                else:
                    st.markdown("### Review Pending Leave Requests")
                    for _, leave_row in pending_df.iterrows():
                        leave_id = int(leave_row["id"])
                        leave_user = str(leave_row.get("username", ""))
                        leave_dates = f"{str(leave_row.get('start_date', ''))[:10]} to {str(leave_row.get('end_date', ''))[:10]}"
                        leave_reason = str(leave_row.get("reason", "") or "No reason provided.")

                        with st.expander(f"{leave_user} | {leave_dates}"):
                            st.write(leave_reason)
                            admin_note = st.text_area(
                                "Admin note",
                                key=f"admin_leave_note_{leave_id}",
                                help="This note is visible in leave records and helps super admin review the decision.",
                            )
                            ac1, ac2, ac3 = st.columns(3)
                            if ac1.button("Approve Leave", key=f"admin_leave_approve_{leave_id}"):
                                conn.execute(
                                    """
                                    UPDATE leaves
                                    SET status='approved', approved_by=?, admin_note=?, reviewed_at=datetime('now')
                                    WHERE id=? AND organization=? AND branch=?
                                    """,
                                    (username, admin_note.strip(), leave_id, org, admin_branch),
                                )
                                conn.commit()
                                log_action(conn, username, "APPROVE LEAVE", leave_user, org)
                                refresh_with_message(f"Leave approved for {leave_user}.")
                            if ac2.button("Reject Leave", key=f"admin_leave_reject_{leave_id}"):
                                conn.execute(
                                    """
                                    UPDATE leaves
                                    SET status='rejected', approved_by=?, admin_note=?, reviewed_at=datetime('now')
                                    WHERE id=? AND organization=? AND branch=?
                                    """,
                                    (username, admin_note.strip(), leave_id, org, admin_branch),
                                )
                                conn.commit()
                                log_action(conn, username, "REJECT LEAVE", leave_user, org)
                                refresh_with_message(f"Leave rejected for {leave_user}.", level="warning")
                            if ac3.button("Request Reapply", key=f"admin_leave_reapply_{leave_id}"):
                                conn.execute(
                                    """
                                    UPDATE leaves
                                    SET status='reapply', approved_by=?, admin_note=?, reviewed_at=datetime('now')
                                    WHERE id=? AND organization=? AND branch=?
                                    """,
                                    (username, admin_note.strip(), leave_id, org, admin_branch),
                                )
                                conn.commit()
                                log_action(conn, username, "REQUEST LEAVE REAPPLY", leave_user, org)
                                refresh_with_message(f"Reapply requested from {leave_user}.", level="info")

    # =====================================================
    # ALERTS
    # =====================================================
    elif menu == "Alerts":
        st.subheader("Alerts")
        st.caption("Read-only branch alerts and auto-warnings from super admin/system.")

        alerts_df = safe_read(
            """
            SELECT id, username, type, message, created_at
            FROM warnings
            WHERE organization=? AND branch=?
            ORDER BY created_at DESC
            """,
            conn,
            params=(org, admin_branch),
        )

        if alerts_df.empty:
            st.info("No alerts available.")
        else:
            key_terms = [
                "late", "lateness", "performance", "conflict", "absent", "low", "branch",
                "non-improving", "manager", "risk", "pattern",
            ]

            mask = alerts_df["type"].astype(str).str.lower().apply(
                lambda t: any(k in t for k in key_terms)
            ) | alerts_df["message"].astype(str).str.lower().apply(
                lambda m: any(k in m for k in key_terms)
            )

            alert_view = alerts_df[mask]
            if alert_view.empty:
                st.info("No system-level alerts found. Showing all warnings below.")
                alert_view = alerts_df

            st.dataframe(alert_view, use_container_width=True)

    # =====================================================
    # WARNINGS
    # =====================================================
    elif menu == "Warnings":
        st.subheader("Warnings")
        st.caption("Send admin warnings to employees and review warning history.")

        if hr_handles_discipline:
            st.info("HR mode is ON. Formal discipline and warning actions are managed from the HR dashboard.")

        employees_df = safe_read(
            "SELECT username FROM users WHERE organization=? AND branch=? AND role='employee' ORDER BY username",
            conn,
            params=(org, admin_branch),
        )

        if employees_df.empty:
            st.info("No employees available for warning.")
        elif not hr_handles_discipline:
            with st.form("admin_warn_form", clear_on_submit=False):
                target = st.selectbox("Employee", employees_df["username"].tolist(), key="admin_warn_target")
                warn_type = st.selectbox(
                    "Warning Type",
                    ["lateness", "absenteeism", "low_performance", "misconduct", "policy_violation", "other"],
                    key="admin_warn_type",
                )
                warn_msg = st.text_area("Warning Message", key="admin_warn_message")
                warn_sub = st.form_submit_button("Send Warning")

                if warn_sub:
                    clean_warn_msg = warn_msg.strip()
                    if not clean_warn_msg:
                        st.error("Warning message is required.")
                    else:
                        full_warn_msg = f"[From {username}] {clean_warn_msg}"
                        recent_dup = safe_read(
                            """
                            SELECT id FROM warnings
                            WHERE username=? AND organization=? AND branch=? AND type=? AND message=?
                              AND created_at >= datetime('now', '-2 minutes')
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            conn,
                            params=(target, org, admin_branch, warn_type, full_warn_msg),
                        )
                        if not recent_dup.empty:
                            refresh_with_message(
                                f"Duplicate warning blocked for {target}. The same warning was already sent just now.",
                                level="warning",
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO warnings(username,organization,branch,type,message,created_at)
                                VALUES (?,?,?,?,?,datetime('now'))
                                """,
                                (target, org, admin_branch, warn_type, full_warn_msg),
                            )
                            conn.commit()
                            log_action(conn, username, "SEND WARNING", target, org)
                            st.session_state["admin_warn_type"] = "lateness"
                            st.session_state["admin_warn_message"] = ""
                            refresh_with_message(f"Warning sent to {target}.")

        history = safe_read(
            """
            SELECT id, username, type, message, created_at
            FROM warnings
            WHERE organization=? AND branch=?
            ORDER BY created_at DESC
            """,
            conn,
            params=(org, admin_branch),
        )

        if not history.empty:
            st.divider()
            st.markdown("### Warning History")
            st.dataframe(history, use_container_width=True)

    # =====================================================
    # RATE
    # =====================================================
    elif menu == "Rate":
        st.subheader("Rate")

        if int(settings.get("rating_open", 1)) == 0:
            st.error("Ratings are currently locked.")
            return

        peers = safe_read(
            "SELECT username FROM users WHERE organization=? AND branch=? ORDER BY username",
            conn,
            params=(org, admin_branch),
        )
        peer_users = [u for u in peers["username"].tolist() if u != username] if not peers.empty else []

        if not peer_users:
            st.info("No peers available for rating.")
            return

        target = st.selectbox("Select Person", peer_users)

        already = safe_read(
            """
            SELECT COUNT(*) AS c
            FROM ratings
            WHERE rater=? AND rated=? AND organization=? AND branch=? AND date(created_at)=date('now')
            """,
            conn,
            params=(username, target, org, admin_branch),
        )

        if not already.empty and int(already.iloc[0]["c"]) > 0:
            st.warning("You already rated this person today.")
            return

        topics_df = safe_read("SELECT topic FROM topics ORDER BY topic", conn)
        if topics_df.empty:
            st.warning("No topics available. Ask super admin to set topics.")
            return

        with st.form("admin_rate_form", clear_on_submit=False):
            scores = {}
            for t in topics_df["topic"].tolist():
                scores[t] = st.slider(t, 0, 100, 0)

            rate_sub = st.form_submit_button("Submit Rating")
            if rate_sub:
                for topic, score in scores.items():
                    conn.execute(
                        """
                        INSERT INTO ratings(rater,rated,topic,score,branch,organization,created_at)
                        VALUES (?,?,?,?,?,?,datetime('now'))
                        """,
                        (username, target, topic, int(score), admin_branch, org),
                    )
                conn.commit()
                log_action(conn, username, "RATE USER", target, org)
                refresh_with_message("Rating submitted.")

    # =====================================================
    # MY SCORE
    # =====================================================
    elif menu == "My Score":
        st.subheader("My Score")

        range_sel = nav_selectbox(
            "Performance Range",
            ["Day", "Week", "Month", "Custom (Sidebar Date Filter)"],
            key="admin_my_score_range",
        )

        mine = safe_read(
            """
            SELECT topic, score, created_at
            FROM ratings
            WHERE rated=? AND organization=? AND branch=?
            """,
            conn,
            params=(username, org, admin_branch),
        )

        branch_scores = safe_read(
            """
            SELECT score, created_at
            FROM ratings
            WHERE organization=? AND branch=?
            """,
            conn,
            params=(org, admin_branch),
        )

        if range_sel == "Custom (Sidebar Date Filter)":
            mine = apply_date_range(mine, "created_at", "Custom", start_date, end_date)
            branch_scores = apply_date_range(branch_scores, "created_at", "Custom", start_date, end_date)
        else:
            mine = apply_date_range(mine, "created_at", range_sel, start_date, end_date)
            branch_scores = apply_date_range(branch_scores, "created_at", range_sel, start_date, end_date)

        if mine.empty:
            st.info("No personal score data in selected range.")
        else:
            topic_avg = mine.groupby("topic")["score"].mean().sort_values(ascending=False)
            st.bar_chart(topic_avg)

            my_avg = float(topic_avg.mean())
            branch_avg = float(branch_scores["score"].mean()) if not branch_scores.empty else 0.0

            c1, c2 = st.columns(2)
            c1.metric("My Score", round(my_avg, 1))
            c2.metric("Branch Score", round(branch_avg, 1))

            if my_avg >= 85:
                st.success("Strong performance. Keep it up.")
            elif my_avg >= 60:
                st.info("Good progress. Keep improving consistency.")
            else:
                st.warning("Performance needs attention. Focus on low topics.")

    # =====================================================
    # KPI & SERVICE
    # =====================================================
    elif menu == "KPI & Service":
        st.subheader("KPI & Service Board")
        st.caption("Track branch goals, progress updates, and customer experience in one place.")

        kpi_weight = float(kpi_ai_config.get("kpi_weight_pct", 60.0) or 60.0)
        service_weight = float(kpi_ai_config.get("service_weight_pct", 40.0) or 40.0)
        warning_threshold = float(kpi_ai_config.get("warning_health_score", 65.0) or 65.0)
        critical_threshold = float(kpi_ai_config.get("critical_health_score", 45.0) or 45.0)
        low_star_threshold = float(kpi_ai_config.get("low_star_threshold", 3.4) or 3.4)
        min_feedback_count = int(kpi_ai_config.get("min_feedback_count", 3) or 3)

        kpi_df = safe_read(
            """
            SELECT id, branch, scope_type, target_role, target_username, metric_name, target_value,
                   current_value, unit, period, priority, status, due_date, note, created_by, updated_by, created_at, updated_at
            FROM kpi_targets
            WHERE organization=?
              AND (trim(coalesce(branch,''))='' OR lower(trim(coalesce(branch,'')))=lower(trim(?)))
              AND lower(coalesce(status,'active'))!='archived'
            ORDER BY CASE WHEN lower(coalesce(status,'active'))='active' THEN 0 ELSE 1 END, due_date ASC, id DESC
            """,
            conn,
            params=(org, admin_branch),
        )

        feedback_df = safe_read(
            """
            SELECT feedback_scope, target_username, stars, message, created_at
            FROM client_feedback
            WHERE organization=? AND branch=?
            ORDER BY id DESC
            """,
            conn,
            params=(org, admin_branch),
        )

        if kpi_df.empty:
            st.info("No KPI goals assigned to this branch yet. The super admin can create them from the KPI & Service AI area.")
        else:
            kpi_view = kpi_df.copy()
            kpi_view["target_value"] = pd.to_numeric(kpi_view["target_value"], errors="coerce").fillna(0)
            kpi_view["current_value"] = pd.to_numeric(kpi_view["current_value"], errors="coerce").fillna(0)
            kpi_view["completion_pct"] = kpi_view.apply(
                lambda row: round((float(row["current_value"]) / float(row["target_value"]) * 100), 1) if float(row["target_value"] or 0) > 0 else 0.0,
                axis=1,
            )

            avg_completion = float(kpi_view["completion_pct"].mean()) if not kpi_view.empty else 0.0
            due_soon = int((pd.to_datetime(kpi_view["due_date"], errors="coerce") <= (pd.Timestamp.now() + pd.Timedelta(days=7))).fillna(False).sum()) if "due_date" in kpi_view.columns else 0
            avg_stars = float(pd.to_numeric(feedback_df["stars"], errors="coerce").mean()) if not feedback_df.empty else None
            feedback_count = int(len(feedback_df))
            completion_score = max(0.0, min(100.0, avg_completion))
            service_score = None if avg_stars is None else max(0.0, min(100.0, (float(avg_stars) / 5.0) * 100.0))
            health_score = completion_score if service_score is None else ((completion_score * kpi_weight) + (service_score * service_weight)) / max(1.0, (kpi_weight + service_weight))

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Open KPIs", len(kpi_view))
            c2.metric("Avg Completion", f"{avg_completion:.0f}%")
            c3.metric("Guest Stars", f"{avg_stars:.1f}" if avg_stars is not None else "-")
            c4.metric("Due Soon", due_soon)
            c5.metric("Health Score", f"{health_score:.0f}/100")

            st.markdown("### AI Branch Coach")
            if feedback_count >= min_feedback_count and (health_score <= critical_threshold or (avg_stars is not None and avg_stars <= low_star_threshold and avg_completion < warning_threshold)):
                st.error(f"AI escalation: branch health is {health_score:.0f}/100 with {feedback_count} feedback entries. Run immediate manager coaching, service recovery, and KPI reset.")
            elif avg_stars is not None and avg_stars < 3.5 and avg_completion < 60:
                st.error(f"AI alert: {admin_branch} is soft on both execution ({avg_completion:.0f}% KPI completion) and guest satisfaction ({avg_stars:.1f} stars). Run urgent coaching and service follow-up.")
            elif avg_stars is not None and avg_stars >= 4.5 and avg_completion >= 85:
                st.success(f"AI strength signal: {admin_branch} is operating strongly with {avg_completion:.0f}% KPI completion and {avg_stars:.1f} guest stars. Consider using this branch as a benchmark.")
            elif health_score <= warning_threshold:
                st.warning(f"AI watchlist: branch health is {health_score:.0f}/100. Push weekly accountability and service consistency before risk deepens.")
            elif avg_completion < 60:
                st.warning(f"AI coach: KPI execution is behind at about {avg_completion:.0f}%. Focus on the overdue goals and weekly reviews.")
            elif avg_stars is not None and avg_stars < 3.8:
                st.warning(f"AI service note: guest sentiment is trending low at {avg_stars:.1f} stars. Review customer-facing routines and response speed.")
            else:
                st.info("AI coach: maintain the current branch rhythm and keep improving both KPI follow-through and guest experience.")

            st.markdown("### Branch KPI Board")
            st.dataframe(
                kpi_view[[c for c in ["metric_name", "scope_type", "target_role", "target_username", "current_value", "target_value", "unit", "completion_pct", "priority", "status", "due_date"] if c in kpi_view.columns]],
                use_container_width=True,
                hide_index=True,
            )

            progress_labels = [
                f"#{int(row['id'])} | {row['metric_name']} | {row['target_username'] or row['target_role']}"
                for _, row in kpi_view.iterrows()
            ]
            selected_label = st.selectbox("Update KPI progress", progress_labels, key="admin_kpi_pick")
            selected_row = kpi_view.iloc[progress_labels.index(selected_label)]
            with st.form("admin_kpi_progress_form", clear_on_submit=False):
                progress_value = st.number_input(
                    "Current value",
                    min_value=0.0,
                    value=float(selected_row.get("current_value", 0) or 0),
                    step=1.0,
                )
                progress_note = st.text_area("Progress note", value=str(selected_row.get("note", "") or ""))
                progress_status = st.selectbox(
                    "Status",
                    ["active", "at_risk", "completed"],
                    index=["active", "at_risk", "completed"].index(str(selected_row.get("status", "active") or "active")) if str(selected_row.get("status", "active") or "active") in ["active", "at_risk", "completed"] else 0,
                )
                if st.form_submit_button("Save Branch KPI Update"):
                    conn.execute(
                        """
                        UPDATE kpi_targets
                        SET current_value=?, note=?, status=?, updated_by=?, updated_at=datetime('now')
                        WHERE id=? AND organization=?
                        """,
                        (float(progress_value), progress_note.strip(), progress_status, username, int(selected_row.get("id", 0) or 0), org),
                    )
                    conn.execute(
                        """
                        INSERT INTO kpi_progress_updates(kpi_id, organization, branch, target_username, progress_value, progress_note, updated_by, created_at)
                        VALUES(?,?,?,?,?,?,?,datetime('now'))
                        """,
                        (
                            int(selected_row.get("id", 0) or 0),
                            org,
                            admin_branch,
                            str(selected_row.get("target_username", "") or ""),
                            float(progress_value),
                            progress_note.strip(),
                            username,
                        ),
                    )
                    conn.commit()
                    log_action(conn, username, "UPDATE KPI PROGRESS", str(selected_row.get("metric_name", "KPI")), org)
                    refresh_with_message("KPI progress updated.")

            st.markdown("### Customer Feedback Link")
            if feedback_df.empty:
                st.info("No customer feedback has been captured for this branch yet.")
            else:
                feedback_df["stars"] = pd.to_numeric(feedback_df["stars"], errors="coerce").fillna(0)
                service_summary = (
                    feedback_df[feedback_df["feedback_scope"].astype(str) == "individual"]
                    .groupby("target_username", dropna=False)
                    .agg(feedback_count=("stars", "count"), avg_stars=("stars", "mean"))
                    .reset_index()
                    .rename(columns={"target_username": "staff"})
                    .sort_values(["avg_stars", "feedback_count"], ascending=[False, False])
                )
                service_summary["avg_stars"] = service_summary["avg_stars"].round(1)
                if not service_summary.empty:
                    st.dataframe(service_summary, use_container_width=True, hide_index=True)
                st.dataframe(feedback_df[[c for c in ["created_at", "feedback_scope", "target_username", "stars", "message"] if c in feedback_df.columns]], use_container_width=True, hide_index=True)

    # =====================================================
    # ANALYTICS
    # =====================================================
    elif menu == "Analytics":
        st.subheader("Branch Analytics (Leadership View)")
        st.caption("Shows branch performance and top performers only.")

        range_sel = nav_selectbox(
            "Range",
            ["Day", "Week", "Month", "Custom (Sidebar Date Filter)"],
            key="admin_analytics_range",
        )

        ratings = safe_read(
            """
            SELECT rated, topic, score, created_at
            FROM ratings
            WHERE organization=? AND branch=?
            """,
            conn,
            params=(org, admin_branch),
        )

        if range_sel == "Custom (Sidebar Date Filter)":
            ratings = apply_date_range(ratings, "created_at", "Custom", start_date, end_date)
        else:
            ratings = apply_date_range(ratings, "created_at", range_sel, start_date, end_date)

        if ratings.empty:
            st.info("No rating data in selected range.")
            return

        per_user = ratings.groupby("rated")["score"].mean().sort_values(ascending=False)
        top_df = per_user.head(5).reset_index().rename(columns={"rated": "User", "score": "Avg Score"})
        active_rated_users = int(per_user.shape[0])

        c1, c2, c3 = st.columns(3)
        c1.metric("Branch Average", round(float(ratings["score"].mean()), 1))
        c2.metric("Top Performer", str(top_df.iloc[0]["User"]) if not top_df.empty else "N/A")
        c3.metric("Users Rated", active_rated_users)

        st.markdown("### Top Performers")
        st.dataframe(top_df, use_container_width=True)

        st.markdown("### Topic Performance")
        topic_avg = ratings.groupby("topic")["score"].mean().sort_values(ascending=False)
        st.bar_chart(topic_avg)

        ratings_plot = ratings.copy()
        ratings_plot["created_at"] = pd.to_datetime(ratings_plot["created_at"], errors="coerce")
        daily = ratings_plot.groupby(ratings_plot["created_at"].dt.date)["score"].mean()
        st.markdown("### Branch Trend")
        st.line_chart(daily)

    # =====================================================
    # BADGES
    # =====================================================
    elif menu == "Badges":
        st.subheader("🏅 Organization & Branch Badges")
        st.caption("Visible badge holders update automatically with performance changes.")

        badge_payload = compute_badges_for_organization(org)
        badges = badge_payload.get("badges", [])
        if not badges:
            st.info("No badge data available yet.")
        else:
            badge_df = pd.DataFrame(badges)
            if "badge" in badge_df.columns:
                badge_df["icon"] = badge_df["badge"].astype(str).apply(get_badge_icon)
            if "holder" in badge_df.columns and "icon" in badge_df.columns:
                badge_df["holder"] = badge_df["holder"].astype(str) + " " + badge_df["icon"].astype(str)
            branch_badges = badge_df[(badge_df["scope"] == "organization") | (badge_df["branch"].astype(str) == str(admin_branch))]
            show_cols = [c for c in ["badge", "holder", "scope", "branch", "score", "rating_count"] if c in branch_badges.columns]
            st.dataframe(branch_badges[show_cols], use_container_width=True)

            summary = badge_payload.get("summary", {})
            c1, c2 = st.columns(2)
            c1.metric("Total Active Badges", summary.get("total_badges", 0))
            c2.metric("Unique Badge Holders", summary.get("unique_holders", 0))

    # =====================================================
    # TOPICS
    # =====================================================
    elif menu == "Topics":
        st.subheader("Topics")

        topics = safe_read("SELECT id, topic FROM topics ORDER BY topic", conn)
        st.dataframe(topics if not topics.empty else pd.DataFrame({"Info": ["No topics available"]}), use_container_width=True)

        t1, t2, t3 = st.tabs(["Add", "Edit", "Delete"])

        with t1:
            with st.form("admin_add_topic", clear_on_submit=False):
                topic_new = st.text_input("New Topic")
                add_sub = st.form_submit_button("Add Topic")
                if add_sub:
                    if not topic_new.strip():
                        st.error("Topic is required.")
                    else:
                        exists = safe_read("SELECT id FROM topics WHERE topic=?", conn, params=(topic_new.strip(),))
                        if not exists.empty:
                            st.error("Topic already exists.")
                        else:
                            conn.execute("INSERT INTO topics(topic) VALUES(?)", (topic_new.strip(),))
                            conn.commit()
                            log_action(conn, username, "ADD TOPIC", topic_new.strip(), org)
                            refresh_with_message("Topic added.")

        with t2:
            if topics.empty:
                st.info("No topics to edit.")
            else:
                with st.form("admin_edit_topic", clear_on_submit=False):
                    old_topic = st.selectbox("Select Topic", topics["topic"].tolist(), key="admin_topic_edit_select")
                    new_topic = st.text_input("New Name")
                    edit_sub = st.form_submit_button("Save")
                    if edit_sub:
                        if not new_topic.strip():
                            st.error("New topic name is required.")
                        else:
                            conn.execute("UPDATE topics SET topic=? WHERE topic=?", (new_topic.strip(), old_topic))
                            conn.commit()
                            log_action(conn, username, "EDIT TOPIC", f"{old_topic} -> {new_topic.strip()}", org)
                            refresh_with_message("Topic updated.")

        with t3:
            if topics.empty:
                st.info("No topics to delete.")
            else:
                with st.form("admin_delete_topic", clear_on_submit=False):
                    del_topic = st.selectbox("Select Topic", topics["topic"].tolist(), key="admin_topic_delete_select")
                    del_sub = st.form_submit_button("Delete Topic")
                    if del_sub:
                        conn.execute("DELETE FROM topics WHERE topic=?", (del_topic,))
                        conn.commit()
                        log_action(conn, username, "DELETE TOPIC", del_topic, org)
                        refresh_with_message("Topic deleted.", level="warning")

    # =====================================================
    # MESSAGES
    # =====================================================
    elif menu == "Messages":
        st.subheader("Message Super Admin")
        st.caption("Send one message at a time to management. Replies from super admin appear below.")

        incoming_df = safe_read(
            """
            SELECT sender, message, created_at
            FROM messages
            WHERE receiver=? AND organization=? AND branch=?
            ORDER BY id DESC
            """,
            conn,
            params=(username, org, admin_branch),
        )

        if not incoming_df.empty:
            st.markdown("### Replies from Management")
            st.dataframe(incoming_df, use_container_width=True)

        with st.form("admin_message_form", clear_on_submit=True):
            msg = st.text_area("Message")
            send = st.form_submit_button("Send to Management")
            if send:
                clean_msg = msg.strip()
                if not clean_msg:
                    st.error("Message cannot be empty.")
                elif is_recent_duplicate_message(conn, username, "management", org, admin_branch, clean_msg):
                    refresh_with_message("Duplicate message blocked. The same message was already sent just now.", level="warning")
                else:
                    conn.execute(
                        """
                        INSERT INTO messages(sender,receiver,branch,organization,message,created_at)
                        VALUES (?,?,?,?,?,datetime('now'))
                        """,
                        (username, "management", admin_branch, org, clean_msg),
                    )
                    conn.commit()
                    log_action(conn, username, "SEND MESSAGE", "management", org)
                    refresh_with_message("Message sent.")

        sent_df = safe_read(
            """
            SELECT receiver, message, created_at
            FROM messages
            WHERE sender=? AND organization=? AND branch=?
            ORDER BY created_at DESC
            """,
            conn,
            params=(username, org, admin_branch),
        )

        if not sent_df.empty:
            st.markdown("### Sent Messages")
            st.dataframe(sent_df, use_container_width=True)

    # =====================================================
    # POLLS
    # =====================================================
    elif menu == "Polls":
        st.subheader("Branch Polls")
        st.caption("One question per line creates a step-by-step poll flow. Each person can answer once only, then the next question appears automatically.")
        st.info(
            "🔒 Privacy Notice: responses are not exposed to peers or ordinary staff. "
            "For fully anonymous polls, even the Managing Director / super admin cannot identify the responder. "
            "If anonymity is disabled for an investigation poll, identity remains hidden from branch viewers and is visible only to the Managing Director / super admin."
        )

        with st.form("admin_create_poll", clear_on_submit=True):
            poll_questions = st.text_area(
                "Poll Question(s)",
                placeholder="Example:\nWho moved the wall clock?\nDid you see who took it?",
            )
            allow_custom = st.checkbox("Allow custom typed answers", value=True)
            use_deadline = st.checkbox("Set deadline / expiry", value=False, key="admin_poll_use_deadline")
            exp_col1, exp_col2 = st.columns(2)
            with exp_col1:
                expiry_date = st.date_input(
                    "Expiry Date",
                    value=date.today() + timedelta(days=1),
                    disabled=not use_deadline,
                    key="admin_poll_expiry_date",
                )
            with exp_col2:
                expiry_time = st.time_input(
                    "Expiry Time",
                    value=time(18, 0),
                    disabled=not use_deadline,
                    key="admin_poll_expiry_time",
                )
            poll_submit = st.form_submit_button("Create Poll")
            if poll_submit:
                expiry_value = ""
                if use_deadline:
                    expiry_value = datetime.combine(expiry_date, expiry_time).strftime("%Y-%m-%d %H:%M:%S")
                ok, message, poll_ids = create_poll_batch(
                    conn,
                    org,
                    poll_questions,
                    username,
                    "admin",
                    branch=admin_branch,
                    anonymous=True,
                    allow_custom=allow_custom,
                    expires_at=expiry_value,
                )
                if ok:
                    log_action(conn, username, "CREATE POLL", f"{admin_branch} | poll_ids={','.join(map(str, poll_ids))}", org)
                    refresh_with_message(message)
                else:
                    st.error(message)

        st.divider()
        st.markdown("### Answer Polls")
        open_polls_df = get_visible_polls(
            conn,
            org,
            viewer_branch=admin_branch,
            viewer_role="admin",
            include_closed=False,
        )

        poll_records = open_polls_df.to_dict("records") if not open_polls_df.empty else []
        answered_items = []
        unanswered_items = []
        for row in poll_records:
            existing = get_user_poll_response(conn, int(row.get("id", 0)), username)
            if existing:
                answered_items.append((row, existing))
            else:
                unanswered_items.append(row)

        if poll_records:
            pm1, pm2, pm3 = st.columns(3)
            pm1.metric("Total Questions", len(poll_records))
            pm2.metric("Answered", len(answered_items))
            pm3.metric("Remaining", len(unanswered_items))

        if unanswered_items:
            current_poll = unanswered_items[0]
            poll_id = int(current_poll["id"])
            total_questions = len(poll_records)
            current_number = len(answered_items) + 1
            scope_label = "All Branches" if not str(current_poll.get("branch", "")).strip() else str(current_poll.get("branch", ""))
            anonymous_flag = int(current_poll.get("anonymous", 1)) == 1
            privacy_label = "Anonymous to peers, managers, and MD" if anonymous_flag else "Hidden from branch viewers; visible only to MD if needed"
            expires_text = str(current_poll.get("expires_at", "") or "").strip()
            options = ["Yes", "No"] + (["Custom"] if int(current_poll.get("allow_custom", 1)) == 1 else [])

            st.markdown(f"### Current Question ({current_number}/{total_questions})")
            st.write(str(current_poll.get("question", "")))
            meta_bits = [f"Scope: {scope_label}", f"Privacy: {privacy_label}"]
            if expires_text:
                meta_bits.append(f"Deadline: {expires_text[:16]}")
            st.caption(" | ".join(meta_bits))

            with st.form(f"admin_poll_vote_{poll_id}", clear_on_submit=True):
                answer_choice = st.radio(
                    "Your answer",
                    options,
                    key=f"admin_poll_choice_{poll_id}",
                )
                custom_answer = ""
                if answer_choice == "Custom":
                    custom_answer = st.text_input(
                        "Type custom answer / name / word",
                        key=f"admin_poll_custom_{poll_id}",
                    )
                submit_vote = st.form_submit_button("Submit Answer")
                if submit_vote:
                    ok, message = submit_poll_response(
                        conn,
                        poll_id,
                        org,
                        username,
                        "admin",
                        responder_branch=admin_branch,
                        answer_choice=answer_choice,
                        custom_answer=custom_answer,
                    )
                    if ok:
                        log_action(conn, username, "ANSWER POLL", f"poll_id={poll_id}", org)
                        refresh_with_message("Answer saved. Moving to the next question.")
                    else:
                        st.error(message)
        else:
            if poll_records:
                st.success("✅ Thank you. You have completed all poll questions.")
                st.info("Your answers have been recorded securely. Previous answers are locked and cannot be edited.")
            else:
                st.info("No open polls for this branch right now.")

        if answered_items:
            st.markdown("### Answered Questions (Locked)")
            for row, existing in answered_items:
                poll_id = int(row.get("id", 0))
                saved_answer = str(existing.get("custom_answer", "") or existing.get("response_choice", ""))
                with st.expander(f"#{poll_id} • {row.get('question', '')}"):
                    st.success(f"Submitted answer: {saved_answer}")
                    st.caption("Locked after submit. You cannot go back and change this answer.")

        st.divider()
        st.markdown("### Poll Results")
        all_polls_df = get_visible_polls(
            conn,
            org,
            viewer_branch=admin_branch,
            viewer_role="admin",
            include_closed=True,
        )

        if all_polls_df.empty:
            st.info("No polls created yet.")
        else:
            for _, poll_row in all_polls_df.iterrows():
                poll_id = int(poll_row["id"])
                scope_label = "All Branches" if not str(poll_row.get("branch", "")).strip() else str(poll_row.get("branch", ""))
                raw_status = str(poll_row.get("status", "open") or "open").strip().lower()
                is_expired = int(poll_row.get("is_expired", 0) or 0) == 1
                display_status = "Expired" if raw_status == "open" and is_expired else raw_status.title()
                results = get_poll_results(conn, poll_id, can_view_identities=False)
                with st.expander(f"#{poll_id} [{display_status}] {poll_row.get('question', '')}"):
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Yes", results.get("yes_count", 0))
                    m2.metric("No", results.get("no_count", 0))
                    m3.metric("Custom", results.get("custom_count", 0))
                    m4.metric("Total", results.get("total", 0))

                    chart_df = pd.DataFrame(
                        {"Responses": [results.get("yes_count", 0), results.get("no_count", 0), results.get("custom_count", 0)]},
                        index=["Yes", "No", "Custom"],
                    )
                    st.bar_chart(chart_df)

                    meta_bits = [
                        f"Scope: {scope_label}",
                        f"Created by: {poll_row.get('created_by', '-')}",
                        "Identity is hidden for admin/manager views.",
                    ]
                    expires_text = str(poll_row.get("expires_at", "") or "").strip()
                    if expires_text:
                        meta_bits.append(f"Deadline: {expires_text[:16]}")
                    st.caption(" | ".join(meta_bits))

                    summary_df = results.get("summary_breakdown")
                    if summary_df is not None and not summary_df.empty:
                        st.markdown("**Response Counts Table**")
                        st.dataframe(summary_df, use_container_width=True)

                    detail_df = results.get("detailed_breakdown")
                    if detail_df is not None and not detail_df.empty:
                        st.markdown("**All Answers Breakdown**")
                        st.dataframe(detail_df, use_container_width=True)

                    custom_df = results.get("custom_answers")
                    if custom_df is not None and not custom_df.empty:
                        st.markdown("**Custom Answers Only**")
                        st.dataframe(custom_df, use_container_width=True)
                    else:
                        st.info("No custom answers yet.")

                    if str(poll_row.get("created_by", "")).strip() == str(username):
                        action_col1, action_col2 = st.columns(2)
                        next_status = "closed" if raw_status == "open" else "open"
                        action_label = "Close Poll" if raw_status == "open" else "Reopen Poll"
                        if action_col1.button(action_label, key=f"admin_toggle_poll_{poll_id}"):
                            ok, message = set_poll_status(conn, poll_id, next_status)
                            if ok:
                                log_action(conn, username, action_label.upper(), f"poll_id={poll_id}", org)
                                refresh_with_message(message, level="warning" if next_status != "open" else "success")
                            else:
                                st.error(message)

                        archive_label = "Archive Poll" if raw_status != "archived" else "Restore Poll"
                        archive_next_status = "archived" if raw_status != "archived" else "open"
                        if action_col2.button(archive_label, key=f"admin_archive_poll_{poll_id}"):
                            ok, message = set_poll_status(conn, poll_id, archive_next_status)
                            if ok:
                                log_action(conn, username, archive_label.upper(), f"poll_id={poll_id}", org)
                                refresh_with_message(message, level="warning" if archive_next_status == "archived" else "success")
                            else:
                                st.error(message)

    # =====================================================
    # KIOSK
    # =====================================================
    elif menu == "Staff Check In":
        st.subheader("Staff Check In")
        st.caption("Branch admins can manage kiosk devices for their own branch only. Super admin still has full visibility and override.")

        kiosks_df = safe_read(
            """
            SELECT id, device_name, last_active, COALESCE(status, 'active') AS status
            FROM kiosks
            WHERE organization=? AND branch=?
            ORDER BY id DESC
            """,
            conn,
            params=(org, admin_branch),
        )

        if kiosks_df.empty:
            st.info("No kiosk devices registered for this branch.")
        else:
            st.dataframe(kiosks_df, use_container_width=True)

        st.markdown("### Branch Kiosk Link")
        st.code(build_kiosk_link(admin_branch, org), language="text")

        kc_tab, km_tab = st.tabs(["Create Kiosk", "Manage Kiosks"])

        with kc_tab:
            with st.form("admin_create_kiosk", clear_on_submit=True):
                kiosk_name = st.text_input("Device / Kiosk Label")
                kiosk_submit = st.form_submit_button("Create Kiosk")
                if kiosk_submit:
                    device_name = kiosk_name.strip() or f"{admin_branch}-Kiosk"
                    dup = safe_read(
                        "SELECT id FROM kiosks WHERE branch=? AND organization=? AND device_name=?",
                        conn,
                        params=(admin_branch, org, device_name),
                    )
                    if not dup.empty:
                        st.error("Kiosk name already exists for this branch.")
                    else:
                        conn.execute(
                            "INSERT INTO kiosks(branch,organization,device_name,last_active,status) VALUES(?,?,?,datetime('now'),'active')",
                            (admin_branch, org, device_name),
                        )
                        conn.commit()
                        log_action(conn, username, "CREATE KIOSK", device_name, org)
                        refresh_with_message(f"Kiosk '{device_name}' created for branch '{admin_branch}'.")

        with km_tab:
            if kiosks_df.empty:
                st.info("No kiosks to manage in this branch.")
            else:
                for _, kiosk_row in kiosks_df.iterrows():
                    kiosk_id = int(kiosk_row["id"])
                    kiosk_name = str(kiosk_row.get("device_name", "Unknown"))
                    kiosk_status = str(kiosk_row.get("status", "active") or "active")
                    kiosk_last_active = str(kiosk_row.get("last_active", "") or "")

                    with st.expander(f"{kiosk_name} [{kiosk_status}]"):
                        st.caption(f"Last active: {kiosk_last_active[:16] if kiosk_last_active else 'N/A'}")
                        st.code(build_kiosk_link(admin_branch, org), language="text")

                        mk1, mk2, mk3 = st.columns(3)
                        if mk1.button("Lock", key=f"admin_kiosk_lock_{kiosk_id}"):
                            conn.execute(
                                "UPDATE kiosks SET status='locked' WHERE id=? AND organization=? AND branch=?",
                                (kiosk_id, org, admin_branch),
                            )
                            conn.commit()
                            log_action(conn, username, "LOCK KIOSK", kiosk_name, org)
                            refresh_with_message(f"Kiosk '{kiosk_name}' locked.", level="warning")
                        if mk2.button("Unlock", key=f"admin_kiosk_unlock_{kiosk_id}"):
                            conn.execute(
                                "UPDATE kiosks SET status='active' WHERE id=? AND organization=? AND branch=?",
                                (kiosk_id, org, admin_branch),
                            )
                            conn.commit()
                            log_action(conn, username, "UNLOCK KIOSK", kiosk_name, org)
                            refresh_with_message(f"Kiosk '{kiosk_name}' unlocked.")
                        if mk3.button("Delete", key=f"admin_kiosk_delete_{kiosk_id}"):
                            conn.execute(
                                "DELETE FROM kiosks WHERE id=? AND organization=? AND branch=?",
                                (kiosk_id, org, admin_branch),
                            )
                            conn.commit()
                            log_action(conn, username, "DELETE KIOSK", kiosk_name, org)
                            refresh_with_message(f"Kiosk '{kiosk_name}' deleted.", level="warning")

                        with st.form(f"admin_edit_kiosk_{kiosk_id}", clear_on_submit=False):
                            new_kiosk_name = st.text_input("Rename Kiosk", value=kiosk_name, key=f"admin_kiosk_name_{kiosk_id}")
                            save_kiosk = st.form_submit_button("Save Changes")
                            if save_kiosk:
                                final_name = new_kiosk_name.strip() or kiosk_name
                                dup = safe_read(
                                    "SELECT id FROM kiosks WHERE branch=? AND organization=? AND device_name=? AND id<>?",
                                    conn,
                                    params=(admin_branch, org, final_name, kiosk_id),
                                )
                                if not dup.empty:
                                    st.error("Another kiosk in this branch already uses that name.")
                                else:
                                    conn.execute(
                                        "UPDATE kiosks SET device_name=? WHERE id=? AND organization=? AND branch=?",
                                        (final_name, kiosk_id, org, admin_branch),
                                    )
                                    conn.commit()
                                    log_action(conn, username, "EDIT KIOSK", f"{kiosk_name} -> {final_name}", org)
                                    refresh_with_message(f"Kiosk updated to '{final_name}'.")

    # =====================================================
    # SETTINGS
    # =====================================================
    elif menu == "Settings":
        st.subheader("Settings")

        with st.form("admin_settings_form", clear_on_submit=False):
            rating_open = st.toggle("Enable Ratings", value=bool(int(settings.get("rating_open", 1))))
            save_settings = st.form_submit_button("Save Rating Setting")
            if save_settings:
                conn.execute("UPDATE settings SET rating_open=? WHERE id=1", (int(rating_open),))
                conn.commit()
                log_action(conn, username, "UPDATE RATING LOCK", f"rating_open={int(rating_open)}", org)
                refresh_with_message("Rating setting updated.")

        st.markdown("### Employee Lateness Fine Deduction")
        st.caption("Admin can set or change the employee-only deduction amount here, and super admin must approve it before it becomes active. Admin and manager lateness should be handled by warnings and oversight instead of fines.")
        pending_request = fine_policy.get("pending_request") if isinstance(fine_policy, dict) else None
        current_amount = float(fine_policy.get("amount_per_hour", 0) or 0)
        if pending_request:
            st.warning(
                f"Pending request: KES {float(pending_request.get('requested_amount', 0) or 0):,.0f}/hour from {pending_request.get('requested_by', username)} on {str(pending_request.get('created_at', ''))[:16]}"
            )
        else:
            st.info(f"Current approved late fine amount: KES {current_amount:,.0f} per accumulated hour.")

        with st.form("admin_late_fine_request_form", clear_on_submit=False):
            proposed_amount = st.number_input(
                "Amount to deduct for each accumulated late hour (KES)",
                min_value=0.0,
                value=float(pending_request.get('requested_amount', current_amount) if pending_request else current_amount),
                step=50.0,
            )
            request_reason = st.text_area(
                "Reason / note for super admin approval",
                value=str(pending_request.get('reason', '') if pending_request else ''),
            )
            submit_fine_request = st.form_submit_button("Submit Fine Amount For Approval")
            if submit_fine_request:
                if not request_reason.strip():
                    st.error("Reason is required before sending a deduction amount for approval.")
                else:
                    existing_pending = safe_read(
                        "SELECT id FROM lateness_fine_requests WHERE organization=? AND status='pending' ORDER BY id DESC LIMIT 1",
                        conn,
                        params=(org,),
                    )
                    if existing_pending.empty:
                        conn.execute(
                            """
                            INSERT INTO lateness_fine_requests(
                                organization, branch, requested_by, requested_amount, currency, reason, status, created_at
                            )
                            VALUES(?,?,?,?,?,?, 'pending', datetime('now'))
                            """,
                            (org, admin_branch, username, float(proposed_amount), 'KES', request_reason.strip()),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE lateness_fine_requests
                            SET branch=?, requested_by=?, requested_amount=?, currency='KES', reason=?, status='pending', created_at=datetime('now'), reviewed_by='', review_note='', reviewed_at=''
                            WHERE id=?
                            """,
                            (admin_branch, username, float(proposed_amount), request_reason.strip(), int(existing_pending.iloc[0]['id'])),
                        )
                    conn.commit()
                    log_action(conn, username, "REQUEST LATENESS FINE AMOUNT", f"KES {float(proposed_amount):,.0f}/hour", org)
                    refresh_with_message("Lateness fine amount sent to super admin for approval.")

        st.divider()
        st.markdown("### Change Password")
        with st.form("admin_change_password", clear_on_submit=False):
            current = st.text_input("Current Password", type="password")
            new = st.text_input("New Password", type="password")
            confirm = st.text_input("Confirm New Password", type="password")
            pass_sub = st.form_submit_button("Update Password")

            if pass_sub:
                db_hash = str(user_row.iloc[0].get("password", ""))
                if not verify_password(current, db_hash):
                    st.error("Current password is incorrect.")
                elif len(new) < 4:
                    st.error("New password must be at least 4 characters.")
                elif new != confirm:
                    st.error("Passwords do not match.")
                else:
                    conn.execute(
                        "UPDATE users SET password=? WHERE username=? AND organization=?",
                        (hash_password(new), username, org),
                    )
                    conn.commit()
                    log_action(conn, username, "CHANGE PASSWORD", username, org)
                    refresh_with_message("Password updated.")
