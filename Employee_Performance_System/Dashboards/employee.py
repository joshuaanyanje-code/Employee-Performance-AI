import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from database.db import get_connection, verify_password, hash_password, execute_write, execute_many_write
from Dashboards.ui_responsive import apply_responsive_ui, navigation_expander_open_default
from Analytics.badges import compute_badges_for_organization, build_holder_badge_map, decorate_username_with_badges

from Dashboards.ui_responsive import is_mobile_device
# ==============================
# REFRESH
# ==============================
def refresh():
    st.session_state["_r"] = st.session_state.get("_r", 0) + 1
    st.rerun()


def _safe_read(conn, query, params=None):
    try:
        if params is None:
            return pd.read_sql(query, conn)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()


def _annotate_attendance_with_lateness(att_df, lateness_df):
    if att_df.empty:
        return att_df

    out = att_df.copy()
    out["lateness_request_status"] = "not_requested"
    out["lateness_reason"] = ""
    out["lateness_admin_note"] = ""
    out["lateness_approved_by"] = ""
    out["approved_late"] = False
    out["true_late"] = out["status"].astype(str).str.upper() == "LATE"
    out["status_label"] = out["status"].astype(str)

    if lateness_df.empty:
        return out

    approvals = lateness_df.copy()
    approvals["approved_for_date"] = approvals["approved_for_date"].astype(str)
    lookup = {
        (str(row.get("username", "")), str(row.get("approved_for_date", ""))): row
        for _, row in approvals.iterrows()
    }

    for idx, row in out.iterrows():
        row_date = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(row_date):
            continue

        approval_row = lookup.get((str(row.get("username", "")), row_date.strftime("%Y-%m-%d")))
        if approval_row is None:
            continue

        request_status = str(approval_row.get("status", "pending"))
        out.at[idx, "lateness_request_status"] = request_status
        out.at[idx, "lateness_reason"] = str(approval_row.get("reason", ""))
        out.at[idx, "lateness_admin_note"] = str(approval_row.get("actual_reason", ""))
        out.at[idx, "lateness_approved_by"] = str(approval_row.get("approved_by", ""))

        if request_status.lower() in ["approved", "used"]:
            out.at[idx, "approved_late"] = True
            out.at[idx, "true_late"] = False
            base_status = str(row.get("status", "")).strip().upper()
            if base_status == "LATE":
                out.at[idx, "status_label"] = "APPROVED LATE"
            elif base_status:
                out.at[idx, "status_label"] = f"{base_status} (APPROVED LATE)"
            else:
                out.at[idx, "status_label"] = "APPROVED LATE"

    return out

# ==============================
# RECOMMENDATION ENGINE
# ==============================
def recommendation(score):
    if score >= 90:
        return "🔥 Elite level. Keep Pushing."
    elif score >= 75:
        return "👏 Strong performance. Stay consistent."
    elif score >= 55:
        return "⚠ Improve consistency."
    else:
        return "🚨 Serious improvement required."

