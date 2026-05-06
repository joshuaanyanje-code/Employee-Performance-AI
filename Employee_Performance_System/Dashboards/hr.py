import json
import os
import re
import time as pytime
from datetime import date, datetime

import pandas as pd
import streamlit as st

from database.db import cached_read_sql, get_connection, get_hr_config, log_action
from Dashboards.ui_responsive import (
    apply_responsive_ui,
    inject_global_css,
    render_dashboard_banner,
    render_topbar,
    render_note,
    render_sidebar_nav,
    render_stat_card,
)

try:
    from Dashboards.ui_responsive import is_mobile_device
except Exception:
    def is_mobile_device():
        return False


DEFAULT_ONBOARDING_TASKS = [
    "Issue contract and confirm signed acceptance",
    "Create staff profile and payroll setup",
    "Assign branch, badge, and attendance access",
    "Complete workplace orientation and policy briefing",
    "Assign tools/equipment and confirm receipt",
    "Schedule first-week manager check-in",
]

HR_UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads", "hr_documents")


def _safe_storage_name(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "file"


def _save_uploaded_hr_document(uploaded_file, organization, username):
    if uploaded_file is None:
        return "", "application/octet-stream", 0, ""
    org_key = _safe_storage_name(organization)
    user_key = _safe_storage_name(username)
    folder = os.path.join(HR_UPLOAD_ROOT, org_key, user_key)
    os.makedirs(folder, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    original_name = str(getattr(uploaded_file, "name", "document.bin") or "document.bin")
    stored_name = f"{timestamp}_{_safe_storage_name(original_name)}"
    file_path = os.path.join(folder, stored_name)
    data = uploaded_file.getvalue()
    with open(file_path, "wb") as handle:
        handle.write(data)
    return file_path, str(getattr(uploaded_file, "type", "application/octet-stream") or "application/octet-stream"), len(data), original_name


def refresh():
    st.session_state["_hr_refresh"] = st.session_state.get("_hr_refresh", 0) + 1
    st.rerun()


def refresh_with_message(message, level="success"):
    st.session_state["_hr_flash"] = {
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
        "_hr_flash",
    }
    for session_key in list(st.session_state.keys()):
        if session_key in keep_keys:
            continue
        if str(session_key).startswith("_"):
            continue
        st.session_state.pop(session_key, None)


def show_flash_message():
    payload = st.session_state.get("_hr_flash")
    if not payload:
        return

    created_at = float(payload.get("created_at", pytime.time()))
    duration = max(float(payload.get("duration", 2.0) or 2.0), 0.2)
    if (pytime.time() - created_at) >= duration:
        st.session_state.pop("_hr_flash", None)
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
        st.session_state["_hr_flash"] = payload


def safe_read(query, conn, params=None):
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


def _normalize_scope_branch(value):
    text = str(value or "").strip()
    if text.lower() in {"", "all", "all branches", "organization", "org", "hq"}:
        return None
    return text


def _scope_label(branch_value):
    return str(branch_value or "All Branches (Organization-wide HR)")


def _apply_scope_query(base_query, params, scope_branch):
    if scope_branch:
        return f"{base_query} AND branch=?", list(params) + [scope_branch]
    return base_query, list(params)


def _create_hr_request(conn, org, scope_branch, target_username, target_role, requested_by, action_type, payload):
    payload_text = json.dumps(payload, default=str)
    dup = safe_read(
        """
        SELECT id FROM admin_action_requests
        WHERE organization=? AND requested_by=? AND action_type=? AND target_username=?
          AND lower(coalesce(status, 'pending'))='pending' AND reason=?
        ORDER BY id DESC
        LIMIT 1
        """,
        conn,
        params=(org, requested_by, action_type, target_username, payload_text),
    )
    if not dup.empty:
        return False, "A similar HR request is already pending super admin review."

    conn.execute(
        """
        INSERT INTO admin_action_requests(
            organization, branch, target_username, target_role, requested_by,
            action_type, reason, status, created_at
        )
        VALUES(?,?,?,?,?,?,?,'pending',datetime('now'))
        """,
        (org, scope_branch or "", target_username, target_role, requested_by, action_type, payload_text),
    )
    conn.commit()
    return True, "Request submitted to super admin for final approval."


def _summarize_request_reason(value):
    text = str(value or "").strip()
    payload = {}
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = {}
    summary = str(payload.get("summary", text) or text).strip()
    extras = []
    if payload.get("new_role"):
        extras.append(f"Role -> {payload.get('new_role')}")
    if payload.get("to_branch"):
        extras.append(f"Transfer -> {payload.get('to_branch')}")
    if payload.get("effective_date"):
        extras.append(f"Effective {payload.get('effective_date')}")
    if extras:
        summary = " | ".join([summary] + extras) if summary else " | ".join(extras)
    return summary or "No reason provided."


def hr_dashboard():
    inject_global_css()

    conn = get_connection()
    username = st.session_state.get("username")
    org = st.session_state.get("organization")

    if not username or not org:
        st.error("Session missing HR user or organization.")
        return

    user_df = safe_read(
        "SELECT username, role, organization, branch, status FROM users WHERE username=? AND organization=? LIMIT 1",
        conn,
        params=(username, org),
    )
    if user_df.empty:
        st.error("HR user not found.")
        return

    hr_config = get_hr_config(conn, org)
    if not bool(int(hr_config.get("hr_mode_enabled", 0) or 0)):
        # Wireframe "disabled state" full-screen
        st.markdown(
            f"""
            <div style="min-height:480px;display:flex;flex-direction:column;align-items:center;
                        justify-content:center;gap:16px;padding:40px;">
                <div style="width:40px;height:40px;border:2px solid var(--line);border-radius:4px;
                            display:grid;place-items:center;font-size:20px;">🔒</div>
                <h3 style="margin:0;font-size:16px;font-weight:600;color:var(--onyx);">
                    HR Mode is OFF
                </h3>
                <p style="font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--smoke);
                          max-width:320px;text-align:center;margin:0;">
                    HR Mode is currently disabled for {org}.
                    Ask the Super Admin to enable it in Settings.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    hr_handles_leave = bool(int(hr_config.get("hr_handles_leave", 1) or 0))
    hr_handles_discipline = bool(int(hr_config.get("hr_handles_discipline", 1) or 0))
    hr_handles_performance = bool(int(hr_config.get("hr_handles_performance", 1) or 0))
    hr_handles_people_changes = bool(int(hr_config.get("hr_handles_people_changes", 1) or 0))
    hr_case_files_enabled = bool(int(hr_config.get("hr_case_files_enabled", 1) or 0))
    hr_documents_enabled = bool(int(hr_config.get("hr_documents_enabled", 1) or 0))
    hr_onboarding_enabled = bool(int(hr_config.get("hr_onboarding_enabled", 1) or 0))
    hr_requires_note = bool(int(hr_config.get("hr_require_recommendation_note", 1) or 0))

    if str(user_df.iloc[0].get("status", "active") or "active").strip().lower() == "suspended":
        st.error("This HR account is suspended. Contact the super admin.")
        return

    assigned_branch = _normalize_scope_branch(user_df.iloc[0].get("branch", ""))
    branches_df = safe_read("SELECT name FROM branches WHERE organization=? ORDER BY name", conn, params=(org,))
    branches = branches_df["name"].dropna().astype(str).tolist() if not branches_df.empty else []

    if assigned_branch is None:
        scope_options = ["All Branches"] + branches
        default_idx = 0
        selected_scope = st.selectbox("HR Scope", scope_options, index=default_idx, key="hr_scope_select")
        scope_branch = _normalize_scope_branch(selected_scope)
    else:
        scope_branch = assigned_branch

    scope_text = _scope_label(scope_branch)
    show_flash_message()

    # ── Sidebar nav ────────────────────────────────────────────────────────
    HR_MENU_ITEMS = [
        "Overview", "Leave Desk", "Discipline", "Performance",
        "People Changes", "Case Files", "Documents", "Onboarding", "Requests",
    ]
    if "hr_menu" not in st.session_state:
        st.session_state["hr_menu"] = "Overview"

    with st.sidebar:
        nav_html = render_sidebar_nav(
            f"{org} · HR",
            [{"header": "HR", "items": [
                {"label": m, "key": m} for m in HR_MENU_ITEMS
            ]},
             {"header": "Account", "items": [
                {"label": username, "key": "__profile__"},
            ]}],
            st.session_state["hr_menu"],
        )
        st.markdown(nav_html, unsafe_allow_html=True)
        cur_idx = HR_MENU_ITEMS.index(st.session_state["hr_menu"]) if st.session_state["hr_menu"] in HR_MENU_ITEMS else 0
        nav_choice = st.radio("hr_nav", HR_MENU_ITEMS, index=cur_idx, key="hr_nav_radio", label_visibility="collapsed")
        if nav_choice != st.session_state["hr_menu"]:
            st.session_state["hr_menu"] = nav_choice
            st.rerun()

    menu = st.session_state["hr_menu"]

    users_query, users_params = _apply_scope_query(
        "SELECT username, role, branch, status FROM users WHERE organization=? AND role IN ('employee','admin','hr')",
        [org],
        scope_branch,
    )
    users_df = safe_read(users_query + " ORDER BY branch, role, username", conn, params=tuple(users_params))

    leaves_query, leaves_params = _apply_scope_query(
        "SELECT id, username, branch, start_date, end_date, reason, status, approved_by, admin_note, reviewed_at FROM leaves WHERE organization=?",
        [org],
        scope_branch,
    )
    leaves_df = safe_read(leaves_query + " ORDER BY id DESC", conn, params=tuple(leaves_params))

    warnings_query, warnings_params = _apply_scope_query(
        "SELECT id, username, branch, type, message, created_at FROM warnings WHERE organization=?",
        [org],
        scope_branch,
    )
    warnings_df = safe_read(warnings_query + " ORDER BY created_at DESC", conn, params=tuple(warnings_params))

    ratings_query = "SELECT rated, branch, AVG(score) AS avg_score, COUNT(*) AS rating_count FROM ratings WHERE organization=?"
    ratings_params = [org]
    if scope_branch:
        ratings_query += " AND branch=?"
        ratings_params.append(scope_branch)
    ratings_query += " GROUP BY rated, branch ORDER BY avg_score ASC, rating_count DESC"
    performance_df = safe_read(ratings_query, conn, params=tuple(ratings_params))

    case_query, case_params = _apply_scope_query(
        "SELECT id, username, branch, case_type, title, note, status, visibility, created_by, updated_by, created_at, updated_at FROM hr_case_files WHERE organization=?",
        [org],
        scope_branch,
    )
    case_files_df = safe_read(case_query + " ORDER BY id DESC", conn, params=tuple(case_params))

    docs_query, docs_params = _apply_scope_query(
        "SELECT id, username, branch, title, doc_type, note, file_name, file_path, mime_type, file_size, visibility, employee_acknowledged, acknowledged_at, acknowledged_note, uploaded_by, created_at FROM hr_documents WHERE organization=?",
        [org],
        scope_branch,
    )
    documents_df = safe_read(docs_query + " ORDER BY id DESC", conn, params=tuple(docs_params))

    onboarding_query, onboarding_params = _apply_scope_query(
        "SELECT id, username, branch, checklist_name, task_name, status, note, due_date, assigned_by, completed_at, created_at FROM hr_onboarding_checklists WHERE organization=?",
        [org],
        scope_branch,
    )
    onboarding_df = safe_read(onboarding_query + " ORDER BY id DESC", conn, params=tuple(onboarding_params))

    if menu == "Overview":
        render_topbar(
            [org, "HR", "Overview"],
            chips=[(f"Scope: {scope_text}", False), ("hr", True)],
        )
        st.markdown('<div class="h-row"><h3>HR overview</h3></div>', unsafe_allow_html=True)
        render_note(
            "<b>Approval lane:</b> All HR actions you submit go to <b>Super Admin</b> for final approval. "
            "You review, HR approves in principle — Super Admin countersigns.",
            kind="info", pin="i",
        )

        pending_leaves = int((leaves_df["status"].astype(str).str.lower() == "pending").sum()) if not leaves_df.empty else 0
        pending_requests = safe_read(
            "SELECT COUNT(*) AS cnt FROM admin_action_requests WHERE organization=? AND requested_by=? AND lower(coalesce(status,'pending'))='pending'",
            conn,
            params=(org, username),
        )
        pending_count = int(pending_requests.iloc[0]["cnt"]) if not pending_requests.empty else 0
        low_perf = int((performance_df["avg_score"] < 55).sum()) if not performance_df.empty else 0
        open_cases = int((case_files_df["status"].astype(str).str.lower() != "closed").sum()) if not case_files_df.empty else 0
        document_count = len(documents_df)
        onboarding_pending = int((onboarding_df["status"].astype(str).str.lower() != "done").sum()) if not onboarding_df.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(render_stat_card("People in scope", len(users_df)), unsafe_allow_html=True)
        c2.markdown(render_stat_card("Pending Leave", pending_leaves), unsafe_allow_html=True)
        c3.markdown(render_stat_card("Open HR Cases", open_cases), unsafe_allow_html=True)
        c4.markdown(render_stat_card("Onboarding Pending", onboarding_pending), unsafe_allow_html=True)

        st.markdown("### Scope Staff")
        if users_df.empty:
            st.info("No staff data available in this HR scope.")
        else:
            st.dataframe(users_df, use_container_width=True, hide_index=True)

        st.markdown("### Performance watchlist")
        if performance_df.empty:
            st.info("No rating data yet.")
        else:
            st.dataframe(performance_df.head(20), use_container_width=True, hide_index=True)
            if low_perf:
                st.warning(f"{low_perf} staff member(s) are currently under the recommended 55% performance threshold.")

    elif menu == "Leave Desk":
        st.subheader("Leave Desk")
        st.caption("HR reviews pending leave requests and sends the final recommendation to super admin.")
        if hr_requires_note:
            st.caption("Current HR setting: a written recommendation note is required before anything is escalated to super admin.")

        if leaves_df.empty:
            st.info("No leave requests found in this HR scope.")
        else:
            st.dataframe(leaves_df, use_container_width=True, hide_index=True)
            if not hr_handles_leave:
                st.info("Super admin has not delegated leave approvals to HR for this organization, so this desk is currently view-only.")
            else:
                pending_df = leaves_df[leaves_df["status"].astype(str).str.lower() == "pending"].copy()
                if pending_df.empty:
                    st.info("No pending leave requests right now.")
                else:
                    st.markdown("### Submit HR recommendation")
                    for _, row in pending_df.iterrows():
                        leave_id = int(row.get("id", 0) or 0)
                        leave_user = str(row.get("username", "") or "")
                        leave_branch = str(row.get("branch", "") or "")
                        leave_dates = f"{str(row.get('start_date', ''))[:10]} to {str(row.get('end_date', ''))[:10]}"
                        with st.expander(f"{leave_user} | {leave_dates} | {leave_branch or 'N/A'}"):
                            st.write(str(row.get("reason", "No reason provided.")))
                            hr_note = st.text_area("HR note to super admin", key=f"hr_leave_note_{leave_id}")
                            clean_hr_note = hr_note.strip()
                            c1, c2, c3 = st.columns(3)
                            if c1.button("Recommend Approve", key=f"hr_leave_approve_{leave_id}"):
                                if hr_requires_note and not clean_hr_note:
                                    st.error("A recommendation note is required before sending this leave case to super admin.")
                                else:
                                    ok, msg = _create_hr_request(
                                        conn,
                                        org,
                                        leave_branch,
                                        leave_user,
                                        "employee",
                                        username,
                                        "hr_leave_approve",
                                        {
                                            "summary": clean_hr_note or f"HR recommends approving leave for {leave_user}.",
                                            "leave_id": leave_id,
                                            "requested_branch": leave_branch,
                                        },
                                    )
                                    log_action(conn, username, "HR RECOMMEND LEAVE APPROVE", leave_user, org)
                                    refresh_with_message(msg, level="success" if ok else "warning")
                            if c2.button("Recommend Reject", key=f"hr_leave_reject_{leave_id}"):
                                if hr_requires_note and not clean_hr_note:
                                    st.error("A recommendation note is required before sending this leave case to super admin.")
                                else:
                                    ok, msg = _create_hr_request(
                                        conn,
                                        org,
                                        leave_branch,
                                        leave_user,
                                        "employee",
                                        username,
                                        "hr_leave_reject",
                                        {
                                            "summary": clean_hr_note or f"HR recommends rejecting leave for {leave_user}.",
                                            "leave_id": leave_id,
                                            "requested_branch": leave_branch,
                                        },
                                    )
                                    log_action(conn, username, "HR RECOMMEND LEAVE REJECT", leave_user, org)
                                    refresh_with_message(msg, level="success" if ok else "warning")
                            if c3.button("Request Reapply", key=f"hr_leave_reapply_{leave_id}"):
                                if hr_requires_note and not clean_hr_note:
                                    st.error("A recommendation note is required before sending this leave case to super admin.")
                                else:
                                    ok, msg = _create_hr_request(
                                        conn,
                                        org,
                                        leave_branch,
                                        leave_user,
                                        "employee",
                                        username,
                                        "hr_leave_reapply",
                                        {
                                            "summary": clean_hr_note or f"HR requests leave reapply from {leave_user}.",
                                            "leave_id": leave_id,
                                            "requested_branch": leave_branch,
                                        },
                                    )
                                    log_action(conn, username, "HR REQUEST LEAVE REAPPLY", leave_user, org)
                                    refresh_with_message(msg, level="success" if ok else "warning")

    elif menu == "Discipline":
        st.subheader("Discipline Desk")
        if not hr_handles_discipline:
            st.info("Super admin has not delegated discipline management to HR for this organization.")
        else:
            employee_query, employee_params = _apply_scope_query(
                "SELECT username, branch FROM users WHERE organization=? AND role='employee' AND lower(coalesce(status,'active'))='active'",
                [org],
                scope_branch,
            )
            employees_df = safe_read(employee_query + " ORDER BY branch, username", conn, params=tuple(employee_params))
            if employees_df.empty:
                st.info("No employees available in this HR scope.")
            else:
                with st.form("hr_warning_request_form", clear_on_submit=False):
                    labels = [f"{row['username']} ({row['branch'] or 'N/A'})" for _, row in employees_df.iterrows()]
                    selected_label = st.selectbox("Employee", labels)
                    selected_row = employees_df.iloc[labels.index(selected_label)]
                    warn_type = st.selectbox(
                        "Warning Type",
                        ["lateness", "absenteeism", "low_performance", "misconduct", "policy_violation", "other"],
                    )
                    warn_msg = st.text_area("HR case summary / warning message")
                    if st.form_submit_button("Submit Warning Request to Super Admin"):
                        if not warn_msg.strip():
                            st.error("A warning summary is required.")
                        else:
                            ok, msg = _create_hr_request(
                                conn,
                                org,
                                str(selected_row.get("branch", "") or ""),
                                str(selected_row.get("username", "") or ""),
                                "employee",
                                username,
                                "hr_warning_issue",
                                {
                                    "summary": warn_msg.strip(),
                                    "warning_type": warn_type,
                                    "requested_branch": str(selected_row.get("branch", "") or ""),
                                },
                            )
                            log_action(conn, username, "HR SUBMIT WARNING REQUEST", str(selected_row.get("username", "")), org)
                            refresh_with_message(msg, level="success" if ok else "warning")

            if not warnings_df.empty:
                st.markdown("### Existing warning history")
                st.dataframe(warnings_df, use_container_width=True, hide_index=True)

    elif menu == "Performance":
        st.subheader("Performance Governance")
        if not hr_handles_performance:
            st.info("Super admin has not delegated performance governance to HR for this organization.")
        else:
            if performance_df.empty:
                st.info("No performance data available yet.")
            else:
                st.dataframe(performance_df, use_container_width=True, hide_index=True)
                focus_df = performance_df[performance_df["avg_score"] < 60].copy()
                if focus_df.empty:
                    st.success("No one is currently below the HR review threshold.")
                else:
                    st.markdown("### Escalate a people case")
                    if hr_requires_note:
                        st.caption("A written HR case note is required before super admin review.")
                    focus_labels = [f"{row['rated']} ({row['branch'] or 'N/A'}) - {float(row['avg_score'] or 0):.1f}%" for _, row in focus_df.iterrows()]
                    selected_case = st.selectbox("Staff member", focus_labels)
                    case_row = focus_df.iloc[focus_labels.index(selected_case)]
                    action_type = st.selectbox("Recommended action", ["probation", "suspend", "activate"])
                    case_note = st.text_area("Case note for super admin")
                    if st.button("Submit Performance Request", key="hr_perf_submit"):
                        clean_case_note = case_note.strip()
                        if hr_requires_note and not clean_case_note:
                            st.error("A case note is required before sending this performance request to super admin.")
                        else:
                            ok, msg = _create_hr_request(
                                conn,
                                org,
                                str(case_row.get("branch", "") or ""),
                                str(case_row.get("rated", "") or ""),
                                "employee",
                                username,
                                action_type,
                                {
                                    "summary": clean_case_note or f"HR recommends {action_type} for this performance case.",
                                    "avg_score": float(case_row.get("avg_score", 0) or 0),
                                    "requested_branch": str(case_row.get("branch", "") or ""),
                                },
                            )
                            log_action(conn, username, f"HR SUBMIT {action_type.upper()} REQUEST", str(case_row.get("rated", "")), org)
                            refresh_with_message(msg, level="success" if ok else "warning")

    elif menu == "People Changes":
        st.subheader("People Changes")
        st.caption("Handle onboarding/offboarding recommendations, transfer requests, and promotion or role-change escalations for final super admin approval.")

        if not hr_handles_people_changes:
            st.info("Super admin has not delegated onboarding, offboarding, transfer, and role-change workflows to HR for this organization.")
        else:
            change_candidates = users_df.copy()
            if change_candidates.empty:
                st.info("No staff available in this HR scope.")
            else:
                with st.form("hr_people_changes_form", clear_on_submit=False):
                    labels = [
                        f"{row['username']} ({row['role']} | {row['branch'] or 'N/A'} | {row['status']})"
                        for _, row in change_candidates.iterrows()
                    ]
                    selected_label = st.selectbox("Staff member", labels)
                    selected_row = change_candidates.iloc[labels.index(selected_label)]
                    change_type = st.selectbox(
                        "HR workflow",
                        [
                            "Onboard / Reactivate",
                            "Offboard / Deactivate",
                            "Promotion / Role Change",
                            "Transfer to another branch",
                        ],
                    )
                    current_role = str(selected_row.get("role", "employee") or "employee").strip().lower()
                    current_branch = str(selected_row.get("branch", "") or "").strip()
                    available_roles = [role for role in ["employee", "admin", "hr"] if role != current_role]
                    proposed_role = st.selectbox(
                        "Proposed role",
                        available_roles or [current_role],
                        disabled=change_type != "Promotion / Role Change",
                    )
                    transfer_branches = [branch for branch in branches if str(branch).strip() != current_branch]
                    destination_branch = st.selectbox(
                        "Destination branch",
                        transfer_branches or [current_branch or "No other branch available"],
                        disabled=change_type != "Transfer to another branch",
                    )
                    effective_date = st.date_input("Requested effective date", value=date.today())
                    change_note = st.text_area(
                        "HR note / recommendation",
                        placeholder="Explain the onboarding, exit, transfer, or promotion recommendation for super admin review.",
                    )
                    submit_change = st.form_submit_button("Send Workforce Change Request")

                    if submit_change:
                        clean_change_note = change_note.strip()
                        target_user = str(selected_row.get("username", "") or "")
                        target_role = str(selected_row.get("role", "employee") or "employee")
                        target_branch = str(selected_row.get("branch", "") or "")

                        if hr_requires_note and not clean_change_note:
                            st.error("An HR note is required before sending this workforce change to super admin.")
                        elif change_type == "Promotion / Role Change" and proposed_role == current_role:
                            st.error("Choose a different role before submitting a promotion or role-change request.")
                        elif change_type == "Transfer to another branch" and (not transfer_branches or destination_branch == current_branch):
                            st.error("Choose another branch for the transfer request.")
                        else:
                            if change_type == "Onboard / Reactivate":
                                action_type = "activate"
                                payload = {
                                    "summary": clean_change_note or f"HR recommends onboarding or reactivating {target_user}.",
                                    "case_kind": "onboarding",
                                    "effective_date": str(effective_date),
                                    "requested_branch": target_branch,
                                }
                            elif change_type == "Offboard / Deactivate":
                                action_type = "suspend"
                                payload = {
                                    "summary": clean_change_note or f"HR recommends offboarding or deactivating {target_user}.",
                                    "case_kind": "offboarding",
                                    "effective_date": str(effective_date),
                                    "requested_branch": target_branch,
                                }
                            elif change_type == "Promotion / Role Change":
                                action_type = "hr_role_change_request"
                                payload = {
                                    "summary": clean_change_note or f"HR recommends changing the role for {target_user} to {proposed_role}.",
                                    "new_role": proposed_role,
                                    "case_kind": "promotion",
                                    "effective_date": str(effective_date),
                                    "requested_branch": target_branch,
                                }
                            else:
                                action_type = "hr_transfer_request"
                                payload = {
                                    "summary": clean_change_note or f"HR recommends transferring {target_user} to {destination_branch}.",
                                    "from_branch": current_branch,
                                    "to_branch": destination_branch,
                                    "case_kind": "transfer",
                                    "effective_date": str(effective_date),
                                    "requested_branch": current_branch,
                                }

                            ok, msg = _create_hr_request(
                                conn,
                                org,
                                target_branch,
                                target_user,
                                target_role,
                                username,
                                action_type,
                                payload,
                            )
                            log_action(conn, username, f"HR SUBMIT {action_type.upper()} REQUEST", target_user, org)
                            refresh_with_message(msg, level="success" if ok else "warning")

            transfer_history_query, transfer_history_params = _apply_scope_query(
                "SELECT created_at, username, role, from_branch, to_branch, transferred_by, note, effective_date FROM staff_transfers WHERE organization=?",
                [org],
                scope_branch,
            )
            transfer_history_df = safe_read(transfer_history_query + " ORDER BY id DESC", conn, params=tuple(transfer_history_params))
            if not transfer_history_df.empty:
                st.markdown("### Approved transfer history")
                st.dataframe(transfer_history_df, use_container_width=True, hide_index=True)

    elif menu == "Case Files":
        st.subheader("Employee Case Files")
        st.caption("Create and keep formal HR notes for onboarding, exits, investigations, welfare follow-up, and employee history.")

        if not hr_case_files_enabled:
            st.info("Employee case files are currently disabled for HR in this organization.")
        else:
            case_people_df = users_df.copy()
            if case_people_df.empty:
                st.info("No staff found for HR case files in this scope.")
            else:
                with st.form("hr_case_file_form", clear_on_submit=False):
                    labels = [f"{row['username']} ({row['role']} | {row['branch'] or 'N/A'})" for _, row in case_people_df.iterrows()]
                    selected_label = st.selectbox("Employee / staff profile", labels, key="hr_case_file_user")
                    case_row = case_people_df.iloc[labels.index(selected_label)]
                    case_type = st.selectbox(
                        "Case type",
                        ["onboarding", "offboarding", "promotion", "transfer", "performance", "conduct", "leave_followup", "wellbeing", "investigation", "other"],
                    )
                    case_title = st.text_input("Case title")
                    case_status = st.selectbox("Case status", ["open", "in_review", "closed"])
                    case_visibility = st.selectbox("Visibility", ["hr", "super_admin", "employee"], help="Choose 'employee' only for notes that are safe for the employee to read.")
                    case_note = st.text_area("Case note")
                    save_case = st.form_submit_button("Save HR Case Note")

                    if save_case:
                        clean_title = case_title.strip()
                        clean_note = case_note.strip()
                        if not clean_title:
                            st.error("A case title is required.")
                        elif not clean_note:
                            st.error("A case note is required.")
                        else:
                            conn.execute(
                                """
                                INSERT INTO hr_case_files(
                                    organization, branch, username, case_type, title, note,
                                    status, visibility, created_by, updated_by, created_at, updated_at
                                )
                                VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                                """,
                                (
                                    org,
                                    str(case_row.get("branch", "") or ""),
                                    str(case_row.get("username", "") or ""),
                                    case_type,
                                    clean_title,
                                    clean_note,
                                    case_status,
                                    case_visibility,
                                    username,
                                    username,
                                ),
                            )
                            conn.commit()
                            log_action(conn, username, f"HR SAVE {case_type.upper()} CASE", str(case_row.get("username", "")), org)
                            refresh_with_message("HR case file saved.", level="success")

            if case_files_df.empty:
                st.info("No HR case files recorded yet in this scope.")
            else:
                case_view = case_files_df.copy()
                st.dataframe(case_view, use_container_width=True, hide_index=True)

    elif menu == "Documents":
        st.subheader("HR Documents")
        st.caption("Upload and store offer letters, signed forms, transfer letters, onboarding files, and policy acknowledgements for HR and super admin review.")

        if not hr_documents_enabled:
            st.info("HR document uploads are currently disabled for this organization.")
        else:
            doc_people_df = users_df.copy()
            if doc_people_df.empty:
                st.info("No staff found in this HR scope.")
            else:
                with st.form("hr_document_upload_form", clear_on_submit=False):
                    labels = [f"{row['username']} ({row['role']} | {row['branch'] or 'N/A'})" for _, row in doc_people_df.iterrows()]
                    selected_label = st.selectbox("Staff member", labels, key="hr_doc_user")
                    doc_row = doc_people_df.iloc[labels.index(selected_label)]
                    doc_title = st.text_input("Document title")
                    doc_type = st.selectbox("Document type", ["offer_letter", "contract", "id_copy", "certificate", "promotion_letter", "transfer_letter", "warning_notice", "policy_form", "other"])
                    doc_visibility = st.selectbox("Visibility", ["hr", "super_admin", "employee"], help="Choose 'employee' if the employee should be able to download and acknowledge this document.")
                    doc_note = st.text_area("Document note")
                    uploaded_file = st.file_uploader(
                        "Upload file",
                        type=["pdf", "doc", "docx", "txt", "png", "jpg", "jpeg"],
                        key="hr_doc_upload",
                    )
                    upload_sub = st.form_submit_button("Save HR Document")

                    if upload_sub:
                        clean_title = doc_title.strip()
                        clean_note = doc_note.strip()
                        target_user = str(doc_row.get("username", "") or "")
                        target_branch = str(doc_row.get("branch", "") or "")
                        if not clean_title:
                            st.error("A document title is required.")
                        elif uploaded_file is None:
                            st.error("Choose a file to upload.")
                        else:
                            file_path, mime_type, file_size, original_name = _save_uploaded_hr_document(uploaded_file, org, target_user)
                            conn.execute(
                                """
                                INSERT INTO hr_documents(
                                    organization, branch, username, title, doc_type, note,
                                    file_name, file_path, mime_type, file_size, visibility, uploaded_by, created_at
                                )
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                                """,
                                (
                                    org,
                                    target_branch,
                                    target_user,
                                    clean_title,
                                    doc_type,
                                    clean_note,
                                    original_name,
                                    file_path,
                                    mime_type,
                                    int(file_size or 0),
                                    doc_visibility,
                                    username,
                                ),
                            )
                            conn.commit()
                            log_action(conn, username, f"HR UPLOAD {doc_type.upper()} DOCUMENT", target_user, org)
                            refresh_with_message("HR document uploaded.", level="success")

            if documents_df.empty:
                st.info("No HR documents uploaded yet in this scope.")
            else:
                docs_view = documents_df.copy()
                if "employee_acknowledged" in docs_view.columns:
                    docs_view["employee_acknowledged"] = docs_view["employee_acknowledged"].apply(lambda v: "Yes" if int(v or 0) == 1 else "No")
                st.dataframe(
                    docs_view[[col for col in ["created_at", "username", "branch", "title", "doc_type", "visibility", "employee_acknowledged", "acknowledged_at", "uploaded_by", "file_name", "file_size"] if col in docs_view.columns]],
                    use_container_width=True,
                    hide_index=True,
                )
                for _, doc in documents_df.head(12).iterrows():
                    with st.expander(f"{str(doc.get('title', 'Document'))} | {str(doc.get('username', ''))} | {str(doc.get('created_at', ''))[:16]}"):
                        st.write(str(doc.get("note", "") or "No note provided."))
                        file_path = str(doc.get("file_path", "") or "")
                        if file_path and os.path.exists(file_path):
                            with open(file_path, "rb") as handle:
                                st.download_button(
                                    "Download document",
                                    data=handle.read(),
                                    file_name=str(doc.get("file_name", "document.bin") or "document.bin"),
                                    mime=str(doc.get("mime_type", "application/octet-stream") or "application/octet-stream"),
                                    key=f"hr_doc_download_{int(doc.get('id', 0) or 0)}",
                                )
                        else:
                            st.warning("Stored file not found on disk.")

    elif menu == "Onboarding":
        st.subheader("Onboarding Checklist Automation")
        st.caption("Create and track onboarding tasks so HR and super admin can follow every new starter through setup, orientation, and branch readiness.")

        if not hr_onboarding_enabled:
            st.info("Onboarding checklist automation is currently disabled for this organization.")
        else:
            onboard_people_df = users_df[users_df["role"].astype(str).str.lower().isin(["employee", "admin", "hr"])].copy() if not users_df.empty else pd.DataFrame()
            if onboard_people_df.empty:
                st.info("No staff available in this HR scope.")
            else:
                with st.form("hr_onboarding_generate_form", clear_on_submit=False):
                    labels = [f"{row['username']} ({row['role']} | {row['branch'] or 'N/A'})" for _, row in onboard_people_df.iterrows()]
                    selected_label = st.selectbox("Staff profile", labels, key="hr_onboarding_user")
                    board_row = onboard_people_df.iloc[labels.index(selected_label)]
                    checklist_name = st.text_input("Checklist name", value="Standard Onboarding")
                    due_date = st.date_input("Target completion date", value=date.today())
                    generate_sub = st.form_submit_button("Generate Default Checklist")

                    if generate_sub:
                        target_user = str(board_row.get("username", "") or "")
                        target_branch = str(board_row.get("branch", "") or "")
                        checklist_title = checklist_name.strip() or "Standard Onboarding"
                        existing = safe_read(
                            """
                            SELECT id FROM hr_onboarding_checklists
                            WHERE organization=? AND username=? AND checklist_name=?
                            LIMIT 1
                            """,
                            conn,
                            params=(org, target_user, checklist_title),
                        )
                        if not existing.empty:
                            st.warning("A checklist with this name already exists for that staff member.")
                        else:
                            for task in DEFAULT_ONBOARDING_TASKS:
                                conn.execute(
                                    """
                                    INSERT INTO hr_onboarding_checklists(
                                        organization, branch, username, checklist_name, task_name,
                                        status, note, due_date, assigned_by, completed_at, created_at
                                    )
                                    VALUES(?,?,?,?,?,?,?, ?,?, '', datetime('now'))
                                    """,
                                    (org, target_branch, target_user, checklist_title, task, "pending", "", str(due_date), username),
                                )
                            conn.commit()
                            log_action(conn, username, "HR GENERATE ONBOARDING CHECKLIST", target_user, org)
                            refresh_with_message("Onboarding checklist generated.", level="success")

                if onboarding_df.empty:
                    st.info("No onboarding tasks created yet.")
                else:
                    st.dataframe(onboarding_df, use_container_width=True, hide_index=True)
                    task_labels = [
                        f"#{int(row['id'])} | {row['username']} | {row['task_name']} | {row['status']}"
                        for _, row in onboarding_df.iterrows()
                    ]
                    selected_task_label = st.selectbox("Update task", task_labels, key="hr_onboarding_task_pick")
                    task_row = onboarding_df.iloc[task_labels.index(selected_task_label)]
                    with st.form("hr_onboarding_update_form", clear_on_submit=False):
                        new_status = st.selectbox("Task status", ["pending", "in_progress", "done"], index=["pending", "in_progress", "done"].index(str(task_row.get("status", "pending") or "pending")) if str(task_row.get("status", "pending") or "pending") in ["pending", "in_progress", "done"] else 0)
                        task_note = st.text_area("Task note", value=str(task_row.get("note", "") or ""))
                        task_due = st.text_input("Due date", value=str(task_row.get("due_date", "") or ""))
                        update_task_sub = st.form_submit_button("Update Checklist Task")
                        if update_task_sub:
                            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_status == "done" else ""
                            conn.execute(
                                """
                                UPDATE hr_onboarding_checklists
                                SET status=?, note=?, due_date=?, completed_at=?
                                WHERE id=? AND organization=?
                                """,
                                (new_status, task_note.strip(), task_due.strip(), completed_at, int(task_row.get("id", 0) or 0), org),
                            )
                            conn.commit()
                            log_action(conn, username, f"HR UPDATE ONBOARDING TASK {new_status.upper()}", str(task_row.get("username", "")), org)
                            refresh_with_message("Onboarding task updated.", level="success")

    elif menu == "Requests":
        st.subheader("My HR Requests")
        requests_df = safe_read(
            """
            SELECT created_at, branch, target_username, action_type, status, review_note, reviewed_by, reviewed_at, reason
            FROM admin_action_requests
            WHERE organization=? AND requested_by=?
            ORDER BY id DESC
            """,
            conn,
            params=(org, username),
        )
        if requests_df.empty:
            st.info("No HR requests submitted yet.")
        else:
            display_df = requests_df.copy()
            if "reason" in display_df.columns:
                display_df["reason"] = display_df["reason"].apply(_summarize_request_reason)
            st.dataframe(display_df, use_container_width=True, hide_index=True)
