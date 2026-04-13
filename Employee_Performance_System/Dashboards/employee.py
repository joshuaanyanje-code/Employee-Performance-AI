import os
import time as pytime
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from database.db import cached_read_sql, get_connection, verify_password, hash_password, execute_write, execute_many_write, is_recent_duplicate_message, get_hr_config, get_kpi_ai_config
from Dashboards.ui_responsive import apply_responsive_ui, navigation_expander_open_default, render_dashboard_banner
from Analytics.badges import compute_badges_for_organization, build_holder_badge_map, decorate_username_with_badges
from Analytics.polls import ensure_poll_tables, get_user_poll_response, get_visible_polls, submit_poll_response
try:
    from Analytics.late_fines import compute_lateness_fines, compute_lateness_fine_history, get_lateness_policy
except Exception:
    def compute_lateness_fines(*args, **kwargs):
        return pd.DataFrame(columns=["Username", "Role", "Branch", "Chargeable Late Minutes", "Approved Late Minutes", "Chargeable Hours", "Pending Minutes to Next Fine", "Fine Amount"])

    def compute_lateness_fine_history(*args, **kwargs):
        return pd.DataFrame(columns=["Month", "Username", "Role", "Branch", "Chargeable Late Minutes", "Approved Late Minutes", "Chargeable Hours", "Pending Minutes to Next Fine", "Fine Amount"])

    def get_lateness_policy(*args, **kwargs):
        return {"amount_per_hour": 0.0, "currency": "KES", "pending_request": None}

try:
    from Dashboards.ui_responsive import is_mobile_device
except Exception:
    def is_mobile_device():
        return False
# ==============================
# REFRESH
# ==============================
def refresh():
    st.session_state["_r"] = st.session_state.get("_r", 0) + 1
    st.rerun()


def refresh_with_message(message, level="success"):
    st.session_state["_employee_flash"] = {
        "level": level,
        "text": str(message or "").strip(),
        "created_at": pytime.time(),
        "duration": 2.0,
    }
    refresh()


def _clear_action_widgets():
    keep_keys = {
        "logged",
        "username",
        "role",
        "organization",
        "branch",
        "auth_token",
        "_employee_flash",
    }
    for session_key in list(st.session_state.keys()):
        if session_key in keep_keys:
            continue
        if str(session_key).startswith("_"):
            continue
        st.session_state.pop(session_key, None)


def show_flash_message():
    payload = st.session_state.get("_employee_flash")
    if not payload:
        return

    created_at = float(payload.get("created_at", pytime.time()))
    duration = max(float(payload.get("duration", 2.0) or 2.0), 0.2)
    if (pytime.time() - created_at) >= duration:
        st.session_state.pop("_employee_flash", None)
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

    if not bool(payload.get("widgets_cleared", False)):
        _clear_action_widgets()
        payload["widgets_cleared"] = True
        st.session_state["_employee_flash"] = payload


def _safe_read(conn, query, params=None):
    try:
        normalized_params = tuple(params) if isinstance(params, (list, tuple)) else ((params,) if params is not None else ())
        query_text = str(query or "").strip()
        if query_text.lower().startswith("select") and not getattr(conn, "in_transaction", False):
            df = cached_read_sql(query_text, normalized_params)
        else:
            if params is None:
                df = pd.read_sql(query, conn)
            else:
                df = pd.read_sql(query, conn, params=params)
        if isinstance(df, pd.DataFrame) and not df.empty:
            for col in df.select_dtypes(include=["object"]).columns:
                series = df[col].astype(str)
                dirty_mask = series.str.contains(r"â[\x80-\xBF]{1,2}|Ã.|Â|�|[\u200B-\u200D\uFEFF]", regex=True, na=False)
                if not dirty_mask.any():
                    continue
                cleaned = series.loc[dirty_mask]
                try:
                    repaired = cleaned.str.encode("latin-1", errors="ignore").str.decode("utf-8", errors="ignore")
                    cleaned = repaired.where(repaired.str.len() > 0, cleaned)
                except Exception:
                    pass
                cleaned = (
                    cleaned
                    .str.replace("â€”", " - ", regex=False)
                    .str.replace("â€“", " - ", regex=False)
                    .str.replace("Â", "", regex=False)
                    .str.replace("Ã", "", regex=False)
                    .str.replace("�", "", regex=False)
                    .str.replace(r"â[\x80-\xBF]{1,2}", "", regex=True)
                    .str.replace(r"[\u200B-\u200D\uFEFF]", "", regex=True)
                    .str.replace(r"\s{2,}", " ", regex=True)
                    .str.strip()
                )
                df.loc[dirty_mask, col] = cleaned
        return df
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
        return "⚠ Improve on your performance."
    else:
        return "🚨 Serious improvement required."

