import streamlit as st
import pandas as pd
from datetime import datetime, date, time, timedelta
from urllib.parse import quote
from database.db import get_connection, hash_password, verify_password, log_action
from Dashboards.ui_responsive import apply_responsive_ui, navigation_expander_open_default
from Analytics.badges import compute_badges_for_organization, get_badge_icon


# ==============================
# HELPERS
# ==============================
def refresh():
    st.session_state["_admin_refresh"] = st.session_state.get("_admin_refresh", 0) + 1
    st.rerun()


def safe_read(query, conn, params=None):
    try:
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

        

    if str(user_row.iloc[0].get("status", "active")).lower() == "suspended":
        st.error("Account suspended. Contact super admin.")
        return

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

    st.title("Admin Dashboard")
    st.caption(f"Manager: {username} | Branch: {admin_branch} | Organization: {org}")

    with st.expander("Navigation and Filter", expanded=navigation_expander_open_default()):
        date_range = st.date_input(
            "Select Range",
            value=(date.today(), date.today()),
            key="admin_sidebar_date",
        )
        menu = st.radio(
            "Menu",
            [
                "Profile",
                "Users",
                "Schedules",
                "Attendance",
                "Leaves",
                "Alerts",
                "Warnings",
                "Rate",
                "My Score",
                "Analytics",
                "Badges",
                "Topics",
                "Messages",
                "Staff Check In",
                "Settings",
            ],
            key="admin_menu",
        )

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

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

        tab_create, tab_edit, tab_suspend = st.tabs(["Create Employee", "Edit User", "Suspend / Activate"])

        with tab_create:
            st.info("Admins can create employees only. Only super admin can delete users.")
            with st.form("admin_create_user", clear_on_submit=False):
                new_user = st.text_input("Username")
                new_pass = st.text_input("Password", type="password")
                new_pin = st.text_input("PIN", value="1234")
                new_phone = st.text_input("Phone Number (required)")
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
                            "SELECT id FROM users WHERE username=?",
                            conn,
                            params=(new_user.strip(),),
                        )
                        if not exists.empty:
                            st.error("Username already exists.")
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
                                    new_phone.strip(),
                                ),
                            )
                            conn.commit()
                            log_action(conn, username, "CREATE EMPLOYEE", new_user.strip(), org)
                            st.success(f"Employee '{new_user}' created.")
                            refresh()

        with tab_edit:
            editable = users_df[users_df["username"] != username] if not users_df.empty else pd.DataFrame()
            if editable.empty:
                st.info("No editable users available.")
            else:
                selected_user = st.selectbox("Select User", editable["username"].tolist(), key="admin_edit_user_select")
                row = editable[editable["username"] == selected_user].iloc[0]

                with st.form("admin_edit_user_form", clear_on_submit=False):
                    new_pin_val = st.text_input("New PIN", value=str(row.get("pin", "1234")))
                    new_phone_val = st.text_input("Phone Number", value=str(row.get("phone", "") or ""))
                    reset_pass = st.text_input("Reset Password (optional)", type="password")
                    save_edit = st.form_submit_button("Save Changes")

                    if save_edit:
                        if not new_phone_val.strip():
                            st.error("Phone number is required.")
                            return
                        conn.execute(
                            "UPDATE users SET pin=?, phone=? WHERE username=? AND organization=? AND branch=?",
                            (new_pin_val.strip() or "1234", new_phone_val.strip(), selected_user, org, admin_branch),
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
                        st.success("User updated.")
                        refresh()

        with tab_suspend:
            manageable = users_df[users_df["username"] != username] if not users_df.empty else pd.DataFrame()
            if manageable.empty:
                st.info("No users to manage.")
            else:
                selected = st.selectbox("Select User", manageable["username"].tolist(), key="admin_suspend_user_select")
                row = manageable[manageable["username"] == selected].iloc[0]
                st.write(f"Current status: **{row['status']}**")

                c1, c2 = st.columns(2)
                if c1.button("Suspend User", key="admin_suspend_user_btn"):
                    conn.execute(
                        "UPDATE users SET status='suspended' WHERE username=? AND organization=? AND branch=?",
                        (selected, org, admin_branch),
                    )
                    conn.commit()
                    log_action(conn, username, "SUSPEND USER", selected, org)
                    st.warning(f"{selected} suspended.")
                    refresh()
                if c2.button("Activate User", key="admin_activate_user_btn"):
                    conn.execute(
                        "UPDATE users SET status='active' WHERE username=? AND organization=? AND branch=?",
                        (selected, org, admin_branch),
                    )
                    conn.commit()
                    log_action(conn, username, "ACTIVATE USER", selected, org)
                    st.success(f"{selected} activated.")
                    refresh()

        st.info("Delete user is disabled for admins. Only super admin can delete users.")

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
                st.success("Schedule saved.")
                refresh()

        if not schedules_df.empty:
            del_id = st.selectbox("Delete Schedule Row", schedules_df["id"].tolist(), key="delete_schedule_id")
            if st.button("Delete Schedule", key="delete_schedule_btn"):
                conn.execute(
                    "DELETE FROM schedules WHERE id=? AND organization=? AND branch=?",
                    (int(del_id), org, admin_branch),
                )
                conn.commit()
                log_action(conn, username, "DELETE SCHEDULE", str(del_id), org)
                st.warning("Schedule deleted.")
                refresh()

    # =====================================================
    # ATTENDANCE
    # =====================================================
    elif menu == "Attendance":
        st.subheader("Attendance")
        st.caption("Users clock in/out from kiosk. This view monitors lateness, absentism, and early clock-outs.")

        range_sel = st.selectbox(
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
                            st.success("Early clock-out approval saved.")
                            refresh()

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
                            st.success("Lateness approval saved.")
                            refresh()

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
                        if lr1.button("Approve Lateness", key=f"approve_lateness_request_{request_id}"):
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
                        if lr2.button("Reject Lateness", key=f"reject_lateness_request_{request_id}"):
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
            with st.form("admin_leave_request", clear_on_submit=False):
                lv_start = st.date_input("Start Date", value=date.today())
                lv_end = st.date_input("End Date", value=date.today())
                lv_reason = st.text_area("Reason")
                lv_sub = st.form_submit_button("Submit Leave Request")

                if lv_sub:
                    if lv_end < lv_start:
                        st.error("End date cannot be before start date.")
                    elif not lv_reason.strip():
                        st.error("Reason is required.")
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
                                lv_reason.strip(),
                                "pending",
                            ),
                        )
                        conn.commit()
                        log_action(conn, username, "REQUEST LEAVE", username, org)
                        st.success("Leave request submitted.")
                        refresh()

        with tab_branch:
            status_filter = st.selectbox(
                "Status",
                ["All", "pending", "approved", "rejected", "reapply"],
                key="admin_leave_status_filter",
            )

            leaves_df = safe_read(
                """
                SELECT id, username, start_date, end_date, reason, status
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
                st.dataframe(leaves_df, use_container_width=True)
                st.info("Branch admins can view leave requests and statuses but cannot approve/reject.")

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

        employees_df = safe_read(
            "SELECT username FROM users WHERE organization=? AND branch=? AND role='employee' ORDER BY username",
            conn,
            params=(org, admin_branch),
        )

        if employees_df.empty:
            st.info("No employees available for warning.")
        else:
            with st.form("admin_warn_form", clear_on_submit=False):
                target = st.selectbox("Employee", employees_df["username"].tolist())
                warn_type = st.selectbox(
                    "Warning Type",
                    ["lateness", "absenteeism", "low_performance", "misconduct", "policy_violation", "other"],
                )
                warn_msg = st.text_area("Warning Message")
                warn_sub = st.form_submit_button("Send Warning")

                if warn_sub:
                    if not warn_msg.strip():
                        st.error("Warning message is required.")
                    else:
                        conn.execute(
                            """
                            INSERT INTO warnings(username,organization,branch,type,message,created_at)
                            VALUES (?,?,?,?,?,datetime('now'))
                            """,
                            (target, org, admin_branch, warn_type, f"[From {username}] {warn_msg.strip()}"),
                        )
                        conn.commit()
                        log_action(conn, username, "SEND WARNING", target, org)
                        st.success(f"Warning sent to {target}.")
                        refresh()

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
                st.success("Rating submitted.")
                refresh()

    # =====================================================
    # MY SCORE
    # =====================================================
    elif menu == "My Score":
        st.subheader("My Score")

        range_sel = st.selectbox(
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
    # ANALYTICS
    # =====================================================
    elif menu == "Analytics":
        st.subheader("Branch Analytics (Leadership View)")
        st.caption("Shows branch performance and top performers only.")

        range_sel = st.selectbox(
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
                            st.success("Topic added.")
                            refresh()

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
                            st.success("Topic updated.")
                            refresh()

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
                        st.warning("Topic deleted.")
                        refresh()

    # =====================================================
    # MESSAGES
    # =====================================================
    elif menu == "Messages":
        st.subheader("Message Super Admin")
        st.caption("Admins can send messages to management but cannot open/read inbox messages here.")

        with st.form("admin_message_form", clear_on_submit=False):
            msg = st.text_area("Message")
            send = st.form_submit_button("Send to Management")
            if send:
                if not msg.strip():
                    st.error("Message cannot be empty.")
                else:
                    conn.execute(
                        """
                        INSERT INTO messages(sender,receiver,branch,organization,message,created_at)
                        VALUES (?,?,?,?,?,datetime('now'))
                        """,
                        (username, "management", admin_branch, org, msg.strip()),
                    )
                    conn.commit()
                    log_action(conn, username, "SEND MESSAGE", "management", org)
                    st.success("Message sent.")

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
    # KIOSK
    # =====================================================
    elif menu == "Staff Check In":
        st.subheader("Staff Check In")
        st.caption("Each kiosk is tied to your branch only.")

        kiosks_df = safe_read(
            """
            SELECT id, device_name, last_active
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
                st.success("Rating setting updated.")
                refresh()

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
                    st.success("Password updated.")
