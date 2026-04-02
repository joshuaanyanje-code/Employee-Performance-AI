import streamlit as st
import pandas as pd
from datetime import datetime
from Dashboards.ui_responsive import apply_responsive_ui


def _safe_read(conn, query, params=None):
    try:
        if params is None:
            return pd.read_sql(query, conn)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()


def _parse_time(value, fallback):
    txt = str(value or fallback)
    try:
        return datetime.strptime(txt, "%H:%M").time()
    except Exception:
        return datetime.strptime(fallback, "%H:%M").time()


def _insert_warning(conn, username, org, branch, warning_type, message):
    conn.execute(
        """
        INSERT INTO warnings(username,organization,branch,type,message,created_at)
        VALUES (?,?,?,?,?,datetime('now'))
        """,
        (username, org, branch, warning_type, message),
    )


# ==============================
# MAIN FUNCTION (🔥 FIXED)
# ==============================
def attendance_dashboard(conn, branch):

    apply_responsive_ui("default")

    org = st.session_state.get("organization")
    if not branch:
        st.error("Branch is required.")
        return

    if not org:
        st.warning("Organization not detected in session. Showing branch-level data only.")

    # ==============================
    # LOAD SETTINGS
    # ==============================
    settings_df = _safe_read(conn, "SELECT * FROM settings WHERE id=1")
    if settings_df.empty:
        st.error("Settings row missing.")
        return
    settings = settings_df.iloc[0]

    global_start = _parse_time(settings.get("work_start", "09:00"), "09:00")
    global_end = _parse_time(settings.get("work_end", "18:00"), "18:00")
    try:
        late_minutes = int(settings.get("late_minutes", 15))
    except Exception:
        late_minutes = 15

    # ==============================
    # LOAD USERS
    # ==============================
    if org:
        users = _safe_read(
            conn,
            "SELECT username, role FROM users WHERE branch=? AND organization=?",
            params=(branch, org),
        )
    else:
        users = _safe_read(
            conn,
            "SELECT username, role FROM users WHERE branch=?",
            params=(branch,),
        )

    # ==============================
    # LOAD ATTENDANCE
    # ==============================
    if org:
        attendance = _safe_read(
            conn,
            "SELECT * FROM attendance WHERE branch=? AND organization=?",
            params=(branch, org),
        )
    else:
        attendance = _safe_read(
            conn,
            "SELECT * FROM attendance WHERE branch=?",
            params=(branch,),
        )

    if attendance.empty:
        st.info("No attendance data for this branch.")
        return

    attendance["clock_in"] = pd.to_datetime(attendance["clock_in"], errors="coerce")
    attendance["clock_out"] = pd.to_datetime(attendance["clock_out"], errors="coerce")

    today = datetime.now().date()
    today_day = datetime.now().strftime("%A")

    today_df = attendance[attendance["clock_in"].dt.date == today]

    late_list = []
    early_clockout_list = []
    absent_list = []

    if org:
        lateness_approvals = _safe_read(
            conn,
            """
            SELECT username, approved_for_date, status
            FROM lateness_approvals
            WHERE branch=? AND organization=?
            """,
            params=(branch, org),
        )
    else:
        lateness_approvals = _safe_read(
            conn,
            """
            SELECT username, approved_for_date, status
            FROM lateness_approvals
            WHERE branch=?
            """,
            params=(branch,),
        )

    approved_lateness_keys = set()
    if not lateness_approvals.empty:
        approved_lateness_rows = lateness_approvals[
            lateness_approvals["status"].astype(str).str.lower().isin(["approved", "used"])
        ].copy()
        approved_lateness_keys = {
            (str(row.get("username", "")), str(row.get("approved_for_date", "")))
            for _, row in approved_lateness_rows.iterrows()
        }

    # Build today schedule map set by admin per user/day.
    if org:
        schedules = _safe_read(
            conn,
            """
            SELECT username, day, work_start, work_end, off_day
            FROM schedules
            WHERE branch=? AND organization=?
            """,
            params=(branch, org),
        )
    else:
        schedules = _safe_read(
            conn,
            """
            SELECT username, day, work_start, work_end, off_day
            FROM schedules
            WHERE branch=?
            """,
            params=(branch,),
        )

    schedule_today = {}
    if not schedules.empty:
        schedules_day = schedules[schedules["day"].astype(str) == today_day]
        for _, s in schedules_day.iterrows():
            schedule_today[str(s["username"])] = {
                "work_start": _parse_time(s.get("work_start", "09:00"), "09:00"),
                "work_end": _parse_time(s.get("work_end", "18:00"), "18:00"),
                "off_day": int(s.get("off_day", 0)),
            }

    # Expected users for today (exclude off-day users when explicit schedule exists).
    expected_users = []
    if not users.empty:
        for _, u in users.iterrows():
            uname = str(u["username"])
            sched = schedule_today.get(uname)
            if sched and int(sched.get("off_day", 0)) == 1:
                continue
            expected_users.append(uname)

    # ==============================
    # LOOP USERS
    # ==============================
    for _, row in today_df.iterrows():

        username = row["username"]

        # ==============================
        # LOAD SCHEDULE
        # ==============================
        schedule = schedule_today.get(username)

        # ==============================
        # GET WORK HOURS
        # ==============================
        if schedule:
            work_start = schedule["work_start"]
            work_end = schedule["work_end"]
            off_day = int(schedule.get("off_day", 0))
        else:
            work_start = global_start
            work_end = global_end
            off_day = 0

        # ==============================
        # SKIP OFF DAY
        # ==============================
        if off_day == 1:
            continue

        # ==============================
        # SAFE CLOCK IN
        # ==============================
        if pd.isna(row["clock_in"]):
            continue

        # ==============================
        # CHECK LEAVE
        # ==============================
        if org:
            leave = _safe_read(
                conn,
                """
                SELECT * FROM leaves
                WHERE username=? AND branch=? AND organization=? AND status='approved'
                """,
                params=(username, branch, org),
            )
        else:
            leave = _safe_read(
                conn,
                """
                SELECT * FROM leaves
                WHERE username=? AND branch=? AND status='approved'
                """,
                params=(username, branch),
            )

        on_leave = False
        today_str = str(today)

        for _, l in leave.iterrows():
            if l["start_date"] <= today_str <= l["end_date"]:
                on_leave = True
                break

        if on_leave:
            continue

        # ==============================
        # LATE CALCULATION
        # ==============================
        start_dt = datetime.combine(today, work_start)
        delay = (row["clock_in"] - start_dt).total_seconds() / 60

        approval_key = (str(username), str(today))
        if delay > late_minutes and approval_key not in approved_lateness_keys:
            late_list.append((username, int(delay)))

        if pd.notna(row.get("clock_out")):
            try:
                if row["clock_out"].time() < work_end:
                    mins_early = int((datetime.combine(today, work_end) - row["clock_out"]).total_seconds() / 60)
                    early_clockout_list.append((username, max(mins_early, 0)))
            except Exception:
                pass

    # Absentee list from expected users not in today's attendance.
    present_users = set(today_df["username"].astype(str).tolist()) if not today_df.empty else set()
    absent_list = sorted(list(set(expected_users) - present_users))

    # ==============================
    # AUTO RULES (UNCHANGED)
    # ==============================
    run_auto_rules(conn, branch, org)

    # ==============================
    # FILTER BUTTONS
    # ==============================
    if "filter" not in st.session_state:
        st.session_state.filter = "all"

    c0, c1, c2, c3 = st.columns(4)

    c0.metric("Records Today", len(today_df))
    c1.metric("Late Today", len(late_list))
    c2.metric("Early Clock-out", len(early_clockout_list))
    c3.metric("Absent Today", len(absent_list))

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Show All Staff"):
            st.session_state.filter = "all"
            st.rerun()

    with col2:
        if st.button("🚨 Show Only Late Staff"):
            st.session_state.filter = "late"
            st.rerun()

    # ==============================
    # DISPLAY RESULTS
    # ==============================
    if st.session_state.filter == "late":
        if not late_list:
            st.info("No late staff today")
        else:
            st.warning("Late Staff")
            for u, mins in late_list:
                st.write(f"{u} → {mins} mins late")
    else:
        st.dataframe(today_df, use_container_width=True)

        st.markdown("### Early Clock-out Today")
        if early_clockout_list:
            st.dataframe(pd.DataFrame(early_clockout_list, columns=["username", "minutes_early"]))
        else:
            st.info("No early clock-out cases today.")

        st.markdown("### Absent Today")
        if absent_list:
            st.dataframe(pd.DataFrame(absent_list, columns=["username"]))
        else:
            st.info("No absent users today.")