# ==============================
# DASHBOARD
# ==============================
def employee_dashboard():

    apply_responsive_ui("default")

    conn = get_connection()
    username = st.session_state.get("username")
    org = st.session_state.get("organization")

    # ==============================
    # USER DATA (ORG SAFE)
    # ==============================
    user_data = pd.read_sql(
        "SELECT * FROM users WHERE username=? AND organization=?",
        conn,
        params=(username, org)
    )

    if user_data.empty:
        st.error("User not found")
        return

    if user_data.iloc[0]["status"] == "suspended":
        st.error("🚫 Account Suspended. Contact Admin.")
        return

    branch = user_data.iloc[0]["branch"]

    st.title("👨‍💼 Employee Dashboard")
    st.success(f"Welcome {username} | {branch}")

    # ==============================
    # NOTIFICATION COUNT
    # ==============================
    notif_count = pd.read_sql(
        "SELECT COUNT(*) as c FROM messages WHERE receiver=? AND branch=?",
        conn,
        params=(username, branch)
    )["c"].iloc[0]

    is_mobile = is_mobile_device()

    def _collapse_employee_mobile_nav():
        if is_mobile:
            st.session_state["employee_nav_open"] = False

    if "employee_nav_open" not in st.session_state:
        st.session_state["employee_nav_open"] = True

    page_items = [
        "Profile", "Schedule", "Attendance", "Leave",
        "Notifications", "Rate", "My Score",
        "Analytics", "Top Performers",
        "🏅 Badges", "Message Management", "Settings"
    ]

    if is_mobile:
        if st.button("Change Menu / Filter", key="employee_reopen_nav", use_container_width=True):
            st.session_state["employee_nav_open"] = True
            st.rerun()
        with st.expander("Navigation and Filter", expanded=bool(st.session_state.get("employee_nav_open", True))):
            date_range = st.date_input(
                "Select Range",
                value=(date.today(), date.today()),
                key="employee_date_range",
                on_change=_collapse_employee_mobile_nav,
            )
            page = st.radio(
                f"Menu 🔔({notif_count})",
                page_items,
                key="employee_menu",
                on_change=_collapse_employee_mobile_nav,
            )
    else:
        with st.sidebar:
            st.markdown("### Navigation")
            date_range = st.date_input(
                "Select Range",
                value=(date.today(), date.today()),
                key="employee_date_range",
            )
            page = st.radio(f"Menu 🔔({notif_count})", page_items, key="employee_menu")

    if isinstance(date_range, tuple):
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    # ==============================
    # FILTER FUNCTION
    # ==============================
    def filter_df(df):
        if "organization" in df.columns:
            df = df[df["organization"] == org]

        if "branch" in df.columns:
            df = df[df["branch"] == branch]

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df[
                (df["date"] >= pd.to_datetime(start_date)) &
                (df["date"] <= pd.to_datetime(end_date))
            ]
        return df

    # =====================================================
    # PROFILE
    # =====================================================
    if page == "Profile":
        manager_row = pd.read_sql(
            """
            SELECT username, role FROM users
            WHERE organization=? AND branch=? AND role IN ('admin','superadmin')
            ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END, id ASC
            LIMIT 1
            """,
            conn,
            params=(org, branch)
        )

        manager_name = manager_row.iloc[0]["username"] if not manager_row.empty else "Not assigned"

        off_days_df = pd.read_sql(
            """
            SELECT day FROM schedules
            WHERE username=? AND branch=? AND organization=? AND off_day=1
            ORDER BY day
            """,
            conn,
            params=(username, branch, org)
        )
        off_day_text = ", ".join(off_days_df["day"].astype(str).tolist()) if not off_days_df.empty else "Not set"

        st.subheader("My Profile")
        p1, p2 = st.columns(2)
        p1.info(f"**Name:** {username}")
        p1.info(f"**Branch:** {branch}")
        p2.info(f"**Branch Manager:** {manager_name}")
        p2.info(f"**Off Day(s):** {off_day_text}")
        st.success("Keep improving daily, adhere to rules and regulations, and stay consistent.")

    # =====================================================
    # SCHEDULE
    # =====================================================
    elif page == "Schedule":

        df = pd.read_sql(
            "SELECT * FROM schedules WHERE username=? AND branch=?",
            conn, params=(username, branch)
        )

        if df.empty:
            st.warning("No schedule")
        else:
            df_view = df.copy()
            if "off_day" in df_view.columns:
                df_view["off_day"] = df_view["off_day"].apply(lambda x: "Yes" if int(x) == 1 else "No")
            st.dataframe(df_view, use_container_width=True)

            if "off_day" in df.columns:
                work_rows = df[df["off_day"] == 0]
                off_rows = df[df["off_day"] == 1]
            else:
                work_rows = df
                off_rows = pd.DataFrame()

            s1, s2, s3 = st.columns(3)
            s1.metric("Working Days", len(work_rows))
            s2.metric("Off Days", len(off_rows))
            if not work_rows.empty and "work_start" in work_rows.columns and "work_end" in work_rows.columns:
                s3.metric("Work Hours", f"{work_rows.iloc[0]['work_start']} - {work_rows.iloc[0]['work_end']}")

        leave_notice = pd.read_sql(
            """
            SELECT start_date, end_date, status
            FROM leaves
            WHERE username=? AND branch=? AND organization=?
            ORDER BY id DESC
            LIMIT 1
            """,
            conn,
            params=(username, branch, org)
        )

        if not leave_notice.empty:
            try:
                lv_start = pd.to_datetime(leave_notice.iloc[0]["start_date"], errors="coerce").date()
                lv_end = pd.to_datetime(leave_notice.iloc[0]["end_date"], errors="coerce").date()
                lv_status = str(leave_notice.iloc[0]["status"]).lower()
                today = date.today()
                if pd.notna(lv_start) and pd.notna(lv_end) and lv_start <= today <= lv_end and lv_status in ["approved", "active", "pending"]:
                    expected_date = lv_end + timedelta(days=1)
                    st.warning(f"You are currently on leave. Expected reporting date: {expected_date}")
            except Exception:
                pass

    # =====================================================
    # ATTENDANCE
    # =====================================================
    elif page == "Attendance":

        df = pd.read_sql(
            "SELECT * FROM attendance WHERE username=? AND branch=?",
            conn, params=(username, branch)
        )

        if df.empty:
            st.info("No attendance records yet")
        else:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            today_dt = pd.to_datetime(date.today())

            att_range = st.selectbox(
                "Attendance Range",
                ["Day", "Week", "Month", "Custom (Sidebar Date Filter)"],
                key="att_range"
            )

            if att_range == "Day":
                mask = df["date"].dt.date == date.today()
                df = df[mask]
            elif att_range == "Week":
                start_week = today_dt - pd.Timedelta(days=6)
                df = df[df["date"] >= start_week]
            elif att_range == "Month":
                start_month = today_dt - pd.Timedelta(days=29)
                df = df[df["date"] >= start_month]
            else:
                df = filter_df(df)

            if df.empty:
                st.info("No attendance records in selected range")
            else:
                df = df.sort_values("date", ascending=False)
                lateness_df = _safe_read(
                    conn,
                    """
                    SELECT username, approved_for_date, reason, approved_by, status, actual_reason, used_at
                    FROM lateness_approvals
                    WHERE organization=? AND branch=? AND username=?
                    ORDER BY approved_for_date DESC, id DESC
                    """,
                    params=(org, branch, username),
                )
                df = _annotate_attendance_with_lateness(df, lateness_df)
                status_text = df["status"].astype(str).str.lower() if "status" in df.columns else pd.Series([], dtype=str)

                total_days = len(df)
                complete_days = int(((df["clock_in"].astype(str) != "") & (df["clock_out"].astype(str) != "")).sum())
                true_late_count = int(df["true_late"].sum()) if "true_late" in df.columns else 0
                approved_late_count = int(df["approved_late"].sum()) if "approved_late" in df.columns else 0
                other_flags = int(status_text.str.contains("flag|warning|absent", regex=True).sum()) if not status_text.empty else 0
                flagged = true_late_count + other_flags
                good_job = int(status_text.str.contains("on time|good|present", regex=True).sum()) if not status_text.empty else 0

                a1, a2, a3, a4 = st.columns(4)
                a1.metric("Days Logged", total_days)
                a2.metric("Clock In & Out Complete", complete_days)
                a3.metric("True Late Flags", flagged)
                a4.metric("Approved Late", approved_late_count)

                st.dataframe(
                    df[[
                        c for c in [
                            "date", "clock_in", "clock_out", "status_label",
                            "lateness_request_status", "lateness_reason",
                            "lateness_admin_note", "lateness_approved_by"
                        ] if c in df.columns
                    ]],
                    use_container_width=True
                )

                if good_job > 0:
                    st.caption(f"Good attendance records in range: {good_job}")

    # =====================================================
    # LEAVE
    # =====================================================
    elif page == "Leave":

        st.subheader("Request Leave")

        with st.form("leave_form", clear_on_submit=False):
            start = st.date_input("Start", key="leave_start")
            end = st.date_input("End", key="leave_end")
            reason = st.text_area("Reason", key="leave_reason")
            leave_submit = st.form_submit_button("Submit")

            if leave_submit:
                if end < start:
                    st.error("End date cannot be earlier than start date")
                elif not reason.strip():
                    st.error("Reason is required")
                else:
                    execute_write(conn, """
                    INSERT INTO leaves(username,branch,organization,start_date,end_date,reason,status)
                    VALUES (?,?,?,?,?,?,'pending')
                    """,(username,branch,org,str(start),str(end),reason.strip()))
                    conn.commit()
                    st.success("Submitted. Form is reset and ready for a new application.")
                    st.session_state["leave_start"] = date.today()
                    st.session_state["leave_end"] = date.today()
                    st.session_state["leave_reason"] = ""
                    st.rerun()

        df = pd.read_sql(
            "SELECT * FROM leaves WHERE username=? AND branch=?",
            conn, params=(username, branch)
        )

        st.dataframe(df)

    # =====================================================
    # NOTIFICATIONS
    # =====================================================
    elif page == "Notifications":

        msgs = pd.read_sql(
            "SELECT * FROM messages WHERE receiver=? AND branch=?",
            conn, params=(username, branch)
        )

        warns = pd.read_sql(
            "SELECT * FROM warnings WHERE username=?",
            conn, params=(username,)
        )

        leaves = pd.read_sql(
            """
            SELECT start_date, end_date, reason, status
            FROM leaves
            WHERE username=? AND branch=? AND organization=?
            ORDER BY id DESC
            LIMIT 10
            """,
            conn,
            params=(username, branch, org)
        )

        if msgs.empty and warns.empty and leaves.empty:
            st.info("No notifications")

        for _, r in msgs.iterrows():
            st.info(r["message"])

        for _, r in warns.iterrows():
            warn_type = r["type"] if "type" in r else "Warning"
            warn_msg = r["message"] if "message" in r and str(r["message"]).strip() else "Please review your recent activity."
            st.warning(f"{warn_type}: {warn_msg}")

        if not leaves.empty:
            st.subheader("Leave Updates")
            for _, lv in leaves.iterrows():
                status = str(lv.get("status", "pending")).lower()
                start_v = str(lv.get("start_date", ""))[:10]
                end_v = str(lv.get("end_date", ""))[:10]
                reason_v = str(lv.get("reason", "")).strip()
                message = f"{start_v} to {end_v}"
                if reason_v:
                    message = f"{message} | Reason: {reason_v}"

                if status == "approved":
                    st.success(f"Leave Approved: {message}")
                elif status in ["rejected", "declined", "denied"]:
                    st.error(f"Leave Rejected: {message}")
                else:
                    st.info(f"Leave Pending: {message}")

    # =====================================================
    # RATE (ADMIN INCLUDED)
    # =====================================================
    elif page == "Rate":

        settings = pd.read_sql("SELECT * FROM settings WHERE id=1", conn).iloc[0]

        if settings["rating_open"] == 0:
            st.error("Rating Closed")
            return

        users = pd.read_sql(
            "SELECT username FROM users WHERE branch=? AND organization=?",
            conn,
            params=(branch, org)
        )["username"].tolist()

        users = [u for u in users if u != username]

        if not users:
            st.info("No peers available for rating.")
            return

        target = st.selectbox("Select Person", users)

        already_rated = pd.read_sql(
            """
            SELECT COUNT(*) AS c
            FROM ratings
            WHERE rater=? AND rated=? AND branch=? AND organization=? AND date(created_at)=date('now')
            """,
            conn,
            params=(username, target, branch, org)
        )["c"].iloc[0]

        if already_rated > 0:
            st.warning("You already rated this peer today. Double rating is not allowed.")
            return

        topics = pd.read_sql("SELECT topic FROM topics", conn)

        with st.form("rate", clear_on_submit=False):

            scores = {}
            for t in topics["topic"]:
                scores[t] = st.slider(t, 0, 100, 0)

            if st.form_submit_button("Submit"):
                params_list = [
                    (username, target, t, s, branch, org)
                    for t, s in scores.items()
                ]
                execute_many_write(
                    conn,
                    """
                    INSERT INTO ratings(rater,rated,topic,score,branch,organization,created_at)
                    VALUES(?,?,?,?,?,?,datetime('now'))
                    """,
                    params_list,
                )
                conn.commit()
                st.success("Submitted successfully. Ready for next rating.")
                st.rerun()

    # =====================================================
    # MY SCORE
    # =====================================================
    elif page == "My Score":

        df = pd.read_sql(
            "SELECT * FROM ratings WHERE rated=? AND branch=?",
            conn, params=(username, branch)
        )

        df = filter_df(df)

        if df.empty:
            st.info("No data")
        else:
            avg = df.groupby("topic")["score"].mean()
            st.bar_chart(avg)
            st.metric("Average", round(avg.mean(),1))
            st.caption(recommendation(avg.mean()))

    # =====================================================
    # ANALYTICS
    # =====================================================
    elif page == "Analytics":

        df = pd.read_sql(
            "SELECT * FROM ratings WHERE rated=? AND branch=?",
            conn, params=(username, branch)
        )

        if df.empty:
            st.info("No analytics yet")
        else:
            df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
            range_sel = st.selectbox(
                "Performance Range",
                ["Day", "Week", "Month", "Custom (Sidebar Date Filter)"],
                key="analytics_range"
            )

            if range_sel == "Day":
                df = df[df["created_at"].dt.date == date.today()]
            elif range_sel == "Week":
                df = df[df["created_at"] >= (pd.Timestamp.now() - pd.Timedelta(days=6))]
            elif range_sel == "Month":
                df = df[df["created_at"] >= (pd.Timestamp.now() - pd.Timedelta(days=29))]
            else:
                df = df[
                    (df["created_at"] >= pd.to_datetime(start_date)) &
                    (df["created_at"] <= pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
                ]

            if df.empty:
                st.info("No analytics in selected range")
                return

            topic_avg = df.groupby("topic")["score"].mean()

            best = topic_avg.idxmax()
            worst = topic_avg.idxmin()

            st.metric("Overall Performance", round(float(topic_avg.mean()), 1))
            st.bar_chart(topic_avg)
            st.success(f"💪 Strongest: {best}")
            st.error(f"⚠ Weakest: {worst}")
            st.info("👉 Focus on weakest area to improve")

    # =====================================================
    # TOP PERFORMERS
    # =====================================================
    elif page == "Top Performers":

        df = pd.read_sql(
            "SELECT rated,score FROM ratings WHERE branch=? AND organization=?",
            conn, params=(branch, org)
        )

        top = df.groupby("rated")["score"].mean().sort_values(ascending=False).head(2)
        top_df = top.reset_index().rename(columns={"rated": "username", "score": "avg_score"})
        badge_map = build_holder_badge_map(org)
        top_df["username"] = top_df["username"].apply(lambda x: decorate_username_with_badges(x, badge_map))
        st.dataframe(top_df, use_container_width=True)

    # =====================================================
    # BADGES (VISIBLE TO USERS)
    # =====================================================
    elif page == "🏅 Badges":
        st.subheader("🏅 Organization & Branch Badges")

        badge_payload = compute_badges_for_organization(org)
        badges = badge_payload.get("badges", [])
        if not badges:
            st.info("No badge data available yet.")
        else:
            badges_df = pd.DataFrame(badges)
            if "badge" in badges_df.columns:
                badges_df["icon"] = badges_df["badge"].astype(str).apply(
                    lambda b: "🥇" if "Gold" in b else "🥈" if "Silver" in b else "🥉" if "Bronze" in b else "🚀" if "Most Improved" in b else "👩🏆" if "Best Female" in b else "👨🏆" if "Best Male" in b else "👑" if "Best Employee" in b else "🛡️" if "Best Admin" in b else "🏅"
                )
            if "holder" in badges_df.columns and "icon" in badges_df.columns:
                badges_df["holder"] = badges_df["holder"].astype(str) + " " + badges_df["icon"].astype(str)
            show_cols = [
                c for c in ["badge", "holder", "scope", "branch", "score", "rating_count"]
                if c in badges_df.columns
            ]
            st.dataframe(badges_df[show_cols], use_container_width=True)

            mine = badges_df[badges_df["holder"].astype(str) == str(username)]
            if not mine.empty:
                st.success(f"You currently hold {len(mine)} badge(s). Keep it up!")
                st.dataframe(mine[show_cols], use_container_width=True)
            else:
                st.info("You currently hold no active badge. Improve and badges will shift to you.")

    # =====================================================
    # MESSAGE MANAGEMENT
    # =====================================================
    elif page == "Message Management":

        msg = st.text_area("Message to Management")

        if st.button("Send"):
            if not msg.strip():
                st.error("Message cannot be empty")
                return
            execute_write(conn, """
            INSERT INTO messages(sender,receiver,branch,organization,message,created_at)
            VALUES (?,?,?,?,?,datetime('now'))
            """,(username,'management',branch,org,msg.strip()))
            conn.commit()
            st.success("Sent to Management")

    # =====================================================
    # SETTINGS
    # =====================================================
    elif page == "Settings":

        current = st.text_input("Current Password", type="password")
        new = st.text_input("New Password", type="password")

        if st.button("Update"):
            stored = pd.read_sql(
                "SELECT password FROM users WHERE username=?",
                conn,
                params=(username,)
            ).iloc[0]["password"]

            if verify_password(current, stored):
                execute_write(
                    conn,
                    "UPDATE users SET password=? WHERE username=?",
                    (hash_password(new),username)
                )
                conn.commit()
                st.success("Updated")
                refresh()
            else:
                st.error("Wrong password")