# ==============================
# DASHBOARD
# ==============================
def employee_dashboard():

    apply_responsive_ui("default")

    conn = get_connection()
    ensure_poll_tables(conn)
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

    current_status = str(user_data.iloc[0].get("status", "active") or "active").lower()
    if current_status == "suspended":
        st.error("🚫 Account Suspended. Contact Admin.")
        return
    if current_status == "probation":
        st.warning("⚠ Your account is on probation and under management review.")

    branch = user_data.iloc[0]["branch"]
    hr_config = get_hr_config(conn, org)
    kpi_ai_config = get_kpi_ai_config(conn, org)
    hr_mode_enabled = bool(int(hr_config.get("hr_mode_enabled", 0) or 0))
    hr_coverage_df = _safe_read(
        conn,
        """
        SELECT username, branch
        FROM users
        WHERE organization=?
          AND lower(coalesce(role,''))='hr'
          AND lower(coalesce(status,'active'))='active'
          AND (trim(coalesce(branch,''))='' OR lower(trim(coalesce(branch,'')))=lower(trim(?)))
        ORDER BY CASE WHEN trim(coalesce(branch,''))='' THEN 0 ELSE 1 END, username
        """,
        params=(org, branch),
    ) if hr_mode_enabled else pd.DataFrame()
    has_hr_coverage = hr_mode_enabled and not hr_coverage_df.empty
    has_org_wide_hr = has_hr_coverage and hr_coverage_df["branch"].fillna("").astype(str).str.strip().eq("").any()
    hr_scope_label = "Organization-wide HR" if has_org_wide_hr else (f"Branch HR ({branch})" if has_hr_coverage else "")

    employee_docs_enabled = has_hr_coverage and bool(int(hr_config.get("hr_documents_enabled", 1) or 0))
    employee_onboarding_enabled = has_hr_coverage and bool(int(hr_config.get("hr_onboarding_enabled", 1) or 0))
    employee_case_updates_enabled = has_hr_coverage and bool(int(hr_config.get("hr_case_files_enabled", 1) or 0))

    st.title("Employee Dashboard")
    render_dashboard_banner(
        "Specialist workspace",
        f"Welcome, {username} !",
        "Track your schedule, attendance, leave, ratings, and growth.",
        pills=[
             f"Organization: {org}",
        ],
    )
    show_flash_message()

    if hr_mode_enabled and has_hr_coverage:
        st.caption(f"HR support is active for you via {hr_scope_label}. Any employee-safe HR documents, onboarding tasks, and updates will appear in this dashboard only for your profile.")
    elif hr_mode_enabled and not has_hr_coverage:
        st.caption("HR mode is enabled for the organization, but there is currently no HR partner assigned to your branch scope yet.")

    # ==============================
    # NOTIFICATION COUNT
    # ==============================
    notif_count = pd.read_sql(
        "SELECT COUNT(*) as c FROM messages WHERE receiver=? AND branch=? AND organization=?",
        conn,
        params=(username, branch, org)
    )["c"].iloc[0]

    is_mobile = is_mobile_device()

    def _collapse_employee_mobile_nav():
        if is_mobile:
            st.session_state["employee_nav_open"] = False

    if "employee_nav_open" not in st.session_state:
        st.session_state["employee_nav_open"] = True

    def nav_selectbox(label, options, key, **kwargs):
        if is_mobile:
            return st.selectbox(label, options, key=key, **kwargs)
        with st.sidebar:
            return st.selectbox(label, options, key=key, **kwargs)

    page_items = [
        "Profile", "Schedule", "Attendance", "Leave", "Notifications", "My KPIs"
    ]
    if employee_docs_enabled:
        page_items.append("My HR Documents")
    if employee_onboarding_enabled:
        page_items.append("My Onboarding")
    page_items.extend([
        "Rate", "My Score",
        "Analytics", "Top Performers",
        "🏅 Badges", "Polls", "Message Management", "Settings"
    ])

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

        st.markdown("### Quick Performance Snapshot")

        prof_att = _safe_read(
            conn,
            "SELECT date, status FROM attendance WHERE username=? AND organization=?",
            params=(username, org),
        )
        if not prof_att.empty:
            prof_att["date"] = pd.to_datetime(prof_att["date"], errors="coerce")
            prof_att = prof_att[
                (prof_att["date"] >= pd.to_datetime(start_date)) &
                (prof_att["date"] <= pd.to_datetime(end_date))
            ]

        prof_lateness = _safe_read(
            conn,
            """
            SELECT approved_for_date, status, reason, actual_reason, approved_by
            FROM lateness_approvals
            WHERE username=? AND organization=?
            """,
            params=(username, org),
        )
        prof_att = _annotate_attendance_with_lateness(prof_att, prof_lateness)
        true_late_flags = int(prof_att["true_late"].sum()) if not prof_att.empty and "true_late" in prof_att.columns else 0

        fine_summary = compute_lateness_fines(conn, org, username=username)
        fine_row = fine_summary.iloc[0].to_dict() if not fine_summary.empty else {}
        chargeable_min = int(fine_row.get("Chargeable Late Minutes", 0) or 0)
        approved_min = int(fine_row.get("Approved Late Minutes", 0) or 0)
        total_late_min = chargeable_min + approved_min
        fine_due = float(fine_row.get("Fine Amount", 0) or 0)

        score_df = _safe_read(
            conn,
            "SELECT score FROM ratings WHERE rated=? AND organization=?",
            params=(username, org),
        )
        avg_score = float(pd.to_numeric(score_df["score"], errors="coerce").mean()) if not score_df.empty else 0.0

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Lateness Flags", true_late_flags)
        q2.metric("Total Lateness (min)", total_late_min)
        q3.metric("Fines Accumulated", f"KES {fine_due:,.0f}")
        q4.metric("Average Score", f"{avg_score:.1f}/100 ({avg_score:.1f}%)" if not score_df.empty else "-")

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
            SELECT start_date, end_date, status, approved_by, admin_note, branch
            FROM leaves
            WHERE username=? AND organization=?
            ORDER BY id DESC
            LIMIT 1
            """,
            conn,
            params=(username, org)
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
            "SELECT * FROM attendance WHERE username=? AND organization=?",
            conn, params=(username, org)
        )

        if df.empty:
            st.info("No attendance records yet")
        else:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            today_dt = pd.to_datetime(date.today())

            att_range = nav_selectbox(
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
                    WHERE organization=? AND username=?
                    ORDER BY approved_for_date DESC, id DESC
                    """,
                    params=(org, username),
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

                fine_policy = get_lateness_policy(conn, org)
                employee_fine_df = compute_lateness_fines(conn, org, username=username)
                employee_fine_history_df = compute_lateness_fine_history(conn, org, username=username, months=6)
                employee_fine_row = employee_fine_df.iloc[0].to_dict() if not employee_fine_df.empty else {}

                fine_amount = float(employee_fine_row.get("Fine Amount", 0) or 0)
                chargeable_minutes = int(employee_fine_row.get("Chargeable Late Minutes", 0) or 0)
                chargeable_hours = int(employee_fine_row.get("Chargeable Hours", 0) or 0)
                next_fine_gap = int(employee_fine_row.get("Pending Minutes to Next Fine", 60) or 60)

                f1, f2, f3, f4 = st.columns(4)
                f1.metric("Late Fine Due", f"KES {fine_amount:,.0f}")
                f2.metric("Chargeable Late Min", chargeable_minutes)
                f3.metric("Chargeable Hours", chargeable_hours)
                f4.metric("Minutes to Next Fine", next_fine_gap)

                if float(fine_policy.get("amount_per_hour", 0) or 0) <= 0:
                    st.info("No approved lateness deduction amount is active yet, so your current month fine remains KES 0.")
                else:
                    st.caption(
                        f"Approved fine rate: KES {float(fine_policy.get('amount_per_hour', 0) or 0):,.0f} for each full accumulated hour of unapproved lateness. This due resets for the new month, while history stays available below."
                    )

                st.markdown("**Monthly lateness fine history**")
                if employee_fine_history_df.empty:
                    st.info("No lateness fine history yet.")
                else:
                    st.dataframe(
                        employee_fine_history_df[["Month", "Chargeable Late Minutes", "Chargeable Hours", "Fine Amount"]],
                        use_container_width=True,
                        hide_index=True,
                    )

                st.dataframe(
                    df[[
                        c for c in [
                            "branch", "date", "clock_in", "clock_out", "status_label",
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

        if st.session_state.pop("_reset_leave_form", False):
            st.session_state["leave_start"] = date.today()
            st.session_state["leave_end"] = date.today()
            st.session_state["leave_reason"] = ""
        st.session_state.setdefault("leave_start", date.today())
        st.session_state.setdefault("leave_end", date.today())
        st.session_state.setdefault("leave_reason", "")

        with st.form("leave_form", clear_on_submit=False):
            start = st.date_input("Start", key="leave_start")
            end = st.date_input("End", key="leave_end")
            reason = st.text_area("Reason", key="leave_reason")
            leave_submit = st.form_submit_button("Submit")

            if leave_submit:
                clean_reason = reason.strip()
                if end < start:
                    st.error("End date cannot be earlier than start date")
                elif not clean_reason:
                    st.error("Reason is required")
                else:
                    duplicate_leave = _safe_read(
                        conn,
                        """
                        SELECT id
                        FROM leaves
                        WHERE username=? AND organization=?
                          AND start_date=? AND end_date=? AND reason=?
                          AND lower(coalesce(status, 'pending')) IN ('pending', 'approved', 'reapply')
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        params=(username, org, str(start), str(end), clean_reason),
                    )
                    if not duplicate_leave.empty:
                        refresh_with_message("Duplicate leave request blocked. The same leave request already exists.", level="warning")
                    else:
                        execute_write(conn, """
                        INSERT INTO leaves(username,branch,organization,start_date,end_date,reason,status)
                        VALUES (?,?,?,?,?,?,'pending')
                        """,(username,branch,org,str(start),str(end),clean_reason))
                        conn.commit()
                        st.session_state["_reset_leave_form"] = True
                        refresh_with_message("Leave request submitted. Form is reset and ready for a new application.")

        df = pd.read_sql(
            "SELECT * FROM leaves WHERE username=? AND organization=? ORDER BY id DESC",
            conn, params=(username, org)
        )
        if not df.empty:
            show_cols = [
                c for c in [
                    "branch", "start_date", "end_date", "reason", "status",
                    "approved_by", "admin_note", "reviewed_at"
                ] if c in df.columns
            ]
            st.dataframe(df[show_cols], use_container_width=True)
        else:
            st.info("No leave requests yet.")

    # =====================================================
    # NOTIFICATIONS
    # =====================================================
    elif page == "Notifications":

        msgs = pd.read_sql(
            "SELECT * FROM messages WHERE receiver=? AND organization=? ORDER BY id DESC",
            conn, params=(username, org)
        )

        warns = pd.read_sql(
            "SELECT * FROM warnings WHERE username=? AND organization=?",
            conn, params=(username, org)
        )

        leaves = pd.read_sql(
            """
            SELECT start_date, end_date, reason, status, approved_by, admin_note, reviewed_at
            FROM leaves
            WHERE username=? AND organization=?
            ORDER BY id DESC
            LIMIT 10
            """,
            conn,
            params=(username, org)
        )

        visible_hr_cases = _safe_read(
            conn,
            """
            SELECT case_type, title, note, status, created_at, updated_at
            FROM hr_case_files
            WHERE organization=? AND username=?
              AND lower(coalesce(visibility,'hr'))='employee'
              AND (trim(coalesce(branch,''))='' OR lower(trim(coalesce(branch,'')))=lower(trim(?)))
            ORDER BY id DESC
            LIMIT 10
            """,
            params=(org, username, branch),
        ) if employee_case_updates_enabled else pd.DataFrame()

        if msgs.empty and warns.empty and leaves.empty and visible_hr_cases.empty:
            st.info("No notifications")

        for _, r in msgs.iterrows():
            sender_name = str(r.get("sender", "Management") or "Management")
            created_at = str(r.get("created_at", ""))[:16]
            branch_name = str(r.get("branch", "") or "").strip()
            branch_label = f" | Branch: {branch_name}" if branch_name else ""
            st.info(f"From {sender_name}{branch_label} | {created_at} | {r['message']}")

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
                approver_v = str(lv.get("approved_by", "") or "").strip()
                admin_note_v = str(lv.get("admin_note", "") or "").strip()
                reviewed_v = str(lv.get("reviewed_at", "") or "").strip()
                if approver_v:
                    message = f"{message} | Handled by: {approver_v}"
                if reviewed_v:
                    message = f"{message} | Reviewed: {reviewed_v[:16]}"
                if admin_note_v:
                    message = f"{message} | Note: {admin_note_v}"

                if status == "approved":
                    st.success(f"Leave Approved: {message}")
                elif status in ["rejected", "declined", "denied"]:
                    st.error(f"Leave Rejected: {message}")
                else:
                    st.info(f"Leave Pending: {message}")

        if not visible_hr_cases.empty:
            st.subheader("HR Updates")
            for _, case_row in visible_hr_cases.iterrows():
                case_status = str(case_row.get("status", "open") or "open").strip().lower()
                case_title = str(case_row.get("title", "HR Update") or "HR Update").strip()
                case_note = str(case_row.get("note", "") or "").strip()
                case_type = str(case_row.get("case_type", "case") or "case").replace("_", " ").title()
                case_when = str(case_row.get("updated_at", "") or case_row.get("created_at", "")).strip()
                message = f"{case_type}: {case_title}"
                if case_note:
                    message = f"{message} | {case_note}"
                if case_when:
                    message = f"{message} | Updated: {case_when[:16]}"
                if case_status == "closed":
                    st.success(message)
                elif case_status == "in_review":
                    st.warning(message)
                else:
                    st.info(message)

    # =====================================================
    # MY KPIS
    # =====================================================
    elif page == "My KPIs":
        st.subheader("My Goals & KPIs")
        st.caption("Track your assigned goals, update progress, and see how guest experience is linking to your performance.")

        kpi_weight = float(kpi_ai_config.get("kpi_weight_pct", 60.0) or 60.0)
        service_weight = float(kpi_ai_config.get("service_weight_pct", 40.0) or 40.0)
        warning_threshold = float(kpi_ai_config.get("warning_health_score", 65.0) or 65.0)
        critical_threshold = float(kpi_ai_config.get("critical_health_score", 45.0) or 45.0)
        low_star_threshold = float(kpi_ai_config.get("low_star_threshold", 3.4) or 3.4)
        min_feedback_count = int(kpi_ai_config.get("min_feedback_count", 3) or 3)

        kpi_df = _safe_read(
            conn,
            """
            SELECT id, branch, scope_type, target_role, metric_name, target_value, current_value,
                   unit, period, priority, status, due_date, note, created_by, updated_by, created_at, updated_at
            FROM kpi_targets
            WHERE organization=?
              AND lower(coalesce(status,'active'))!='archived'
              AND (
                    (lower(coalesce(scope_type,'individual'))='individual' AND lower(trim(coalesce(target_username,'')))=lower(trim(?)))
                 OR (lower(coalesce(scope_type,'branch'))='branch' AND lower(trim(coalesce(branch,'')))=lower(trim(?)) AND lower(coalesce(target_role,'employee')) IN ('employee','all'))
                 OR (lower(coalesce(scope_type,'organization'))='organization' AND lower(coalesce(target_role,'employee')) IN ('employee','all'))
              )
            ORDER BY CASE WHEN lower(coalesce(status,'active'))='active' THEN 0 ELSE 1 END, due_date ASC, id DESC
            """,
            params=(org, username, branch),
        )

        service_df = _safe_read(
            conn,
            """
            SELECT feedback_scope, stars, message, created_at
            FROM client_feedback
            WHERE organization=?
              AND ((feedback_scope='individual' AND lower(trim(coalesce(target_username,'')))=lower(trim(?)))
                   OR (feedback_scope='general' AND lower(trim(coalesce(branch,'')))=lower(trim(?))))
            ORDER BY id DESC
            """,
            params=(org, username, branch),
        )

        if kpi_df.empty:
            st.info("No KPI goals have been assigned to you yet.")
        else:
            kpi_view = kpi_df.copy()
            kpi_view["target_value"] = pd.to_numeric(kpi_view["target_value"], errors="coerce").fillna(0)
            kpi_view["current_value"] = pd.to_numeric(kpi_view["current_value"], errors="coerce").fillna(0)
            kpi_view["completion_pct"] = kpi_view.apply(
                lambda row: round((float(row["current_value"]) / float(row["target_value"]) * 100), 1) if float(row["target_value"] or 0) > 0 else 0.0,
                axis=1,
            )

            total_goals = len(kpi_view)
            avg_completion = float(kpi_view["completion_pct"].mean()) if not kpi_view.empty else 0.0
            due_soon = int((pd.to_datetime(kpi_view["due_date"], errors="coerce") <= (pd.Timestamp.now() + pd.Timedelta(days=7))).fillna(False).sum()) if "due_date" in kpi_view.columns else 0
            c1, c2, c3 = st.columns(3)
            c1.metric("Active Goals", total_goals)
            c2.metric("Average Completion", f"{avg_completion:.0f}%")
            c3.metric("Due Soon", due_soon)

            display_cols = [c for c in ["metric_name", "scope_type", "branch", "current_value", "target_value", "unit", "completion_pct", "period", "priority", "status", "due_date"] if c in kpi_view.columns]
            st.dataframe(kpi_view[display_cols], use_container_width=True, hide_index=True)

            target_feedback_df = service_df[service_df["feedback_scope"].astype(str) == "individual"].copy() if not service_df.empty else pd.DataFrame()
            branch_feedback_df = service_df[service_df["feedback_scope"].astype(str) == "general"].copy() if not service_df.empty else pd.DataFrame()
            avg_target_stars = float(pd.to_numeric(target_feedback_df["stars"], errors="coerce").mean()) if not target_feedback_df.empty else None
            avg_branch_stars = float(pd.to_numeric(branch_feedback_df["stars"], errors="coerce").mean()) if not branch_feedback_df.empty else None
            personal_feedback_count = int(len(target_feedback_df))
            completion_score = max(0.0, min(100.0, avg_completion))
            reference_stars = avg_target_stars if avg_target_stars is not None else avg_branch_stars
            reference_feedback_count = personal_feedback_count if avg_target_stars is not None else int(len(branch_feedback_df))
            service_score = None if reference_stars is None else max(0.0, min(100.0, (float(reference_stars) / 5.0) * 100.0))
            health_score = completion_score if service_score is None else ((completion_score * kpi_weight) + (service_score * service_weight)) / max(1.0, (kpi_weight + service_weight))

            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Personal Health Score", f"{health_score:.0f}/100 ({health_score:.0f}%)")
            h2.metric("KPI Weight", f"{kpi_weight:.0f}%")
            h3.metric("Service Weight", f"{service_weight:.0f}%")
            h4.metric("Feedback Count", personal_feedback_count)

            st.markdown("### AI Coaching Snapshot")
            if avg_completion >= 85 and (avg_target_stars is None or avg_target_stars >= 4.2):
                st.success(f"AI growth signal: you are tracking strongly at about {avg_completion:.0f}% KPI completion. Keep the current service rhythm and ask for stretch goals.")
            elif reference_feedback_count >= min_feedback_count and (health_score <= critical_threshold or (reference_stars is not None and reference_stars <= low_star_threshold and avg_completion < warning_threshold)):
                st.error(f"AI escalation alert: your weighted health is {health_score:.0f}/100. Ask your manager for a focused recovery plan and weekly check-ins.")
            elif avg_completion < 60 and avg_target_stars is not None and avg_target_stars < 3.5:
                st.error(f"AI alert: your KPI completion is around {avg_completion:.0f}% and guest feedback is soft at {avg_target_stars:.1f} stars. Focus on service recovery and daily follow-through.")
            elif health_score <= warning_threshold:
                st.warning(f"AI watchlist: your weighted health is {health_score:.0f}/100. Increase consistency in execution and customer experience this week.")
            elif avg_completion < 60:
                st.warning(f"AI coach: your KPI completion is around {avg_completion:.0f}%. Break the goal into weekly wins and update progress more often.")
            elif avg_target_stars is not None and avg_target_stars >= 4.5:
                st.success(f"AI recognition: guests are rating your service at {avg_target_stars:.1f} stars on average. Keep building on that strength.")
            else:
                st.info("AI coach: stay consistent on your current goals and keep watching customer feedback for service trends.")

            if not service_df.empty:
                st.markdown("### Customer / Guest Feedback Link")
                s1, s2, s3 = st.columns(3)
                s1.metric("Personal Feedback", len(target_feedback_df))
                s2.metric("Personal Avg Stars", f"{avg_target_stars:.1f}" if avg_target_stars is not None else "-")
                s3.metric("Branch Team Avg Stars", f"{avg_branch_stars:.1f}" if avg_branch_stars is not None else "-")
                service_view = service_df.copy()
                service_view["stars"] = pd.to_numeric(service_view["stars"], errors="coerce").fillna(0)
                st.dataframe(service_view[[c for c in ["created_at", "feedback_scope", "stars", "message"] if c in service_view.columns]], use_container_width=True, hide_index=True)

            labels = [f"#{int(row['id'])} | {row['metric_name']} | {row['current_value']}/{row['target_value']} {row['unit']}" for _, row in kpi_view.iterrows()]
            selected_label = st.selectbox("Update progress", labels, key="employee_kpi_pick")
            selected_row = kpi_view.iloc[labels.index(selected_label)]
            with st.form("employee_kpi_update_form", clear_on_submit=False):
                progress_value = st.number_input(
                    "Current progress value",
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
                save_progress = st.form_submit_button("Save KPI Progress")
                if save_progress:
                    execute_write(
                        conn,
                        """
                        UPDATE kpi_targets
                        SET current_value=?, note=?, status=?, updated_by=?, updated_at=datetime('now')
                        WHERE id=? AND organization=?
                        """,
                        (float(progress_value), progress_note.strip(), progress_status, username, int(selected_row.get("id", 0) or 0), org),
                    )
                    execute_write(
                        conn,
                        """
                        INSERT INTO kpi_progress_updates(kpi_id, organization, branch, target_username, progress_value, progress_note, updated_by, created_at)
                        VALUES(?,?,?,?,?,?,?,datetime('now'))
                        """,
                        (int(selected_row.get("id", 0) or 0), org, branch, username, float(progress_value), progress_note.strip(), username),
                    )
                    conn.commit()
                    refresh_with_message("KPI progress updated.")

    # =====================================================
    # MY HR DOCUMENTS
    # =====================================================
    elif page == "My HR Documents":
        st.subheader("My HR Documents")
        st.caption("Documents shared with you by HR or super admin will appear here. You can download them and acknowledge receipt.")

        if not employee_docs_enabled:
            st.info("HR documents are not enabled for your organization.")
        else:
            docs_df = _safe_read(
                conn,
                """
                SELECT id, title, doc_type, note, file_name, file_path, mime_type, file_size,
                       created_at, uploaded_by, employee_acknowledged, acknowledged_at, acknowledged_note
                FROM hr_documents
                WHERE organization=? AND username=?
                  AND lower(coalesce(visibility,'hr'))='employee'
                  AND (trim(coalesce(branch,''))='' OR lower(trim(coalesce(branch,'')))=lower(trim(?)))
                ORDER BY id DESC
                """,
                params=(org, username, branch),
            )

            if docs_df.empty:
                st.info("No HR documents have been shared with you yet.")
            else:
                docs_view = docs_df.copy()
                docs_view["employee_acknowledged"] = docs_view["employee_acknowledged"].apply(lambda v: "Yes" if int(v or 0) == 1 else "No")
                st.dataframe(
                    docs_view[[c for c in ["created_at", "title", "doc_type", "uploaded_by", "employee_acknowledged", "acknowledged_at", "file_name", "file_size"] if c in docs_view.columns]],
                    use_container_width=True,
                    hide_index=True,
                )

                for _, doc in docs_df.iterrows():
                    doc_id = int(doc.get("id", 0) or 0)
                    with st.expander(f"{str(doc.get('title', 'Document'))} | {str(doc.get('created_at', ''))[:16]}"):
                        st.write(str(doc.get("note", "") or "No note provided."))
                        file_path = str(doc.get("file_path", "") or "")
                        if file_path and os.path.exists(file_path):
                            with open(file_path, "rb") as handle:
                                st.download_button(
                                    "Download document",
                                    data=handle.read(),
                                    file_name=str(doc.get("file_name", "document.bin") or "document.bin"),
                                    mime=str(doc.get("mime_type", "application/octet-stream") or "application/octet-stream"),
                                    key=f"employee_hr_doc_download_{doc_id}",
                                )
                        else:
                            st.warning("This file is no longer available on disk.")

                        acknowledged = int(doc.get("employee_acknowledged", 0) or 0) == 1
                        if acknowledged:
                            ack_when = str(doc.get("acknowledged_at", "") or "").strip()
                            ack_note = str(doc.get("acknowledged_note", "") or "").strip()
                            st.success(f"Acknowledged{(' on ' + ack_when[:16]) if ack_when else ''}.")
                            if ack_note:
                                st.caption(f"Your note: {ack_note}")
                        else:
                            ack_note = st.text_area("Acknowledgement note (optional)", key=f"employee_doc_ack_note_{doc_id}")
                            if st.button("Acknowledge receipt", key=f"employee_doc_ack_btn_{doc_id}"):
                                execute_write(
                                    conn,
                                    """
                                    UPDATE hr_documents
                                    SET employee_acknowledged=1, acknowledged_at=datetime('now'), acknowledged_note=?
                                    WHERE id=? AND organization=? AND username=?
                                    """,
                                    (ack_note.strip(), doc_id, org, username),
                                )
                                conn.commit()
                                refresh_with_message("Document acknowledged successfully.")

    # =====================================================
    # MY ONBOARDING
    # =====================================================
    elif page == "My Onboarding":
        st.subheader("My Onboarding Checklist")
        st.caption("Track your onboarding tasks and update progress as you complete each step.")

        if not employee_onboarding_enabled:
            st.info("Onboarding checklist automation is not enabled for your organization.")
        else:
            onboarding_df = _safe_read(
                conn,
                """
                SELECT id, checklist_name, task_name, status, note, due_date, assigned_by, completed_at, created_at
                FROM hr_onboarding_checklists
                WHERE organization=? AND username=?
                  AND (trim(coalesce(branch,''))='' OR lower(trim(coalesce(branch,'')))=lower(trim(?)))
                ORDER BY id DESC
                """,
                params=(org, username, branch),
            )

            if onboarding_df.empty:
                st.info("No onboarding tasks have been assigned to you yet.")
            else:
                status_series = onboarding_df["status"].astype(str).str.lower()
                total_tasks = len(onboarding_df)
                done_tasks = int((status_series == "done").sum())
                in_progress_tasks = int((status_series == "in_progress").sum())
                pending_tasks = int((status_series == "pending").sum())
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Tasks", total_tasks)
                c2.metric("Done", done_tasks)
                c3.metric("In Progress", in_progress_tasks)
                c4.metric("Pending", pending_tasks)
                st.dataframe(onboarding_df, use_container_width=True, hide_index=True)

                task_labels = [
                    f"#{int(row['id'])} | {row['task_name']} | {row['status']}"
                    for _, row in onboarding_df.iterrows()
                ]
                selected_task = st.selectbox("Update a task", task_labels, key="employee_onboarding_pick")
                task_row = onboarding_df.iloc[task_labels.index(selected_task)]
                current_status = str(task_row.get("status", "pending") or "pending")
                with st.form("employee_onboarding_update_form", clear_on_submit=False):
                    new_status = st.selectbox(
                        "Task status",
                        ["pending", "in_progress", "done"],
                        index=["pending", "in_progress", "done"].index(current_status) if current_status in ["pending", "in_progress", "done"] else 0,
                    )
                    progress_note = st.text_area("Progress note", value=str(task_row.get("note", "") or ""))
                    submit_task = st.form_submit_button("Save progress")
                    if submit_task:
                        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_status == "done" else ""
                        execute_write(
                            conn,
                            """
                            UPDATE hr_onboarding_checklists
                            SET status=?, note=?, completed_at=?
                            WHERE id=? AND organization=? AND username=?
                            """,
                            (new_status, progress_note.strip(), completed_at, int(task_row.get("id", 0) or 0), org, username),
                        )
                        conn.commit()
                        refresh_with_message("Onboarding task updated.")

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
            "SELECT * FROM ratings WHERE rated=? AND organization=?",
            conn, params=(username, org)
        )

        df = filter_df(df)

        if df.empty:
            st.info("No data")
        else:
            avg = df.groupby("topic")["score"].mean()
            st.bar_chart(avg)
            avg_score_val = round(float(avg.mean()), 1)
            st.metric("Average", f"{avg_score_val:.1f}/100 ({avg_score_val:.1f}%)")
            st.caption(recommendation(avg.mean()))

    # =====================================================
    # ANALYTICS
    # =====================================================
    elif page == "Analytics":

        df = pd.read_sql(
            "SELECT * FROM ratings WHERE rated=? AND organization=?",
            conn, params=(username, org)
        )

        if df.empty:
            st.info("No analytics yet")
        else:
            df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
            range_sel = nav_selectbox(
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
    # POLLS
    # =====================================================
    elif page == "Polls":
        st.subheader("Active Polls")
        st.caption("Questions appear one by one. After you submit, the form resets and the next question opens automatically. Previous answers are locked.")
        st.info(
            "🔒 Privacy Notice: your responses are not shown to peers or branch managers. "
            "If a poll is marked fully anonymous, even the Managing Director / super admin cannot identify you. "
            "If anonymity is turned off for an investigation poll, only the Managing Director / super admin may see names."
        )

        polls_df = get_visible_polls(
            conn,
            org,
            viewer_branch=branch,
            viewer_role="employee",
            include_closed=False,
        )

        poll_records = polls_df.to_dict("records") if not polls_df.empty else []
        answered_items = []
        unanswered_items = []
        for row in poll_records:
            existing = get_user_poll_response(conn, int(row.get("id", 0)), username)
            if existing:
                answered_items.append((row, existing))
            else:
                unanswered_items.append(row)

        if poll_records:
            p1, p2, p3 = st.columns(3)
            p1.metric("Total Questions", len(poll_records))
            p2.metric("Answered", len(answered_items))
            p3.metric("Remaining", len(unanswered_items))

        if unanswered_items:
            current_poll = unanswered_items[0]
            poll_id = int(current_poll["id"])
            current_number = len(answered_items) + 1
            total_questions = len(poll_records)
            scope_label = "All Branches" if not str(current_poll.get("branch", "")).strip() else str(current_poll.get("branch", ""))
            anonymous_flag = int(current_poll.get("anonymous", 1)) == 1
            privacy_label = "Anonymous to peers, managers, and MD" if anonymous_flag else "Hidden from peers/managers; visible only to MD if needed"
            expires_text = str(current_poll.get("expires_at", "") or "").strip()
            options = ["Yes", "No"] + (["Custom"] if int(current_poll.get("allow_custom", 1)) == 1 else [])

            st.markdown(f"### Current Question ({current_number}/{total_questions})")
            st.write(str(current_poll.get("question", "")))
            meta_bits = [f"Scope: {scope_label}", f"Privacy: {privacy_label}"]
            if expires_text:
                meta_bits.append(f"Deadline: {expires_text[:16]}")
            st.caption(" | ".join(meta_bits))

            with st.form(f"employee_poll_vote_{poll_id}", clear_on_submit=True):
                answer_choice = st.radio(
                    "Your answer",
                    options,
                    key=f"employee_poll_choice_{poll_id}",
                )
                custom_answer = ""
                if answer_choice == "Custom":
                    custom_answer = st.text_input(
                        "Type custom answer / name / word",
                        key=f"employee_poll_custom_{poll_id}",
                    )
                submit_vote = st.form_submit_button("Submit Answer")
                if submit_vote:
                    ok, message = submit_poll_response(
                        conn,
                        poll_id,
                        org,
                        username,
                        "employee",
                        responder_branch=branch,
                        answer_choice=answer_choice,
                        custom_answer=custom_answer,
                    )
                    if ok:
                        refresh_with_message("Answer saved. Moving to the next question.")
                    else:
                        st.error(message)
        else:
            if poll_records:
                st.success("✅ Thank you. You have completed all poll questions.")
                st.info("Your answers have been recorded securely. Previous answers are locked and cannot be edited.")
            else:
                st.info("No active polls for you right now.")

        if answered_items:
            st.markdown("### Answered Questions (Locked)")
            for row, existing in answered_items:
                poll_id = int(row.get("id", 0))
                saved_answer = str(existing.get("custom_answer", "") or existing.get("response_choice", ""))
                with st.expander(f"#{poll_id} • {row.get('question', '')}"):
                    st.success(f"Submitted answer: {saved_answer}")
                    st.caption("Locked after submit. You cannot go back to change this answer.")

    # =====================================================
    # MESSAGE MANAGEMENT
    # =====================================================
    elif page == "Message Management":
        inbox_df = _safe_read(
            conn,
            """
            SELECT sender, message, created_at
            FROM messages
            WHERE receiver=? AND branch=? AND organization=?
            ORDER BY id DESC
            """,
            params=(username, branch, org),
        )
        if not inbox_df.empty:
            st.markdown("### Replies from Management")
            st.dataframe(inbox_df, use_container_width=True)

        sent_df = _safe_read(
            conn,
            """
            SELECT receiver, message, created_at
            FROM messages
            WHERE sender=? AND branch=? AND organization=?
            ORDER BY id DESC
            """,
            params=(username, branch, org),
        )

        with st.form("employee_message_form", clear_on_submit=True):
            msg = st.text_area("Message to Management")
            send = st.form_submit_button("Send")
            if send:
                clean_msg = msg.strip()
                if not clean_msg:
                    st.error("Message cannot be empty")
                elif is_recent_duplicate_message(conn, username, "management", org, branch, clean_msg):
                    refresh_with_message("Duplicate message blocked. The same message was already sent just now.", level="warning")
                else:
                    execute_write(conn, """
                    INSERT INTO messages(sender,receiver,branch,organization,message,created_at)
                    VALUES (?,?,?,?,?,datetime('now'))
                    """,(username,'management',branch,org,clean_msg))
                    conn.commit()
                    refresh_with_message("Sent to Management")

        if not sent_df.empty:
            st.markdown("### Sent Messages")
            st.dataframe(sent_df, use_container_width=True)

    # =====================================================
    # SETTINGS
    # =====================================================
    elif page == "Settings":

        current = st.text_input("Current Password", type="password")
        new = st.text_input("New Password", type="password")

        if st.button("Update"):
            stored = pd.read_sql(
                "SELECT password FROM users WHERE username=? AND organization=? AND branch=?",
                conn,
                params=(username, org, branch)
            ).iloc[0]["password"]

            if verify_password(current, stored):
                execute_write(
                    conn,
                    "UPDATE users SET password=? WHERE username=? AND organization=? AND branch=?",
                    (hash_password(new), username, org, branch)
                )
                conn.commit()
                st.success("Updated")
                refresh()
            else:
                st.error("Wrong password")

        st.markdown("### Change PIN")
        current_pin = st.text_input("Current PIN", type="password")
        new_pin = st.text_input("New PIN", type="password")
        confirm_pin = st.text_input("Confirm New PIN", type="password")

        if st.button("Update PIN"):
            stored_pin = str(
                pd.read_sql(
                    "SELECT pin FROM users WHERE username=? AND organization=? AND branch=?",
                    conn,
                    params=(username, org, branch),
                ).iloc[0]["pin"]
            ).strip()

            if not current_pin.strip() or current_pin.strip() != stored_pin:
                st.error("Wrong PIN")
            elif not new_pin.strip():
                st.error("New PIN is required")
            elif new_pin != confirm_pin:
                st.error("PINs do not match")
            else:
                execute_write(
                    conn,
                    "UPDATE users SET pin=? WHERE username=? AND organization=? AND branch=?",
                    (new_pin.strip(), username, org, branch),
                )
                conn.commit()
                st.success("PIN updated")
                refresh()