# ===========================================
# AUTO RULES FUNCTION (UNCHANGED)
# ===========================================
def run_auto_rules(conn, branch, org=None):

    today = str(datetime.now().date())

    if org:
        users = _safe_read(
            conn,
            "SELECT username FROM users WHERE branch=? AND organization=?",
            params=(branch, org),
        )
    else:
        users = _safe_read(
            conn,
            "SELECT username FROM users WHERE branch=?",
            params=(branch,),
        )

    if users.empty:
        return

    for u in users["username"]:

        if org:
            leaves = _safe_read(
                conn,
                """
                SELECT * FROM leaves
                WHERE username=? AND branch=? AND organization=? AND status='approved'
                """,
                params=(u, branch, org),
            )
        else:
            leaves = _safe_read(
                conn,
                """
                SELECT * FROM leaves
                WHERE username=? AND branch=? AND status='approved'
                """,
                params=(u, branch),
            )

        if len(leaves) >= 5:
            _insert_warning(
                conn,
                u,
                org or "",
                branch,
                "excess_leave",
                f"Excess leave pattern detected ({len(leaves)} approved leaves)",
            )

        monday_leaves = 0
        for _, l in leaves.iterrows():
            day = pd.to_datetime(l["start_date"]).strftime("%A")
            if day == "Monday":
                monday_leaves += 1

        if monday_leaves >= 3:
            _insert_warning(
                conn,
                u,
                org or "",
                branch,
                "monday_pattern",
                f"Frequent Monday leave pattern ({monday_leaves})",
            )

        if org:
            attendance = _safe_read(
                conn,
                """
                SELECT * FROM attendance
                WHERE username=? AND branch=? AND organization=? AND UPPER(status)='LATE'
                """,
                params=(u, branch, org),
            )
        else:
            attendance = _safe_read(
                conn,
                """
                SELECT * FROM attendance
                WHERE username=? AND branch=? AND UPPER(status)='LATE'
                """,
                params=(u, branch),
            )

        if len(attendance) >= 5:
            _insert_warning(
                conn,
                u,
                org or "",
                branch,
                "too_many_lates",
                f"Too many late clock-ins ({len(attendance)})",
            )

        if org:
            absents = _safe_read(
                conn,
                """
                SELECT * FROM attendance
                WHERE username=? AND branch=? AND organization=? AND UPPER(status)='ABSENT'
                """,
                params=(u, branch, org),
            )
        else:
            absents = _safe_read(
                conn,
                """
                SELECT * FROM attendance
                WHERE username=? AND branch=? AND UPPER(status)='ABSENT'
                """,
                params=(u, branch),
            )

        if len(absents) >= 3:
            _insert_warning(
                conn,
                u,
                org or "",
                branch,
                "absent_without_leave",
                f"Absent without approved leave pattern ({len(absents)})",
            )

    conn.commit()
