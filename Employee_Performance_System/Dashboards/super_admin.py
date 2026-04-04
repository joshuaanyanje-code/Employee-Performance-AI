import streamlit as st
import pandas as pd
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote
import importlib

try:
    holiday_lib = importlib.import_module("holidays")
    HOLIDAYS_OK = True
except Exception:
    holiday_lib = None
    HOLIDAYS_OK = False

from database.db import get_connection, hash_password, log_action, is_recent_duplicate_message
from Dashboards.ui_responsive import apply_responsive_ui
try:
    from Dashboards.ui_responsive import is_mobile_device
except Exception:
    def is_mobile_device():
        return False

# ==============================
# OPTIONAL ANALYTICS IMPORTS
# ==============================
try:
    from Analytics.reports import reports_panel
    from Analytics.insights import generate_insights
    from Analytics.leadership import detect_leaders
    from Analytics.powermap import display_powermap
    from Analytics.prediction import predict_future
    from Analytics.stability import stability_analysis
    from Analytics.decision_engine import management_recommendations
    from Analytics.super_admin_dashboard import get_super_admin_dashboard
    from Analytics.alerts_system import get_unread_alerts, resolve_alert, get_alert_statistics
    from Analytics.group_demographics import analyze_group_demographics, get_demographic_statistics, get_group_details_for_super_admin
    from Analytics.analytics_filter import filter_ratings_by_role, filter_users_by_role
    from Analytics.badges import compute_badge_summary_for_super_admin, get_badge_holders_table
    from Analytics.ai_recommendations import get_cached_recommendations
    ANALYTICS_OK = True
except Exception as _ae:
    ANALYTICS_OK = False
    def get_cached_recommendations():
        return {"articles": [], "fetched_at": "", "sources_ok": 0, "error": "Analytics module unavailable."}

try:
    import plotly.express as px
    PLOTLY_OK = True
except Exception:
    PLOTLY_OK = False


# ==============================
# HELPERS
# ==============================
def safe_df(df):
    return df is not None and not df.empty


def valid_pass(p):
    return p and len(p) >= 4


def safe_read(query, conn, params=None):
    try:
        return pd.read_sql(query, conn, params=params) if params else pd.read_sql(query, conn)
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


def clean_display_text(value):
    if value is None:
        return "-"

    text = str(value)
    try:
        repaired = text.encode("latin-1").decode("utf-8")
        if repaired and repaired != text:
            text = repaired
    except Exception:
        pass

    replacements = {
        "â€”": " - ",
        "â€“": " - ",
        "—": " - ",
        "Â·": " | ",
        "â€¢": "- ",
        "â€¦": "...",
        "âœ…": "",
        "âš ": "Warning ",
        "â„¹": "Info ",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)

    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or "-"


def extract_user_mentions(findings, usernames):
    if not findings or not usernames:
        return []

    findings_text = "\n".join(str(item) for item in findings)
    mentioned = []
    for username in usernames:
        candidate = str(username).strip()
        if not candidate:
            continue
        pattern = rf"(?<!\w){re.escape(candidate)}(?!\w)"
        if re.search(pattern, findings_text, flags=re.IGNORECASE):
            mentioned.append(candidate)

    return sorted(set(mentioned), key=str.lower)


def infer_relationship_label(text):
    t = str(text or "").upper()
    if "CONFLICT" in t or "ESCALATION" in t:
        return "Conflict"
    if "FAVORIT" in t or "BIAS" in t:
        return "Favoritism/Bias"
    if "DATING" in t or "RELATIONSHIP" in t:
        return "Dating/Relationship"
    if "ISOLATION" in t:
        return "Isolation/Targeting"
    if "BAD SEED" in t or "TOXIC" in t:
        return "Toxic Influence"
    if "WITHDRAWAL" in t:
        return "Withdrawal Risk"
    return "General"


def build_who_against_who_rows(findings, usernames, source_label):
    rows = []
    if not findings:
        return rows

    for finding in findings:
        finding_text = str(finding)
        mentions = extract_user_mentions([finding_text], usernames)
        relationship = infer_relationship_label(finding_text)

        if len(mentions) >= 2:
            for i in range(len(mentions)):
                for j in range(i + 1, len(mentions)):
                    rows.append({
                        "Source": source_label,
                        "Person A": mentions[i],
                        "Person B": mentions[j],
                        "Relationship": relationship,
                        "Evidence": finding_text,
                    })
        elif len(mentions) == 1:
            rows.append({
                "Source": source_label,
                "Person A": mentions[0],
                "Person B": "",
                "Relationship": relationship,
                "Evidence": finding_text,
            })

    return rows


def build_favorite_relationship_tables(ratings_df):
    if ratings_df is None or ratings_df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    rel = ratings_df.copy()

    # Backward-compatible support for schemas that still use `ratee`.
    if "rated" not in rel.columns and "ratee" in rel.columns:
        rel = rel.rename(columns={"ratee": "rated"})

    required_cols = {"rater", "rated", "score"}
    if not required_cols.issubset(set(rel.columns)):
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    rel["rater"] = rel["rater"].astype(str).str.strip()
    rel["rated"] = rel["rated"].astype(str).str.strip()
    rel = rel[(rel["rater"] != "") & (rel["rated"] != "")]
    rel = rel[rel["rater"] != rel["rated"]]
    rel["score"] = pd.to_numeric(rel["score"], errors="coerce")
    rel = rel.dropna(subset=["score"])

    if rel.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    agg_map = {
        "ratings_count": ("score", "count"),
        "avg_score": ("score", "mean"),
    }
    if "created_at" in rel.columns:
        rel["created_at"] = pd.to_datetime(rel["created_at"], errors="coerce")
        agg_map["last_rating"] = ("created_at", "max")

    pair_summary = rel.groupby(["rater", "rated"], as_index=False).agg(**agg_map)
    pair_summary["avg_score"] = pair_summary["avg_score"].round(2)
    if "last_rating" in pair_summary.columns:
        pair_summary["last_rating"] = pair_summary["last_rating"].dt.strftime("%Y-%m-%d %H:%M").fillna("")
    else:
        pair_summary["last_rating"] = ""

    ranked_pairs = pair_summary.sort_values(["ratings_count", "avg_score"], ascending=[False, False]).reset_index(drop=True)

    favorite_to = ranked_pairs.groupby("rater", as_index=False).head(1).copy()
    favorite_to = favorite_to.rename(
        columns={
            "rater": "User",
            "rated": "Favorite To",
            "ratings_count": "Ratings Given",
            "avg_score": "Avg Score Given",
            "last_rating": "Last Rating",
        }
    )
    favorite_to = favorite_to[["User", "Favorite To", "Ratings Given", "Avg Score Given", "Last Rating"]]

    favored_by = ranked_pairs.groupby("rated", as_index=False).head(1).copy()
    favored_by = favored_by.rename(
        columns={
            "rated": "User",
            "rater": "Favored By",
            "ratings_count": "Ratings Received",
            "avg_score": "Avg Score Received",
            "last_rating": "Last Rating",
        }
    )
    favored_by = favored_by[["User", "Favored By", "Ratings Received", "Avg Score Received", "Last Rating"]]

    relationship_top = ranked_pairs.rename(
        columns={
            "rater": "From",
            "rated": "To",
            "ratings_count": "Ratings Count",
            "avg_score": "Avg Score",
            "last_rating": "Last Rating",
        }
    )
    relationship_top = relationship_top[["From", "To", "Ratings Count", "Avg Score", "Last Rating"]]

    return favorite_to, favored_by, relationship_top


def render_favorite_relationship_section(ratings_df, scope_label):
    st.markdown("### Favorite Relationships")
    st.caption(f"Scope: {scope_label}")

    try:
        fav_to_df, favored_by_df, top_pairs_df = build_favorite_relationship_tables(ratings_df)
        if fav_to_df.empty and favored_by_df.empty:
            st.info("Not enough rating relationship data yet to determine favorites.")
            return

        rel_col1, rel_col2 = st.columns(2)
        with rel_col1:
            st.markdown("**Who Is Favorite To Who**")
            if fav_to_df.empty:
                st.info("No outbound favorite links yet.")
            else:
                fav_to_df = fav_to_df.copy().fillna("")
                fav_to_df["User"] = fav_to_df["User"].astype(str)
                fav_to_df["Favorite To"] = fav_to_df["Favorite To"].astype(str)
                st.dataframe(fav_to_df.sort_values(["User"]), use_container_width=True)

        with rel_col2:
            st.markdown("**Who Is Favored By Who (Vice Versa)**")
            if favored_by_df.empty:
                st.info("No inbound favorite links yet.")
            else:
                favored_by_df = favored_by_df.copy().fillna("")
                favored_by_df["User"] = favored_by_df["User"].astype(str)
                favored_by_df["Favored By"] = favored_by_df["Favored By"].astype(str)
                st.dataframe(favored_by_df.sort_values(["User"]), use_container_width=True)

        st.markdown("**Top Rating Relationships**")
        top_pairs_df = top_pairs_df.copy().fillna("")
        top_pairs_df["From"] = top_pairs_df["From"].astype(str)
        top_pairs_df["To"] = top_pairs_df["To"].astype(str)
        st.dataframe(top_pairs_df.head(25), use_container_width=True)
    except Exception as e:
        st.warning(f"Favorite relationship analytics unavailable: {e}")


def get_holiday_preview(country_code, subdivision, year):
    if not HOLIDAYS_OK:
        return pd.DataFrame()

    country_code = str(country_code or "KE").strip().upper()
    subdivision = str(subdivision or "").strip()

    try:
        if subdivision:
            holiday_set = holiday_lib.country_holidays(country_code, subdiv=subdivision, years=[int(year)])
        else:
            holiday_set = holiday_lib.country_holidays(country_code, years=[int(year)])
    except Exception:
        try:
            holiday_set = holiday_lib.country_holidays(country_code, years=[int(year)])
        except Exception:
            return pd.DataFrame()

    rows = [{"date": dt.strftime("%Y-%m-%d"), "holiday_name": str(name)} for dt, name in sorted(holiday_set.items())]
    return pd.DataFrame(rows)


def annotate_attendance_lateness(att_df, lateness_df):
    if att_df is None or att_df.empty:
        return pd.DataFrame() if att_df is None else att_df

    out = att_df.copy()
    out["lateness_request_status"] = "not_requested"
    out["lateness_reason"] = ""
    out["lateness_admin_note"] = ""
    out["lateness_approved_by"] = ""
    out["approved_late"] = False
    out["true_late"] = out["status"].astype(str).str.upper() == "LATE"
    out["late_status_label"] = out["status"].astype(str)

    if lateness_df is None or lateness_df.empty:
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
                out.at[idx, "late_status_label"] = "APPROVED LATE"
            elif base_status:
                out.at[idx, "late_status_label"] = f"{base_status} (APPROVED LATE)"
            else:
                out.at[idx, "late_status_label"] = "APPROVED LATE"

    return out


def kiosk_link(branch, org):
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


def calc_plan_price(branch_count, cfg_row):
    try:
        single_price = int(cfg_row.get("price_single_branch", 1000))
    except Exception:
        single_price = 1000
    try:
        per_branch_price = int(cfg_row.get("price_per_branch", 800))
    except Exception:
        per_branch_price = 800

    if int(branch_count) <= 1:
        return single_price
    return int(branch_count) * per_branch_price


def build_executive_bi_report(intel):
    meta = intel.get("executive_summary", {})
    biz = intel.get("business_intelligence", {})
    pay = biz.get("payment_trend", {})
    plan = intel.get("branch_action_plan", {})
    monthly = intel.get("monthly_trends", {})

    lines = [
        "EXECUTIVE BUSINESS INTELLIGENCE REPORT",
        f"Generated At: {intel.get('generated_at', '')}",
        f"Organization: {intel.get('organization', '')}",
        f"Branch Scope: {intel.get('branch', 'All Branches')}",
        "",
        "EXECUTIVE SUMMARY",
        f"Employees: {meta.get('total_employees', 0)}",
        f"Team Health: {meta.get('team_health_score', 0):.0f}% ({meta.get('team_health_status', 'Unknown')})",
        f"Business Health: {biz.get('business_health_score', 0):.0f}% ({biz.get('business_health_status', 'Unknown')})",
        f"Active Branches: {biz.get('active_branches', 0)}",
        f"Collections Last 30 Days: KES {pay.get('last_30_days', 0):,.2f}",
        f"Collections Trend: {pay.get('trend_direction', 'Stable')} ({pay.get('trend_delta', 0):,.2f})",
        "",
        "PRIORITIES",
    ]

    for item in biz.get("priorities", []):
        lines.append(f"- {item}")

    lines.extend([
        "",
        "GROWTH OPPORTUNITIES",
    ])
    for item in biz.get("growth_opportunities", []):
        lines.append(f"- {item}")

    lines.extend([
        "",
        "OPERATIONAL RISKS",
    ])
    for item in biz.get("operational_risks", []):
        lines.append(f"- {item}")

    lines.extend([
        "",
        "WEAKEST BRANCH ACTION PLAN",
        f"Target Branch: {plan.get('target_branch', 'N/A')}",
        f"Urgency: {plan.get('urgency', 'Low')}",
    ])
    for item in plan.get("actions", []):
        lines.append(f"- {item}")

    lines.extend([
        "",
        "MONTHLY TREND SUMMARY",
    ])
    for item in monthly.get("trend_summary", []):
        lines.append(f"- {item}")

    forecast = monthly.get("forecast", {})
    if forecast:
        lines.extend([
            "",
            "NEXT MONTH FORECAST",
            f"Month: {forecast.get('next_month', 'N/A')}",
            f"Forecast Collections: KES {forecast.get('collections', 0):,.2f}",
            f"Forecast Internal Score: {forecast.get('avg_internal_score', 0):,.2f}",
            f"Forecast Attendance Records: {forecast.get('attendance_records', 0):,.0f}",
        ])

    return "\n".join(lines)


# ==============================
# MAIN DASHBOARD
# ==============================
def super_admin_dashboard():

    apply_responsive_ui("default")

    conn = get_connection()
    user = st.session_state.get("username")
    org  = st.session_state.get("organization")

    if not org:
        st.error("Organization not assigned")
        st.stop()

    # Fetch business type once per session so every section can use it
    if "sa_business_type" not in st.session_state or st.session_state.get("sa_org_for_btype") != org:
        try:
            _org_meta = safe_read(
                "SELECT business_type FROM organizations WHERE name=?", conn, params=(org,)
            )
            st.session_state["sa_business_type"] = (
                str(_org_meta["business_type"].iloc[0])
                if not _org_meta.empty and _org_meta["business_type"].iloc[0]
                else "Office"
            )
        except Exception:
            st.session_state["sa_business_type"] = "Office"
        st.session_state["sa_org_for_btype"] = org

    org_business_type = st.session_state["sa_business_type"]

    st.title(f"Managing Director - {org}")

    branches_raw = safe_read("SELECT name FROM branches WHERE organization=?", conn, params=(org,))
    branches = branches_raw["name"].tolist() if not branches_raw.empty else []
    usernames_raw = safe_read("SELECT username FROM users WHERE organization=?", conn, params=(org,))
    org_usernames = usernames_raw["username"].dropna().astype(str).tolist() if not usernames_raw.empty else []

    is_mobile = is_mobile_device()

    def _collapse_sa_mobile_nav():
        if is_mobile:
            st.session_state["sa_nav_open"] = False

    if "sa_nav_open" not in st.session_state:
        st.session_state["sa_nav_open"] = True

    def nav_selectbox(label, options, key, **kwargs):
        if is_mobile:
            return st.selectbox(label, options, key=key, **kwargs)
        with st.sidebar:
            return st.selectbox(label, options, key=key, **kwargs)

    def nav_radio(label, options, key, horizontal=False, **kwargs):
        if is_mobile:
            return st.radio(label, options, key=key, horizontal=horizontal, **kwargs)
        with st.sidebar:
            return st.radio(label, options, key=key, **kwargs)

    def nav_date_input(label, value, key, **kwargs):
        if is_mobile:
            return st.date_input(label, value=value, key=key, **kwargs)
        with st.sidebar:
            return st.date_input(label, value=value, key=key, **kwargs)

    nav_items = [
        "Overview",
        "Management",
        "Analytics",
        "Risk Center",
        "Attendance",
        "Staff Check In",
        "Settings",
        "Payments",
        "Logs",
    ]

    if is_mobile:
        if st.button("Change Navigation", key="sa_reopen_nav", use_container_width=True):
            st.session_state["sa_nav_open"] = True
            st.rerun()
        with st.expander("Navigation", expanded=bool(st.session_state.get("sa_nav_open", True))):
            menu = st.radio("Navigation", nav_items, key="sa_menu", on_change=_collapse_sa_mobile_nav)
    else:
        with st.sidebar:
            st.markdown("### Navigation")
            menu = st.radio("Navigation", nav_items, key="sa_menu")

    management_view = None
    analytics_view = None
    risk_view = None

    if menu == "Management":
        if is_mobile:
            management_view = st.radio(
                "Management Area",
                ["Users", "Branches", "Operations"],
                horizontal=True,
                key="sa_management_view",
                on_change=_collapse_sa_mobile_nav,
            )
        else:
            with st.sidebar:
                st.markdown("### Management Area")
                management_view = st.radio(
                    "Management Area",
                    ["Users", "Branches", "Operations"],
                    key="sa_management_view",
                )

    if menu == "Analytics":
        if is_mobile:
            analytics_view = st.radio(
                "Analytics Area",
                ["Performance", "Intelligence", "Demographics", "Guest Experience"],
                horizontal=True,
                key="sa_analytics_view",
                on_change=_collapse_sa_mobile_nav,
            )
        else:
            with st.sidebar:
                st.markdown("### Analytics Area")
                analytics_view = st.radio(
                    "Analytics Area",
                    ["Performance", "Intelligence", "Demographics", "Guest Experience"],
                    key="sa_analytics_view",
                )

    if menu == "Risk Center":
        if is_mobile:
            risk_view = st.radio(
                "Risk Area",
                ["Alerts", "Warnings"],
                horizontal=True,
                key="sa_risk_view",
                on_change=_collapse_sa_mobile_nav,
            )
        else:
            with st.sidebar:
                st.markdown("### Risk Area")
                risk_view = st.radio(
                    "Risk Area",
                    ["Alerts", "Warnings"],
                    key="sa_risk_view",
                )

    branch_scope_options = ["All Branches"] + branches
    selected_branch_scope = nav_selectbox("Branch View", branch_scope_options, key="sa_branch_scope")
    branch_scope = None if selected_branch_scope == "All Branches" else selected_branch_scope

    def apply_branch_scope(df, branch_col="branch"):
        if df is None or getattr(df, "empty", True):
            return df
        if branch_scope and branch_col in df.columns:
            return df[df[branch_col].astype(str) == str(branch_scope)]
        return df

    # =========================================================
    # OVERVIEW
    # =========================================================
    if menu == "Overview":
        st.subheader("Overview")

        users_df      = apply_branch_scope(safe_read("SELECT * FROM users WHERE organization=?",    conn, params=(org,)))
        ratings_df    = apply_branch_scope(safe_read("SELECT * FROM ratings WHERE organization=?",  conn, params=(org,)))
        attendance_df = apply_branch_scope(safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(org,)))
        branches_all = safe_read("SELECT * FROM branches WHERE organization=?", conn, params=(org,))
        if branch_scope and not branches_all.empty and "name" in branches_all.columns:
            branches_all = branches_all[branches_all["name"].astype(str) == str(branch_scope)]
        kiosks_df    = safe_read("SELECT * FROM kiosks WHERE organization=?", conn, params=(org,))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Users",    len(users_df))
        c2.metric("Branches", len(branches_all))
        c3.metric("Ratings",  len(ratings_df))
        c4.metric("Kiosks",   len(kiosks_df))

        try:
            overview_group_data = analyze_group_demographics(
                ratings_df,
                attendance_df=attendance_df,
                users_df=users_df,
                organization=org,
                branch=branch_scope,
            )
            overview_groups = overview_group_data.get("group_details", [])
        except Exception:
            overview_groups = []

        dating_count = len([g for g in overview_groups if str(g.get("group_type", "")).lower() == "dating"])
        conflict_count = len([g for g in overview_groups if str(g.get("group_type", "")).lower() == "conflict_pair"])
        sync_count = len([g for g in overview_groups if str(g.get("group_type", "")).lower() == "synchronized"])

        if dating_count or conflict_count or sync_count:
            st.divider()
            st.markdown("**Relationship and Conflict Alerts**")
            oa1, oa2, oa3 = st.columns(3)
            oa1.metric("Dating / Relationship", dating_count)
            oa2.metric("Conflict Pairs", conflict_count)
            oa3.metric("Synchronized Pairs", sync_count)

        if safe_df(ratings_df):
            st.divider()
            st.markdown("**Top Performers**")
            top = ratings_df.groupby("rated")["score"].mean().sort_values(ascending=False).head(5).reset_index()
            top.columns = ["User", "Avg Score"]
            st.dataframe(top, use_container_width=True)

        if safe_df(branches_all):
            st.divider()
            st.markdown("**All Branches**")
            st.dataframe(branches_all, use_container_width=True)
        st.info("Payment methods, reminders, and upgrade plans are available in the Payments menu.")

    # =========================================================
    # INTELLIGENCE DASHBOARD
    # =========================================================
    elif menu == "Analytics" and analytics_view == "Intelligence":
        st.subheader("Managing Director Intelligence Dashboard")
        
        if not ANALYTICS_OK:
            st.warning("Analytics module unavailable.")
        else:
            intel_options = ["All Branches"] + branches
            intel_default_idx = intel_options.index(branch_scope) if branch_scope in intel_options else 0
            sel_branch = nav_selectbox("Filter by Branch (optional)", intel_options, key="intel_branch", index=intel_default_idx)
            branch_filter = None if sel_branch == "All Branches" else sel_branch
            
            if st.button("Run Intelligence Analysis", type="primary"):
                with st.spinner("Analyzing..."):
                    try:
                        intelligence = get_super_admin_dashboard(org, branch_filter, user)
                        st.session_state["sa_intelligence"] = intelligence
                    except Exception as e:
                        st.error(f"Analysis error: {e}")
            
            intel = st.session_state.get("sa_intelligence")
            
            if intel:
                
                # --- EXECUTIVE SUMMARY ---
                st.divider()
                st.markdown("### Executive Summary")
                
                meta = intel.get("executive_summary", {})
                total_emp = meta.get("total_employees", 0)
                health_score = meta.get("team_health_score", 0)
                health_status = meta.get("team_health_status", "Unknown")
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Employees", total_emp)
                c2.metric("Team Health", f"{health_score:.0f}%")
                c3.metric("Status", health_status)
                c4.metric("Critical Alerts", len(intel.get("critical_alerts", [])))
                
                for point in meta.get("summary_points", []):
                    st.info(clean_display_text(point))

                # --- BUSINESS INTELLIGENCE ---
                st.divider()
                st.markdown("### Business Intelligence")

                biz = intel.get("business_intelligence", {})
                pay = biz.get("payment_trend", {})
                branch_snapshot = biz.get("branch_snapshot", {})

                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Business Health", f"{biz.get('business_health_score', 0):.0f}%")
                b2.metric("Health Status", biz.get("business_health_status", "Unknown"))
                b3.metric("Active Branches", biz.get("active_branches", 0))
                b4.metric("Collections 30d", f"KES {pay.get('last_30_days', 0):,.0f}")

                b5, b6, b7, b8 = st.columns(4)
                b5.metric("Total Collected", f"KES {pay.get('total_collected', 0):,.0f}")
                b6.metric("Payment Trend", pay.get("trend_direction", "Stable"))
                b7.metric("Trend Delta", f"KES {pay.get('trend_delta', 0):,.0f}")
                b8.metric("Avg Internal Score", f"{biz.get('average_internal_score', 0):.1f}")

                # --- BADGES SUMMARY (NO NAMES) ---
                st.markdown("**Badge Summary (organization-wide)**")
                try:
                    badge_summary = compute_badge_summary_for_super_admin(org)
                    bs1, bs2, bs3, bs4 = st.columns(4)
                    bs1.metric("Total Badges", badge_summary.get("total_badges", 0))
                    bs2.metric("Unique Holders", badge_summary.get("unique_holders", 0))
                    bs3.metric("Best Employee Score", f"{badge_summary.get('best_employee_score', 0):.1f}")
                    bs4.metric("Best Admin Score", f"{badge_summary.get('best_admin_score', 0):.1f}")

                    category_map = badge_summary.get("categories", {})
                    if category_map:
                        cat_df = pd.DataFrame(
                            [{"Category": k, "Count": v} for k, v in category_map.items()]
                        )
                        st.dataframe(cat_df, use_container_width=True)

                    badge_holders_df = get_badge_holders_table(org)
                    if not badge_holders_df.empty:
                        # Organization-level winners (general level)
                        org_podium = badge_holders_df[
                            (badge_holders_df["scope"].astype(str).str.lower() == "organization")
                            & (badge_holders_df["badge"].astype(str).isin(["Gold No. 1", "Silver No. 2", "Bronze No. 3"]))
                        ].copy()
                        if not org_podium.empty:
                            st.markdown("**Top Performers - General Level (Org Podium)**")
                            org_cols = [c for c in ["holder_display", "badge", "score", "branch"] if c in org_podium.columns]
                            st.dataframe(org_podium[org_cols], use_container_width=True)

                        best_overall = badge_holders_df[
                            badge_holders_df["badge"].astype(str) == "Best Employee Entire Org"
                        ].copy()
                        if not best_overall.empty:
                            st.markdown("**Best Overall Performer (Best Employee Entire Org Logic)**")
                            bo_cols = [c for c in ["holder_display", "badge", "score", "branch"] if c in best_overall.columns]
                            st.dataframe(best_overall[bo_cols], use_container_width=True)

                        best_admin_overall = badge_holders_df[
                            badge_holders_df["badge"].astype(str) == "Best Admin Overall Org"
                        ].copy()
                        if not best_admin_overall.empty:
                            st.markdown("**Best Overall Admin (Best Admin Overall Org Logic)**")
                            bao_cols = [c for c in ["holder_display", "badge", "score", "branch"] if c in best_admin_overall.columns]
                            st.dataframe(best_admin_overall[bao_cols], use_container_width=True)

                        # Branch-level winners
                        branch_top = badge_holders_df[
                            badge_holders_df["badge"].astype(str).str.contains("Gold No. 1 \(Branch\)", na=False)
                        ].copy()
                        if not branch_top.empty:
                            st.markdown("**Top Performers - Branch Level (Gold per Branch)**")
                            branch_cols = [c for c in ["branch", "holder_display", "badge", "score"] if c in branch_top.columns]
                            st.dataframe(branch_top[branch_cols].sort_values(["branch", "score"], ascending=[True, False]), use_container_width=True)

                        st.markdown("**All Badge Holders (Names + Badge Type)**")
                        cols = [c for c in ["holder_display", "badge", "scope", "branch", "score"] if c in badge_holders_df.columns]
                        st.dataframe(badge_holders_df[cols], use_container_width=True)
                except Exception:
                    st.info("Badge summary is not available yet.")

                growth = biz.get("growth_opportunities", [])
                risks = biz.get("operational_risks", [])
                priorities = biz.get("priorities", [])

                gx, gy = st.columns(2)
                with gx:
                    st.markdown("**Growth Opportunities**")
                    if growth:
                        for item in growth:
                            st.success(item)
                    else:
                        st.info("No growth opportunities surfaced yet.")

                with gy:
                    st.markdown("**Operational Risks**")
                    if risks:
                        for item in risks:
                            st.warning(item)
                    else:
                        st.success("No major operational risks detected.")

                st.markdown("**Immediate Priorities**")
                if priorities:
                    for item in priorities:
                        st.info(item)

                ranking = branch_snapshot.get("ranking", [])
                if ranking:
                    st.markdown("**Branch Business Ranking**")
                    ranking_df = pd.DataFrame(ranking)
                    show_cols = [
                        c for c in [
                            "branch", "business_score", "avg_score", "team_size",
                            "active_users", "admin_count", "attendance_records",
                            "attendance_coverage", "ratings_count"
                        ] if c in ranking_df.columns
                    ]
                    st.dataframe(ranking_df[show_cols], use_container_width=True)

                # --- MONTHLY TRENDS & FORECAST ---
                st.divider()
                st.markdown("### Monthly Trends & Forecast")

                monthly = intel.get("monthly_trends", {})
                monthly_rows = monthly.get("monthly_rows", [])
                forecast = monthly.get("forecast", {})

                if monthly_rows:
                    monthly_df = pd.DataFrame(monthly_rows)

                    if PLOTLY_OK and "month" in monthly_df.columns:
                        try:
                            if "collections" in monthly_df.columns:
                                collections_fig = px.line(
                                    monthly_df,
                                    x="month",
                                    y="collections",
                                    markers=True,
                                    title="Monthly Collections",
                                )
                                st.plotly_chart(collections_fig, use_container_width=True)
                            if "avg_internal_score" in monthly_df.columns:
                                score_fig = px.line(
                                    monthly_df,
                                    x="month",
                                    y="avg_internal_score",
                                    markers=True,
                                    title="Monthly Internal Score",
                                )
                                st.plotly_chart(score_fig, use_container_width=True)
                        except Exception:
                            st.dataframe(monthly_df, use_container_width=True)
                    else:
                        st.dataframe(monthly_df, use_container_width=True)

                    for item in monthly.get("trend_summary", []):
                        st.info(item)
                else:
                    st.info("Not enough monthly data to build trend charts yet.")

                if forecast:
                    f1, f2, f3 = st.columns(3)
                    f1.metric(
                        f"Forecast Collections ({forecast.get('next_month', 'Next')})",
                        f"KES {forecast.get('collections', 0):,.0f}",
                    )
                    f2.metric("Forecast Internal Score", f"{forecast.get('avg_internal_score', 0):.1f}")
                    f3.metric("Forecast Attendance", f"{forecast.get('attendance_records', 0):,.0f}")

                # --- WEAKEST BRANCH ACTION PLAN ---
                st.divider()
                st.markdown("### Branch Action Plan")

                action_plan = intel.get("branch_action_plan", {})
                target_branch = action_plan.get("target_branch")
                if target_branch:
                    st.warning(f"Priority Branch: {target_branch} | Urgency: {action_plan.get('urgency', 'Low')}")
                    st.markdown("**Recommended Actions**")
                    for item in action_plan.get("actions", []):
                        st.warning(item)

                    st.markdown("**Expected Outcomes**")
                    for item in action_plan.get("expected_outcomes", []):
                        st.success(item)
                else:
                    st.success("No weakest-branch action plan needed yet.")

                # --- DOWNLOADABLE EXECUTIVE REPORTS ---
                st.divider()
                st.markdown("### â¬‡ Executive Downloads")

                executive_report_text = build_executive_bi_report(intel)
                report_json = json.dumps(intel, indent=2, default=str)

                d1, d2, d3 = st.columns(3)
                with d1:
                    st.download_button(
                        "Download Executive Report (.txt)",
                        data=executive_report_text,
                        file_name=f"{org}_executive_bi_report.txt",
                        mime="text/plain",
                    )
                with d2:
                    st.download_button(
                        "Download Intelligence Data (.json)",
                        data=report_json,
                        file_name=f"{org}_intelligence_data.json",
                        mime="application/json",
                    )
                with d3:
                    ranking_export_df = pd.DataFrame(ranking) if ranking else pd.DataFrame()
                    st.download_button(
                        "Download Branch Ranking (.csv)",
                        data=ranking_export_df.to_csv(index=False),
                        file_name=f"{org}_branch_ranking.csv",
                        mime="text/csv",
                    )
                
                # --- CRITICAL ALERTS ---
                st.divider()
                st.markdown("### Critical Alerts")
                
                critical = intel.get("critical_alerts", [])
                if critical:
                    for alert in critical:
                        st.error(clean_display_text(alert))
                else:
                    st.success("No critical alerts.")
                
                # --- BIASNESS & FAVORITISM ---
                st.divider()
                st.markdown("### Biasness & Favoritism Analysis")

                favoritism_flags = intel.get("favoritism_analysis", [])
                if favoritism_flags:
                    admin_bias  = [f for f in favoritism_flags if "ADMIN BIAS" in f or "UNREALISTIC RATER" in f]
                    suspicions  = [f for f in favoritism_flags if "SUSPICIOUS GROUP" in f]
                    general_fav = [f for f in favoritism_flags if f not in admin_bias and f not in suspicions]
                    favoritism_people = extract_user_mentions(favoritism_flags, org_usernames)

                    fav_c1, fav_c2, fav_c3 = st.columns(3)
                    fav_c1.metric("Findings", len(favoritism_flags))
                    fav_c2.metric("Individuals", len(favoritism_people))
                    fav_c3.metric("High Severity", len(admin_bias))

                    if favoritism_people:
                        people_df = pd.DataFrame({"Individuals Mentioned": favoritism_people})
                        st.markdown("**Individuals Mentioned**")
                        st.dataframe(people_df, use_container_width=True, hide_index=True)

                    fav_table = []
                    for flag in favoritism_flags:
                        if "ADMIN BIAS" in flag or "UNREALISTIC RATER" in flag:
                            sev = "High"
                        elif "SUSPICIOUS GROUP" in flag:
                            sev = "Medium"
                        else:
                            sev = "Low"
                        fav_table.append({"Severity": sev, "Finding": clean_display_text(flag)})
                    st.dataframe(pd.DataFrame(fav_table), use_container_width=True)

                    if admin_bias:
                        st.markdown("**Admin / Manager Bias**")
                        for f in admin_bias:
                            st.error(clean_display_text(f))
                    if suspicions:
                        st.markdown("**Suspicious Mutual Rating Groups**")
                        for f in suspicions:
                            st.warning(clean_display_text(f))
                    if general_fav:
                        st.markdown("**General Favoritism**")
                        for f in general_fav:
                            st.warning(clean_display_text(f))
                else:
                    st.success("No biasness or favoritism patterns detected.")

                # --- POWER ABUSE / RETALIATION ---
                st.divider()
                st.markdown("### Power Abuse / Retaliation Risk")

                power_abuse_flags = intel.get("power_abuse_analysis", [])
                if power_abuse_flags:
                    abuse_critical = [f for f in power_abuse_flags if "RETALIATION RISK" in f or "DISCIPLINE TARGETING RISK" in f or "POWER ABUSE RISK" in f]
                    abuse_boundary = [f for f in power_abuse_flags if "BOUNDARY RISK" in f or "FAVOR PROTECTION RISK" in f]
                    abuse_other = [f for f in power_abuse_flags if f not in abuse_critical and f not in abuse_boundary]
                    abuse_people = extract_user_mentions(power_abuse_flags, org_usernames)

                    ab1, ab2, ab3 = st.columns(3)
                    ab1.metric("Findings", len(power_abuse_flags))
                    ab2.metric("Individuals", len(abuse_people))
                    ab3.metric("High Severity", len(abuse_critical))

                    if abuse_people:
                        abuse_people_df = pd.DataFrame({"Individuals Mentioned": abuse_people})
                        st.markdown("**Individuals Mentioned**")
                        st.dataframe(abuse_people_df, use_container_width=True, hide_index=True)

                    abuse_rows = []
                    for flag in power_abuse_flags:
                        if "RETALIATION RISK" in flag or "DISCIPLINE TARGETING RISK" in flag or "POWER ABUSE RISK" in flag:
                            sev = "High"
                        elif "BOUNDARY RISK" in flag or "FAVOR PROTECTION RISK" in flag:
                            sev = "Medium"
                        else:
                            sev = "Review"
                        abuse_rows.append({"Severity": sev, "Finding": flag})
                    st.dataframe(pd.DataFrame(abuse_rows), use_container_width=True)

                    if abuse_critical:
                        st.markdown("**Critical Super Admin Review**")
                        for f in abuse_critical:
                            st.error(f)
                    if abuse_boundary:
                        st.markdown("**Boundary / Favor Protection Risk**")
                        for f in abuse_boundary:
                            st.warning(f)
                    if abuse_other:
                        st.markdown("**Other Power-Imbalance Signals**")
                        for f in abuse_other:
                            st.warning(f)
                else:
                    st.success("No power abuse or retaliation patterns detected from current evidence.")

                # --- PEER GANG-UP / TARGETING ---
                st.divider()
                st.markdown("### Peer Gang-Up / Targeting Risk")

                peer_gangup_flags = intel.get("peer_gangup_analysis", [])
                if peer_gangup_flags:
                    peer_critical = [f for f in peer_gangup_flags if "PEER GANG-UP RISK" in f or "PEER TARGETING CLUSTER" in f]
                    peer_repeat = [f for f in peer_gangup_flags if "REPEATED PEER TARGETING" in f]
                    peer_clique = [f for f in peer_gangup_flags if "PEER CLIQUE / MUTUAL FAVORITISM" in f]
                    peer_people = extract_user_mentions(peer_gangup_flags, org_usernames)

                    pg1, pg2, pg3 = st.columns(3)
                    pg1.metric("Findings", len(peer_gangup_flags))
                    pg2.metric("Individuals", len(peer_people))
                    pg3.metric("Critical Cases", len(peer_critical))

                    if peer_people:
                        peer_people_df = pd.DataFrame({"Individuals Mentioned": peer_people})
                        st.markdown("**Individuals Mentioned**")
                        st.dataframe(peer_people_df, use_container_width=True, hide_index=True)

                    peer_rows = []
                    for flag in peer_gangup_flags:
                        if "PEER GANG-UP RISK" in flag or "PEER TARGETING CLUSTER" in flag:
                            sev = "Critical"
                        elif "REPEATED PEER TARGETING" in flag:
                            sev = "High"
                        else:
                            sev = "Review"
                        peer_rows.append({"Severity": sev, "Finding": flag})
                    st.dataframe(pd.DataFrame(peer_rows), use_container_width=True)

                    if peer_critical:
                        st.markdown("**Critical Peer Pressure / Gang-Up Cases**")
                        for f in peer_critical:
                            st.error(f)
                    if peer_repeat:
                        st.markdown("**Repeated Targeting by Individuals**")
                        for f in peer_repeat:
                            st.warning(f)
                    if peer_clique:
                        st.markdown("**Clique / Mutual Favoritism Signals**")
                        for f in peer_clique:
                            st.warning(f)
                else:
                    st.success("No employee-to-employee gang-up or peer targeting patterns detected.")

                # --- ISOLATION ANALYSIS ---
                st.divider()
                st.markdown("### Isolation Analysis")

                isolation_flags = intel.get("isolation_analysis", [])
                if isolation_flags:
                    iso_critical = [f for f in isolation_flags if "ISOLATION DETECTED" in f or "CRITICAL PERFORMANCE" in f or "CRITICAL:" in f]
                    iso_conflict = [f for f in isolation_flags if "CONFLICT (NOT PERFORMANCE)" in f]
                    iso_other    = [f for f in isolation_flags if f not in iso_critical and f not in iso_conflict]
                    isolation_people = extract_user_mentions(isolation_flags, org_usernames)

                    iso_c1, iso_c2, iso_c3 = st.columns(3)
                    iso_c1.metric("Findings", len(isolation_flags))
                    iso_c2.metric("Individuals", len(isolation_people))
                    iso_c3.metric("Critical Cases", len(iso_critical))

                    if isolation_people:
                        isolation_people_df = pd.DataFrame({"Individuals Mentioned": isolation_people})
                        st.markdown("**Individuals Mentioned**")
                        st.dataframe(isolation_people_df, use_container_width=True, hide_index=True)

                    iso_table = []
                    for flag in isolation_flags:
                        if "ISOLATION DETECTED" in flag or "CRITICAL" in flag:
                            sev = "Critical"
                        elif "CONFLICT" in flag:
                            sev = "Conflict"
                        else:
                            sev = "At-Risk"
                        iso_table.append({"Severity": sev, "Finding": clean_display_text(flag)})
                    st.dataframe(pd.DataFrame(iso_table), use_container_width=True)

                    if iso_critical:
                        st.markdown("**Isolation / Critical Performance**")
                        for f in iso_critical:
                            st.error(clean_display_text(f))
                    if iso_conflict:
                        st.markdown("**Personal Conflict (Not a Performance Issue)**")
                        for f in iso_conflict:
                            st.warning(clean_display_text(f))
                    if iso_other:
                        st.markdown("**At-Risk Employees**")
                        for f in iso_other:
                            st.warning(clean_display_text(f))
                else:
                    st.success("No isolation or critical performance patterns detected.")

                # --- CONFLICT PAIRS ---
                st.divider()
                st.markdown("### Conflict Pairs")

                group_analysis = intel.get("group_analysis", {})
                all_groups = group_analysis.get("group_details", [])
                conflict_pairs = [g for g in all_groups if str(g.get("group_type", "")).lower() == "conflict_pair"]

                if conflict_pairs:
                    conflict_rows = []
                    for pair in conflict_pairs:
                        conflict_rows.append({
                            "Pair": pair.get("pair_label", ""),
                            "Member 1": pair.get("member_1", ""),
                            "Gender 1": pair.get("gender_1", "unknown"),
                            "Member 2": pair.get("member_2", ""),
                            "Gender 2": pair.get("gender_2", "unknown"),
                            "Low Ratings": int(pair.get("low_ratings", 0) or 0),
                            "Evidence": pair.get("description", "Conflict pair detected"),
                        })

                    st.dataframe(pd.DataFrame(conflict_rows), use_container_width=True)
                else:
                    st.info("No conflict pairs detected in this scope.")

                # --- WHO VS WHO (ALL INTELLIGENCE FINDINGS) ---
                st.divider()
                st.markdown("### Who Vs Who (All Findings)")

                who_rows = []
                who_rows.extend(build_who_against_who_rows(favoritism_flags, org_usernames, "Bias/Favoritism"))
                who_rows.extend(build_who_against_who_rows(power_abuse_flags, org_usernames, "Power Abuse"))
                who_rows.extend(build_who_against_who_rows(peer_gangup_flags, org_usernames, "Peer Gang-Up"))
                who_rows.extend(build_who_against_who_rows(isolation_flags, org_usernames, "Isolation"))
                who_rows.extend(build_who_against_who_rows(critical, org_usernames, "Critical Alerts"))
                who_rows.extend(build_who_against_who_rows(intel.get("recommendations", []), org_usernames, "Recommendations"))

                # Add explicit pair rows from group analysis so every detected pair is visible.
                for g in all_groups:
                    p1 = str(g.get("member_1", "") or "").strip()
                    p2 = str(g.get("member_2", "") or "").strip()
                    if p1 and p2:
                        gtype = str(g.get("group_type", "") or "unknown")
                        who_rows.append({
                            "Source": "Group Analysis",
                            "Person A": p1,
                            "Person B": p2,
                            "Relationship": gtype,
                            "Evidence": str(g.get("description", "Pair detected")),
                        })

                if who_rows:
                    who_df = pd.DataFrame(who_rows)
                    who_df = who_df.drop_duplicates(subset=["Source", "Person A", "Person B", "Relationship", "Evidence"]).reset_index(drop=True)
                    st.dataframe(who_df[["Source", "Person A", "Person B", "Relationship", "Evidence"]], use_container_width=True)
                else:
                    st.info("No person-vs-person links extracted from current findings.")

                # --- RECOMMENDATIONS ---
                st.divider()
                st.markdown("### AI Recommendations")

                rec_ctx = intel.get("recommendation_context", {})
                if rec_ctx:
                    st.caption(
                        f"Recommendation stage: {str(rec_ctx.get('stage', 'early')).upper()} "
                        f"| ratings: {int(rec_ctx.get('ratings_count', 0))} "
                        f"| months: {int(rec_ctx.get('history_months', 0))} "
                        f"| drift: {int(rec_ctx.get('drift_score', 0))}"
                    )
                
                recs = intel.get("recommendations", [])
                if recs:
                    for rec in recs:
                        clean_rec = clean_display_text(rec)
                        if "ACTION:" in rec:
                            st.warning(clean_rec)
                        elif "SUPPORT:" in rec or "RETENTION:" in rec:
                            st.info(clean_rec)
                        else:
                            st.success(clean_rec)
                else:
                    st.info("No recommendations at this time.")

                # --- LIVE INTERNET AI RECOMMENDATIONS ---
                st.divider()
                st.markdown("### Live Industry AI Recommendations")

                st.caption(
                    f"Tailored for **{org_business_type}** organisations | Sources: SHRM, HR Dive, HBR, MIT Sloan "
                    f"+ {org_business_type}-specific feeds | Refreshed every 6 hours."
                )

                with st.spinner("Fetching latest industry insights from the web..."):
                    live_data = get_cached_recommendations(business_type=org_business_type)

                if live_data.get("error"):
                    st.warning(f"Could not fetch live recommendations: {live_data['error']}")
                else:
                    st.caption(
                        f"Last fetched: {live_data.get('fetched_at', 'unknown')} "
                        f"| Sources online: {live_data.get('sources_ok', 0)}"
                    )
                    articles = live_data.get("articles", [])
                    if articles:
                        # Group by category for a cleaner layout
                        categories = {}
                        for art in articles:
                            cat = art.get("category", "General")
                            categories.setdefault(cat, []).append(art)

                        for cat, items in categories.items():
                            with st.expander(f"{cat} ({len(items)} articles)", expanded=False):
                                for art in items:
                                    title = clean_display_text(art.get("title", "Untitled"))
                                    link = art.get("link", "")
                                    source = clean_display_text(art.get("source", ""))
                                    summary = clean_display_text(art.get("summary", ""))
                                    pub = clean_display_text(art.get("published", ""))

                                    if link:
                                        st.markdown(f"**[{title}]({link})**  _- {source}_")
                                    else:
                                        st.markdown(f"**{title}**  _- {source}_")

                                    if summary:
                                        st.caption(summary + ("..." if len(summary) >= 200 else ""))
                                    if pub:
                                        st.caption(f"Published: {pub}")
                                    st.markdown("---")
                    else:
                        st.info("No live articles retrieved at this time.")

                # --- INDIVIDUALS OF FOCUS ---
                st.divider()
                st.markdown("### Individuals of Focus")
                
                focus = intel.get("individual_focus", {})
                
                ia, ib, ic = st.columns(3)
                
                with ia:
                    st.markdown("**Problematic**")
                    problematic = focus.get("problematic", [])
                    if problematic:
                        for emp in problematic:
                            st.error(f"- {clean_display_text(emp)}")
                    else:
                        st.info("No problematic users detected.")
                
                with ib:
                    st.markdown("**At-Risk (Retention)**")
                    at_risk_retention = focus.get("at_risk_retention", [])
                    at_risk_reasons = focus.get("at_risk_reasons", {})
                    if at_risk_retention:
                        for emp in at_risk_retention:
                            st.warning(f"- {clean_display_text(emp)}")
                            reasons = at_risk_reasons.get(emp, []) if isinstance(at_risk_reasons, dict) else []
                            for reason in reasons[:2]:
                                st.caption(f"Reason: {clean_display_text(reason)}")
                    else:
                        st.info("No at-risk users detected.")
                
                with ic:
                    st.markdown("**High Performers**")
                    high_performers = focus.get("high_performers", [])
                    if high_performers:
                        for emp in high_performers:
                            st.success(f"- {clean_display_text(emp)}")
                    else:
                        st.info("No high performers detected yet.")
                
                # --- POSITIVE HIGHLIGHTS ---
                st.divider()
                st.markdown("### Positive Highlights")
                
                positives = intel.get("positive_highlights", [])
                if positives:
                    for pos in positives:
                        st.success(clean_display_text(pos))
                
                # --- GROUP ANALYSIS ---
                st.divider()
                st.markdown("### Groups Detected")
                
                group_analysis = intel.get("group_analysis", {})
                groups = group_analysis.get("group_details", [])
                
                if groups:
                    dating_groups = [g for g in groups if str(g.get("group_type", "")).lower() == "dating"]
                    conflict_groups = [g for g in groups if str(g.get("group_type", "")).lower() == "conflict_pair"]
                    pair_groups = [g for g in groups if str(g.get("group_type", "")).lower() == "synchronized"]

                    gc1, gc2, gc3 = st.columns(3)
                    gc1.metric("Dating / Relationship", len(dating_groups))
                    gc2.metric("Conflict Pairs", len(conflict_groups))
                    gc3.metric("Synchronized Pairs", len(pair_groups))

                    group_rows = []
                    for idx, g in enumerate(groups, start=1):
                        members = g.get("members", [])
                        if isinstance(members, str):
                            member_list = [m.strip() for m in members.split(",") if m.strip()]
                        elif isinstance(members, list):
                            member_list = [str(m).strip() for m in members if str(m).strip()]
                        else:
                            member_list = []

                        logic_tags = g.get("logic_tags", [])
                        if isinstance(logic_tags, list):
                            logic_text = ", ".join(str(tag) for tag in logic_tags if str(tag).strip())
                        else:
                            logic_text = str(logic_tags or "")

                        group_rows.append({
                            "Pair": g.get("pair_label", " & ".join(member_list) if member_list else f"Group {idx}"),
                            "Group Type": g.get("group_type", "unknown"),
                            "Member 1": g.get("member_1", member_list[0] if len(member_list) > 0 else ""),
                            "Gender 1": g.get("gender_1", "unknown"),
                            "Member 2": g.get("member_2", member_list[1] if len(member_list) > 1 else ""),
                            "Gender 2": g.get("gender_2", "unknown"),
                            "Logic": logic_text or "general",
                            "Risk": g.get("risk_level", "info"),
                            "Avg Rating": round(float(g.get("avg_rating", 0) or 0), 2),
                            "Evidence": g.get("description", "Group detected"),
                        })

                    groups_df = pd.DataFrame(group_rows)
                    st.dataframe(groups_df, use_container_width=True)

                    st.markdown("**Who With Who and Why**")
                    for row, raw in zip(group_rows, groups):
                        with st.expander(f"{row['Pair']} - {row['Group Type']} - {row['Risk']}"):
                            st.write(f"Member 1: {row['Member 1']} ({row['Gender 1']})")
                            st.write(f"Member 2: {row['Member 2']} ({row['Gender 2']})")
                            st.write(f"Logic: {row['Logic']}")
                            st.write(f"Evidence: {row['Evidence']}")
                            st.write(f"Average Rating: {row['Avg Rating']}")
                            if raw.get("clock_in_count") or raw.get("clock_out_count"):
                                st.write(
                                    f"Clock sync: {int(raw.get('clock_in_count', 0))} in / {int(raw.get('clock_out_count', 0))} out"
                                )
                            if raw.get("leave_sync_count"):
                                st.write(f"Leave sync count: {int(raw.get('leave_sync_count', 0))}")
                            if raw.get("low_ratings"):
                                st.write(f"Low-rating evidence: {int(raw.get('low_ratings', 0))} repeated low ratings")
                            if raw.get("avg_mutual_rating"):
                                st.write(f"Mutual rating average: {float(raw.get('avg_mutual_rating', 0)):.1f}%")
                else:
                    st.info("No significant groups detected.")
            
            else:
                st.info("Click 'Run Intelligence Analysis' to start.")
    
    # =========================================================
    # ALERTS
    # =========================================================
    elif menu == "Risk Center" and risk_view == "Alerts":
        st.subheader("Alerts & Notifications")
        
        if not ANALYTICS_OK:
            st.warning("Analytics module unavailable.")
        else:
            # Alert statistics
            try:
                alert_stats = get_alert_statistics(org)
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Critical", alert_stats.get("critical", 0))
                c2.metric("Warnings", alert_stats.get("warning", 0))
                c3.metric("Info", alert_stats.get("info", 0))
                c4.metric("Unread Messages", alert_stats.get("unread_messages", 0))
                
                st.divider()
            except Exception:
                pass
            
            # Filter by severity
            severity_sel = nav_selectbox("Filter by Severity", ["All", "critical", "warning", "info"], key="alert_sev")
            
            try:
                alerts = get_unread_alerts(org)

                if branch_scope:
                    alerts = [a for a in alerts if str(a.get("branch", "")).strip() == str(branch_scope)]
                
                if severity_sel != "All":
                    alerts = [a for a in alerts if a.get("severity") == severity_sel]
                
                if alerts:
                    for alert in alerts:
                        severity = alert.get("severity", "info")
                        created = alert.get("created_at", "")[:16]
                        msg = alert.get("message", "")
                        alert_id = alert.get("id")
                        
                        subject = clean_display_text(alert.get('subject', msg[:60]))
                        with st.expander(f"[{severity.upper()}] {subject} - {created}"):
                            st.write(clean_display_text(msg))
                            if st.button("Mark Resolved", key=f"resolve_{alert_id}"):
                                resolve_alert(alert_id)
                                st.success("Alert resolved.")
                                st.rerun()
                else:
                    st.success("No open alerts.")
            
            except Exception as e:
                st.error(f"Could not load alerts: {e}")

    # =========================================================
    # WARNINGS (RISK CENTER)
    # =========================================================
    elif menu == "Risk Center" and risk_view == "Warnings":
        st.subheader("Warnings Center")

        warns_df = safe_read(
            """
            SELECT id, username, branch, type, message, created_at
            FROM warnings
            WHERE organization=?
            ORDER BY id DESC
            """,
            conn,
            params=(org,),
        )
        warns_df = apply_branch_scope(warns_df)

        if warns_df.empty:
            st.success("No warning records found.")
        else:
            category_map = {
                "late_pattern": "Attendance",
                "early_clockout_request": "Operations",
                "Absenteeism Risk": "Attendance",
                "Low Performer": "Performance",
                "Frequent Latecomer": "Attendance",
                "Frequent Approved Lateness": "Attendance",
                "Low Attendance Input": "Attendance",
                "Non-improving Manager": "Management",
            }

            warns_view = warns_df.copy()
            warns_view["warning_type"] = warns_view["type"].astype(str)
            warns_view["category"] = warns_view["warning_type"].map(category_map).fillna("General")

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Warnings", len(warns_view))
            c2.metric("Unique Users", warns_view["username"].astype(str).nunique())
            c3.metric("Categories", warns_view["category"].astype(str).nunique())

            cat_options = ["All"] + sorted(warns_view["category"].dropna().astype(str).unique().tolist())
            selected_category = nav_selectbox("Filter by Category", cat_options, key="risk_warn_cat")
            type_options = ["All"] + sorted(warns_view["warning_type"].dropna().astype(str).unique().tolist())
            selected_type = nav_selectbox("Filter by Warning Type", type_options, key="risk_warn_type")

            filtered = warns_view.copy()
            if selected_category != "All":
                filtered = filtered[filtered["category"] == selected_category]
            if selected_type != "All":
                filtered = filtered[filtered["warning_type"] == selected_type]

            st.markdown("**Warnings by Category**")
            cat_df = (
                filtered.groupby("category", dropna=False)
                .size()
                .reset_index(name="count")
                .sort_values("count", ascending=False)
            )
            st.dataframe(cat_df, use_container_width=True)

            st.markdown("**Warning Details**")
            show_cols = ["created_at", "username", "branch", "category", "warning_type", "message"]
            st.dataframe(filtered[show_cols], use_container_width=True)
    
    # =========================================================
    # USERS
    # =========================================================
    elif menu == "Management" and management_view == "Users":
        st.subheader("Users")
        show_flash_message("super_admin_user_flash")

        users_df = apply_branch_scope(safe_read(
            "SELECT id, username, role, phone, branch, organization, status FROM users WHERE organization=?",
            conn, params=(org,)
        ))

        role_view_df = users_df.copy()
        if safe_df(role_view_df):
            role_norm = role_view_df["role"].astype(str).str.lower()
            role_view_df["user_category"] = "Specialist"
            role_view_df.loc[role_norm.isin(["admin"]), "user_category"] = "Manager"
            role_view_df.loc[role_norm.isin(["superadmin", "super_admin", "master", "owner"]), "user_category"] = "Managing Director"

            r1, r2, r3 = st.columns(3)
            r1.metric("Managing Directors", int((role_view_df["user_category"] == "Managing Director").sum()))
            r2.metric("Managers", int((role_view_df["user_category"] == "Manager").sum()))
            r3.metric("Specialists", int((role_view_df["user_category"] == "Specialist").sum()))

            with st.expander("User Categories", expanded=False):
                for category in ["Managing Director", "Manager", "Specialist"]:
                    cat_df = role_view_df[role_view_df["user_category"] == category]
                    st.markdown(f"**{category}**")
                    if cat_df.empty:
                        st.caption("No users in this category.")
                    else:
                        st.dataframe(
                            cat_df[["username", "role", "branch", "status", "phone"]],
                            use_container_width=True,
                        )

        tab_all, tab_branch, tab_create, tab_manage, tab_requests = st.tabs([
            "All Users", "By Branch", "Create User", "Manage User", "Admin Requests"
        ])

        with tab_all:
            if safe_df(users_df):
                st.dataframe(users_df, use_container_width=True)
                if st.button("Show / Hide Passwords", key="show_pw_all"):
                    st.session_state["show_pw"] = not st.session_state.get("show_pw", False)
                if st.session_state.get("show_pw"):
                    pw_df = safe_read(
                        "SELECT username, password FROM users WHERE organization=?",
                        conn, params=(org,)
                    )
                    st.dataframe(pw_df, use_container_width=True)
            else:
                st.info("No users yet.")

        with tab_branch:
            if branches:
                user_branch_options = ["All Branches"] + branches
                user_branch_default_idx = user_branch_options.index(branch_scope) if branch_scope in user_branch_options else 0
                sel_branch = nav_selectbox("Select Branch", user_branch_options, key="user_branch_view", index=user_branch_default_idx)
                if sel_branch == "All Branches":
                    branch_users = users_df.copy()
                else:
                    branch_users = safe_read(
                        "SELECT id, username, role, phone, branch, status FROM users WHERE organization=? AND branch=?",
                        conn, params=(org, sel_branch)
                    )
                if safe_df(branch_users):
                    st.dataframe(branch_users, use_container_width=True)
                    if st.button("Show / Hide Passwords", key="show_pw_branch"):
                        st.session_state["show_pw_br"] = not st.session_state.get("show_pw_br", False)
                    if st.session_state.get("show_pw_br"):
                        pw_df_br = safe_read(
                            "SELECT username, password FROM users WHERE organization=? AND branch=?",
                            conn, params=(org, sel_branch)
                        )
                        st.dataframe(pw_df_br, use_container_width=True)
                else:
                    st.info("No users in this branch.")
            else:
                st.info("No branches yet.")

        with tab_create:
            with st.form("add_user", clear_on_submit=False):
                u    = st.text_input("Username")
                p    = st.text_input("Password", type="password")
                pin  = st.text_input("PIN (4 digits, default 1234)")
                phone = st.text_input("Phone Number (required)")
                role = st.selectbox("Role", ["employee", "admin"])
                br   = st.selectbox("Branch", branches if branches else ["Create a branch first"])
                gender = st.selectbox("Gender", ["unknown", "male", "female"])
                sub  = st.form_submit_button("Create User")

                if sub:
                    if not branches:
                        st.error("Create a branch first.")
                    elif not u.strip():
                        st.error("Username is required.")
                    elif not phone.strip():
                        st.error("Phone number is required.")
                    elif not valid_pass(p):
                        st.error("Password must be at least 4 characters.")
                    else:
                        exists = safe_read("SELECT id FROM users WHERE username=?", conn, params=(u,))
                        if not exists.empty:
                            st.error(f"Username '{u}' already exists.")
                        else:
                            conn.execute(
                                "INSERT INTO users(username,password,role,branch,organization,status,pin,phone,gender) VALUES(?,?,?,?,?,?,?,?,?)",
                                (u.strip(), hash_password(p), role, br, org, "active", pin.strip() or "1234", phone.strip(), gender)
                            )
                            conn.commit()
                            log_action(conn, user, "CREATE USER", u, org)
                            set_flash_message(
                                "super_admin_user_flash",
                                "success",
                                f"User '{u.strip()}' created in branch '{br}'.",
                            )
                            st.rerun()

        with tab_manage:
            if safe_df(users_df):
                sel_user = st.selectbox("Select User", users_df["username"].tolist(), key="manage_user_sel")
                row = users_df[users_df["username"] == sel_user].iloc[0]
                st.write(
                    f"**Role:** {row['role']} | **Phone:** {row.get('phone', '')} | "
                    f"**Branch:** {row['branch']} | **Status:** {row['status']}"
                )

                ca, cb, cc, cd = st.columns(4)
                if ca.button("Suspend", key="sus_user"):
                    conn.execute("UPDATE users SET status='suspended' WHERE username=? AND organization=?", (sel_user, org))
                    conn.commit()
                    log_action(conn, user, "SUSPEND USER", sel_user, org)
                    set_flash_message("super_admin_user_flash", "warning", f"{sel_user} suspended.")
                    st.rerun()
                if cb.button("Probation", key="prob_user"):
                    conn.execute("UPDATE users SET status='probation' WHERE username=? AND organization=?", (sel_user, org))
                    conn.commit()
                    log_action(conn, user, "PUT USER ON PROBATION", sel_user, org)
                    set_flash_message("super_admin_user_flash", "info", f"{sel_user} moved to probation.")
                    st.rerun()
                if cc.button("Activate", key="act_user"):
                    conn.execute("UPDATE users SET status='active' WHERE username=? AND organization=?", (sel_user, org))
                    conn.commit()
                    log_action(conn, user, "ACTIVATE USER", sel_user, org)
                    set_flash_message("super_admin_user_flash", "success", f"{sel_user} activated.")
                    st.rerun()
                if cd.button("Delete User", key="del_user"):
                    conn.execute("DELETE FROM users WHERE username=? AND organization=?", (sel_user, org))
                    conn.commit()
                    log_action(conn, user, "DELETE USER", sel_user, org)
                    set_flash_message("super_admin_user_flash", "success", f"{sel_user} deleted.")
                    st.rerun()

                st.divider()
                with st.form("reset_pw_form", clear_on_submit=False):
                    new_pass  = st.text_input("New Password", type="password")
                    conf_pass = st.text_input("Confirm Password", type="password")
                    reset_sub = st.form_submit_button("Reset Password")
                    if reset_sub:
                        if not valid_pass(new_pass):
                            st.error("Password must be at least 4 characters.")
                        elif new_pass != conf_pass:
                            st.error("Passwords do not match.")
                        else:
                            conn.execute(
                                "UPDATE users SET password=? WHERE username=? AND organization=?",
                                (hash_password(new_pass), sel_user, org)
                            )
                            conn.commit()
                            log_action(conn, user, "RESET PASSWORD", sel_user, org)
                            set_flash_message("super_admin_user_flash", "success", f"Password for '{sel_user}' reset.")
                            st.rerun()

        with tab_requests:
            req_df = apply_branch_scope(
                safe_read(
                    """
                    SELECT id, branch, target_username, target_role, requested_by, action_type,
                           reason, status, reviewed_by, review_note, created_at, reviewed_at
                    FROM admin_action_requests
                    WHERE organization=?
                    ORDER BY CASE WHEN lower(status)='pending' THEN 0 ELSE 1 END, id DESC
                    """,
                    conn,
                    params=(org,),
                )
            )

            if req_df.empty:
                st.info("No admin action requests yet.")
            else:
                pending_df = req_df[req_df["status"].astype(str).str.lower() == "pending"].copy()
                reviewed_df = req_df[req_df["status"].astype(str).str.lower() != "pending"].copy()

                rc1, rc2 = st.columns(2)
                rc1.metric("Pending Requests", len(pending_df))
                rc2.metric("Reviewed Requests", len(reviewed_df))

                if not pending_df.empty:
                    st.markdown("### Pending Approval Queue")
                    status_map = {
                        "suspend": "suspended",
                        "activate": "active",
                        "probation": "probation",
                    }
                    for _, req in pending_df.iterrows():
                        req_id = int(req["id"])
                        req_branch = str(req.get("branch", "") or "")
                        target_user = str(req.get("target_username", "") or "")
                        requester = str(req.get("requested_by", "") or "")
                        action_type = str(req.get("action_type", "") or "").lower()
                        created_at = str(req.get("created_at", "") or "")[:16]
                        req_reason = str(req.get("reason", "") or "")

                        with st.expander(f"{target_user} | {action_type.title()} | by {requester} | {created_at}"):
                            st.write(f"Branch: {req_branch or 'N/A'}")
                            st.write(f"Reason: {req_reason or 'No reason provided.'}")
                            review_note = st.text_area("Review note", key=f"sa_req_note_{req_id}")
                            qa, qb = st.columns(2)
                            if qa.button("Approve Request", key=f"sa_req_approve_{req_id}"):
                                final_status = status_map.get(action_type, "active")
                                conn.execute(
                                    "UPDATE users SET status=? WHERE username=? AND organization=?",
                                    (final_status, target_user, org),
                                )
                                conn.execute(
                                    """
                                    UPDATE admin_action_requests
                                    SET status='approved', reviewed_by=?, review_note=?, reviewed_at=datetime('now')
                                    WHERE id=? AND organization=?
                                    """,
                                    (user, review_note.strip(), req_id, org),
                                )
                                try:
                                    conn.execute(
                                        """
                                        INSERT INTO messages(sender,receiver,branch,organization,message,created_at)
                                        VALUES(?,?,?,?,?,datetime('now'))
                                        """,
                                        (
                                            user,
                                            target_user,
                                            req_branch,
                                            org,
                                            f"Management update: your status is now '{final_status}'. {review_note.strip() or req_reason}".strip(),
                                        ),
                                    )
                                except Exception:
                                    pass
                                conn.commit()
                                log_action(conn, user, f"APPROVE {action_type.upper()} REQUEST", target_user, org)
                                set_flash_message("super_admin_user_flash", "success", f"Request approved for {target_user}.")
                                st.rerun()
                            if qb.button("Reject Request", key=f"sa_req_reject_{req_id}"):
                                conn.execute(
                                    """
                                    UPDATE admin_action_requests
                                    SET status='rejected', reviewed_by=?, review_note=?, reviewed_at=datetime('now')
                                    WHERE id=? AND organization=?
                                    """,
                                    (user, review_note.strip(), req_id, org),
                                )
                                try:
                                    conn.execute(
                                        """
                                        INSERT INTO messages(sender,receiver,branch,organization,message,created_at)
                                        VALUES(?,?,?,?,?,datetime('now'))
                                        """,
                                        (
                                            user,
                                            target_user,
                                            req_branch,
                                            org,
                                            f"Management reviewed the requested '{action_type}' action and did not approve it. {review_note.strip() or req_reason}".strip(),
                                        ),
                                    )
                                except Exception:
                                    pass
                                conn.commit()
                                log_action(conn, user, f"REJECT {action_type.upper()} REQUEST", target_user, org)
                                set_flash_message("super_admin_user_flash", "warning", f"Request rejected for {target_user}.")
                                st.rerun()
                else:
                    st.success("No pending admin status requests.")

                if not reviewed_df.empty:
                    st.markdown("### Reviewed Request History")
                    show_cols = [
                        c for c in [
                            "created_at", "branch", "target_username", "target_role",
                            "requested_by", "action_type", "reason", "status",
                            "reviewed_by", "review_note", "reviewed_at"
                        ] if c in reviewed_df.columns
                    ]
                    st.dataframe(reviewed_df[show_cols], use_container_width=True)

    # =========================================================
    # BRANCHES
    # =========================================================
    elif menu == "Management" and management_view == "Branches":
        st.subheader("Branches")
        show_flash_message("super_admin_branch_flash")

        branches_df = safe_read("""
            SELECT b.id, b.name, b.organization, b.status,
                   (SELECT username FROM users
                    WHERE organization=b.organization AND branch=b.name AND role='admin' LIMIT 1) AS manager,
                   (SELECT COUNT(*) FROM kiosks
                    WHERE organization=b.organization AND branch=b.name) AS kiosks
            FROM branches b
            WHERE b.organization=?
            ORDER BY b.name
        """, conn, params=(org,))

        if safe_df(branches_df):
            st.dataframe(branches_df, use_container_width=True)
        else:
            st.info("No branches yet.")

        st.divider()

        tab_add, tab_edit, tab_delete = st.tabs(["Add Branch", "Edit Branch", "Delete Branch"])

        with tab_add:
            with st.form("add_branch", clear_on_submit=False):
                new_branch = st.text_input("Branch Name")
                add_sub    = st.form_submit_button("Add Branch")
                if add_sub:
                    if not new_branch.strip():
                        st.error("Branch name is required.")
                    else:
                        exists = safe_read(
                            "SELECT id FROM branches WHERE name=? AND organization=?",
                            conn, params=(new_branch.strip(), org)
                        )
                        if not exists.empty:
                            st.error(f"Branch '{new_branch}' already exists.")
                        else:
                            conn.execute(
                                "INSERT INTO branches(name,organization,status) VALUES(?,?,?)",
                                (new_branch.strip(), org, "active")
                            )
                            conn.commit()
                            log_action(conn, user, "ADD BRANCH", new_branch, org)
                            set_flash_message(
                                "super_admin_branch_flash",
                                "success",
                                f"Branch '{new_branch.strip()}' created.",
                            )
                            st.rerun()

        with tab_edit:
            if branches:
                with st.form("edit_branch", clear_on_submit=False):
                    eb_sel    = st.selectbox("Select Branch", branches, key="edit_br_sel")
                    eb_name   = st.text_input("New Name (blank = keep current)")
                    eb_status = st.selectbox("Status", ["active", "inactive"])
                    eb_sub    = st.form_submit_button("Save Changes")
                    if eb_sub:
                        final_name = eb_name.strip() if eb_name.strip() else eb_sel
                        conn.execute(
                            "UPDATE branches SET name=?, status=? WHERE name=? AND organization=?",
                            (final_name, eb_status, eb_sel, org)
                        )
                        if final_name != eb_sel:
                            for tbl in ["users","attendance","ratings","leaves",
                                        "warnings","messages","kiosks","schedules"]:
                                try:
                                    conn.execute(
                                        f"UPDATE {tbl} SET branch=? WHERE branch=? AND organization=?",
                                        (final_name, eb_sel, org)
                                    )
                                except Exception:
                                    pass
                        conn.commit()
                        log_action(conn, user, "EDIT BRANCH", eb_sel, org)
                        st.success(f"Branch updated to '{final_name}' ({eb_status}).")
                        st.rerun()
            else:
                st.info("No branches to edit.")

        with tab_delete:
            if branches:
                del_br      = st.selectbox("Select Branch to Delete", branches, key="del_br_sel")
                del_confirm = st.text_input(f"Type '{del_br}' to confirm deletion")
                st.warning(f"Deleting '{del_br}' will also remove all users, attendance, and kiosk data.")
                if st.button("Delete Branch", type="primary", key="del_br_btn"):
                    if del_confirm.strip() == del_br:
                        for tbl in ["users","attendance","ratings","leaves",
                                    "warnings","messages","kiosks","schedules"]:
                            try:
                                conn.execute(
                                    f"DELETE FROM {tbl} WHERE branch=? AND organization=?", (del_br, org)
                                )
                            except Exception:
                                pass
                        conn.execute(
                            "DELETE FROM branches WHERE name=? AND organization=?", (del_br, org)
                        )
                        conn.commit()
                        log_action(conn, user, "DELETE BRANCH", del_br, org)
                        st.success(f"Branch '{del_br}' and all its data deleted.")
                        st.rerun()
                    else:
                        st.error("Confirmation text does not match.")
            else:
                st.info("No branches to delete.")

    # =========================================================
    # MANAGEMENT
    # =========================================================
    elif menu == "Management" and management_view == "Operations":
        st.subheader("Management")

        tab_topics, tab_leaves, tab_messages, tab_warnings, tab_kiosk, tab_pending = st.tabs([
            "Topics", "Leaves", "Messages", "Warnings", "Kiosk", "Pending Approvals"
        ])

        # ---- TOPICS ----
        with tab_topics:
            topics_df = safe_read("SELECT * FROM topics", conn)
            if safe_df(topics_df):
                st.dataframe(topics_df, use_container_width=True)

            with st.form("add_topic", clear_on_submit=False):
                new_topic = st.text_input("New Topic Name")
                topic_sub = st.form_submit_button("Add Topic")
                if topic_sub:
                    if not new_topic.strip():
                        st.error("Topic name required.")
                    else:
                        exists = safe_read(
                            "SELECT id FROM topics WHERE topic=?", conn, params=(new_topic.strip(),)
                        )
                        if not exists.empty:
                            st.error(f"Topic '{new_topic}' already exists.")
                        else:
                            conn.execute("INSERT INTO topics(topic) VALUES(?)", (new_topic.strip(),))
                            conn.commit()
                            log_action(conn, user, "ADD TOPIC", new_topic, org)
                            st.success(f"Topic '{new_topic}' added.")

            if safe_df(topics_df):
                st.divider()
                col_ed, col_del = st.columns(2)
                with col_ed:
                    with st.form("edit_topic", clear_on_submit=False):
                        et_sel = st.selectbox("Edit Topic", topics_df["topic"].tolist(), key="edit_topic_sel")
                        et_val = st.text_input("New Name")
                        et_sub = st.form_submit_button("Save")
                        if et_sub:
                            if et_val.strip():
                                conn.execute(
                                    "UPDATE topics SET topic=? WHERE topic=?",
                                    (et_val.strip(), et_sel)
                                )
                                conn.commit()
                                st.success(f"Topic renamed to '{et_val}'.")
                                st.rerun()
                            else:
                                st.error("New name required.")
                with col_del:
                    with st.form("del_topic", clear_on_submit=False):
                        dt_sel = st.selectbox("Delete Topic", topics_df["topic"].tolist(), key="del_topic_sel")
                        dt_sub = st.form_submit_button("Delete")
                        if dt_sub:
                            conn.execute("DELETE FROM topics WHERE topic=?", (dt_sel,))
                            conn.commit()
                            log_action(conn, user, "DELETE TOPIC", dt_sel, org)
                            st.success(f"Topic '{dt_sel}' deleted.")
                            st.rerun()

        # ---- LEAVES ----
        with tab_leaves:
            leaves_df = safe_read(
                "SELECT * FROM leaves WHERE organization=? ORDER BY id DESC",
                conn, params=(org,)
            )
            leaves_df = apply_branch_scope(leaves_df)
            if leaves_df.empty:
                st.info("No leave requests.")
            else:
                f_status = nav_selectbox(
                    "Filter by Status", ["All", "pending", "approved", "rejected", "reapply"],
                    key="leave_filter"
                )
                view_df = leaves_df if f_status == "All" else leaves_df[leaves_df["status"] == f_status]
                for _, lv in view_df.iterrows():
                    lid    = int(lv["id"])
                    status = str(lv.get("status", "pending")).lower()
                    icon   = {"approved": "Approved", "rejected": "Rejected", "reapply": "Reapply"}.get(status, "Pending")
                    st.markdown(
                        f"**{lv['username']}** | {str(lv.get('start_date',''))[:10]} to "
                        f"{str(lv.get('end_date',''))[:10]} | *{lv.get('reason','-')}* | Status: **{icon}**"
                    )
                    approver = str(lv.get("approved_by", "") or "").strip()
                    admin_note = str(lv.get("admin_note", "") or "").strip()
                    reviewed_at = str(lv.get("reviewed_at", "") or "").strip()
                    if approver or admin_note or reviewed_at:
                        meta_parts = []
                        if approver:
                            meta_parts.append(f"Handled by: {approver}")
                        if reviewed_at:
                            meta_parts.append(f"Reviewed: {reviewed_at[:16]}")
                        if meta_parts:
                            st.caption(" | ".join(meta_parts))
                        if admin_note:
                            st.write(f"Decision note: {admin_note}")

                    override_note = st.text_area("Decision / override note", key=f"sa_leave_note_{lid}")
                    c1, c2, c3 = st.columns(3)
                    if c1.button("Approve", key=f"lv_app_{lid}"):
                        conn.execute(
                            "UPDATE leaves SET status='approved', approved_by=?, admin_note=?, reviewed_at=datetime('now') WHERE id=?",
                            (user, override_note.strip(), lid),
                        )
                        conn.commit()
                        log_action(conn, user, "APPROVE LEAVE", str(lv.get("username", "")), org)
                        st.success("Leave approved.")
                        st.rerun()
                    if c2.button("Reject", key=f"lv_rej_{lid}"):
                        conn.execute(
                            "UPDATE leaves SET status='rejected', approved_by=?, admin_note=?, reviewed_at=datetime('now') WHERE id=?",
                            (user, override_note.strip(), lid),
                        )
                        conn.commit()
                        log_action(conn, user, "REJECT LEAVE", str(lv.get("username", "")), org)
                        st.warning("Leave rejected.")
                        st.rerun()
                    if c3.button("Request Reapply", key=f"lv_rei_{lid}"):
                        conn.execute(
                            "UPDATE leaves SET status='reapply', approved_by=?, admin_note=?, reviewed_at=datetime('now') WHERE id=?",
                            (user, override_note.strip(), lid),
                        )
                        conn.commit()
                        log_action(conn, user, "REQUEST LEAVE REAPPLY", str(lv.get("username", "")), org)
                        st.info("Employee asked to reapply with more info.")
                        st.rerun()
                    st.divider()

        # ---- MESSAGES ----
        with tab_messages:
            show_flash_message("super_admin_message_flash")
            msgs_df = safe_read(
                "SELECT * FROM messages WHERE organization=? ORDER BY id DESC",
                conn, params=(org,)
            )
            msgs_df = apply_branch_scope(msgs_df)
            if msgs_df.empty:
                st.info("No messages.")
            else:
                for _, msg in msgs_df.iterrows():
                    mid         = int(msg["id"])
                    is_imp      = str(msg.get("important", 0)) == "1"
                    sender_txt  = str(msg.get("sender", "unknown"))
                    msg_txt     = str(msg.get("message", ""))
                    created     = str(msg.get("created_at", ""))[:16]
                    imp_label   = "[IMPORTANT] " if is_imp else ""

                    with st.expander(f"{imp_label}From: {sender_txt} | {created}"):
                        st.write(msg_txt)

                        col_r, col_imp, col_del = st.columns(3)
                        with col_r:
                            with st.form(f"reply_{mid}", clear_on_submit=True):
                                reply_txt = st.text_area("Reply", key=f"reply_txt_{mid}")
                                if st.form_submit_button("Send Reply"):
                                    clean_reply = reply_txt.strip()
                                    reply_branch = str(msg.get("branch", "") or "").strip()
                                    if clean_reply:
                                        if is_recent_duplicate_message(
                                            conn,
                                            user,
                                            sender_txt,
                                            org,
                                            reply_branch,
                                            clean_reply,
                                        ):
                                            set_flash_message(
                                                "super_admin_message_flash",
                                                "warning",
                                                f"Duplicate reply blocked. '{sender_txt}' already received that message.",
                                            )
                                            st.rerun()
                                        try:
                                            conn.execute(
                                                """INSERT INTO messages(sender,receiver,branch,organization,message,created_at)
                                                   VALUES(?,?,?,?,?,datetime('now'))""",
                                                (user, sender_txt, reply_branch, org, clean_reply)
                                            )
                                        except Exception:
                                            conn.execute(
                                                """INSERT INTO messages(sender,receiver,organization,message,created_at)
                                                   VALUES(?,?,?,?,datetime('now'))""",
                                                (user, sender_txt, org, clean_reply)
                                            )
                                        conn.commit()
                                        set_flash_message(
                                            "super_admin_message_flash",
                                            "success",
                                            f"Reply sent to {sender_txt}.",
                                        )
                                        st.rerun()
                                    else:
                                        st.error("Reply cannot be empty.")

                        with col_imp:
                            cur_imp = 1 if is_imp else 0
                            lbl = "Unmark Important" if is_imp else "Mark Important"
                            if st.button(lbl, key=f"imp_{mid}"):
                                try:
                                    conn.execute(
                                        "UPDATE messages SET important=? WHERE id=?", (1 - cur_imp, mid)
                                    )
                                    conn.commit()
                                    st.rerun()
                                except Exception:
                                    st.warning("important column not in your schema.")

                        with col_del:
                            if st.button("Delete", key=f"del_msg_{mid}"):
                                conn.execute("DELETE FROM messages WHERE id=?", (mid,))
                                conn.commit()
                                st.success("Message deleted.")
                                st.rerun()

        # ---- WARNINGS ----
        with tab_warnings:
            st.markdown("### Payment Reminders")
            org_sub = safe_read(
                "SELECT expires_at, status FROM organizations WHERE name=?",
                conn,
                params=(org,),
            )
            if org_sub.empty:
                st.warning("Subscription details missing for this organization.")
            else:
                exp_raw = org_sub.iloc[0].get("expires_at")
                sub_status = str(org_sub.iloc[0].get("status", "active"))
                exp_dt = pd.to_datetime(exp_raw, errors="coerce")
                if pd.isna(exp_dt):
                    st.warning("Expiry date is not set. Configure payment plan details.")
                else:
                    days_left = (exp_dt.date() - date.today()).days
                    if days_left < 0:
                        st.error(f"Hard Alert: Subscription expired {abs(days_left)} day(s) ago.")
                    elif days_left <= 7:
                        st.error(f"Hard Alert: Subscription expires in {days_left} day(s).")
                    elif days_left <= 30:
                        st.warning(f"Reminder: Subscription expires in {days_left} day(s).")
                    else:
                        st.success(f"Subscription healthy: {days_left} day(s) remaining.")
                st.caption(f"Organization status: {sub_status}")

            ratings_all   = safe_read("SELECT * FROM ratings WHERE organization=?",   conn, params=(org,))
            attendance_all = safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(org,))
            lateness_all = safe_read(
                "SELECT username, approved_for_date, reason, approved_by, status, actual_reason, used_at FROM lateness_approvals WHERE organization=?",
                conn,
                params=(org,),
            )
            users_all      = safe_read("SELECT * FROM users WHERE organization=?",     conn, params=(org,))
            ratings_all = apply_branch_scope(ratings_all)
            attendance_all = apply_branch_scope(attendance_all)
            lateness_all = apply_branch_scope(lateness_all)
            users_all = apply_branch_scope(users_all)

            smart_warns = []

            if safe_df(ratings_all):
                avg_scores = ratings_all.groupby("rated")["score"].mean()
                for uname, avg in avg_scores.items():
                    if avg < 40:
                        smart_warns.append(("Low Performer", uname, f"Avg score {round(avg,1)} - below 40"))

            if safe_df(attendance_all):
                attendance_all = annotate_attendance_lateness(attendance_all, lateness_all)
                late_counts = attendance_all[attendance_all["true_late"] == True].groupby("username").size()  # noqa: E712
                for uname, cnt in late_counts.items():
                    if cnt >= 3:
                        smart_warns.append(("Frequent Latecomer", uname, f"Late {cnt} times"))

                approved_late_counts = attendance_all[attendance_all["approved_late"] == True].groupby("username").size()  # noqa: E712
                for uname, cnt in approved_late_counts.items():
                    if cnt >= 3:
                        smart_warns.append(("Frequent Approved Lateness", uname, f"Approved late {cnt} times"))

                if safe_df(users_all):
                    emp_users = users_all[users_all["role"] == "employee"]["username"].tolist()
                    clock_counts = attendance_all.groupby("username").size()
                    for uname in emp_users:
                        cnt = clock_counts.get(uname, 0)
                        if cnt < 5:
                            smart_warns.append(("Low Attendance Input", uname, f"Only {cnt} clock-in records"))

                    try:
                        attendance_all["date_dt"] = pd.to_datetime(attendance_all["date"], errors="coerce")
                        last_30 = attendance_all[
                            attendance_all["date_dt"] >= (datetime.now() - timedelta(days=30))
                        ]
                        last_30_count = last_30.groupby("username").size()
                        for uname in emp_users:
                            cnt = int(last_30_count.get(uname, 0))
                            if cnt < 10:
                                smart_warns.append(("Absenteeism Risk", uname, f"Only {cnt} records in last 30 days"))
                    except Exception:
                        pass

            if safe_df(ratings_all) and safe_df(users_all):
                mgr_users = users_all[users_all["role"].isin(["admin","superadmin"])]["username"].tolist()
                for mgr in mgr_users:
                    mgr_rated = ratings_all[ratings_all["rated"] == mgr]
                    if len(mgr_rated) >= 4:
                        try:
                            mgr_rated = mgr_rated.sort_values("created_at")
                            half = len(mgr_rated) // 2
                            first_avg  = mgr_rated.iloc[:half]["score"].mean()
                            second_avg = mgr_rated.iloc[half:]["score"].mean()
                            if second_avg < first_avg - 10:
                                smart_warns.append(("Non-improving Manager", mgr,
                                    f"Score dropped {round(first_avg,1)} â†’ {round(second_avg,1)}"))
                        except Exception:
                            pass

                try:
                    for br in users_all["branch"].dropna().unique():
                        br_mgrs = users_all[(users_all["branch"] == br) & (users_all["role"] == "admin")]["username"].tolist()
                        br_emps = users_all[(users_all["branch"] == br) & (users_all["role"] == "employee")]["username"].tolist()
                        for mgr in br_mgrs:
                            conflict_ratings = ratings_all[
                                (ratings_all["rated"] == mgr) & (ratings_all["rater"].isin(br_emps))
                            ]
                            if len(conflict_ratings) >= 3 and conflict_ratings["score"].mean() < 35:
                                smart_warns.append(("Manager-Employee Conflict", mgr,
                                    f"Branch '{br}': employee avg rating {round(conflict_ratings['score'].mean(),1)}"))
                except Exception:
                    pass

            st.markdown("### Detected Concerns")
            if smart_warns:
                for wtype, uname, detail in smart_warns:
                    st.warning(f"**{wtype}** - {uname}: {clean_display_text(detail)}")
            else:
                st.success("No issues detected.")

            st.divider()
            st.markdown("### Send Warning to User")
            if safe_df(users_all):
                with st.form("send_warning", clear_on_submit=False):
                    w_user = st.selectbox("Select User", users_all["username"].tolist(), key="w_user_sel")
                    w_type = st.selectbox("Warning Type", [
                        "lateness","absenteeism","low_performance","misconduct","policy_violation"
                    ])
                    w_msg  = st.text_area("Warning Message")
                    w_sub  = st.form_submit_button("Send Warning")
                    if w_sub:
                        if not w_msg.strip():
                            st.error("Warning message is required.")
                        else:
                            try:
                                conn.execute(
                                    "INSERT INTO warnings(username,organization,branch,type,message,created_at) VALUES(?,?,?,?,?,datetime('now'))",
                                    (w_user, org, "", w_type, w_msg.strip())
                                )
                            except Exception:
                                conn.execute(
                                    "INSERT INTO warnings(username,type,message) VALUES(?,?,?)",
                                    (w_user, w_type, w_msg.strip())
                                )
                            conn.commit()
                            log_action(conn, user, "SEND WARNING", w_user, org)
                            st.success(f"Warning sent to {w_user}.")

            warns_history = safe_read("SELECT * FROM warnings WHERE organization=?", conn, params=(org,))
            warns_history = apply_branch_scope(warns_history)
            if safe_df(warns_history):
                st.divider()
                st.markdown("### Warning History")
                st.dataframe(warns_history, use_container_width=True)

        # ---- KIOSK (inside Management) ----
        with tab_kiosk:
            kiosks_df = safe_read("SELECT * FROM kiosks WHERE organization=?", conn, params=(org,))
            st.caption("Showing all kiosks across all branches.")
            if safe_df(kiosks_df):
                st.dataframe(kiosks_df, use_container_width=True)
            st.divider()
            if branches:
                with st.form("create_kiosk_mgmt", clear_on_submit=False):
                    k_branch = st.selectbox("Assign Branch", branches, key="kiosk_br_mgmt")
                    k_dev    = st.text_input("Device Label")
                    k_sub    = st.form_submit_button("Create Kiosk")
                    if k_sub:
                        dev_label = k_dev.strip() or f"{k_branch}-Kiosk"
                        dup = safe_read(
                            "SELECT id FROM kiosks WHERE branch=? AND organization=? AND device_name=?",
                            conn, params=(k_branch, org, dev_label)
                        )
                        if not dup.empty:
                            st.error("Kiosk with this name already exists for this branch.")
                        else:
                            try:
                                conn.execute(
                                    "INSERT INTO kiosks(branch,organization,device_name,last_active,status) VALUES(?,?,?,datetime('now'),'active')",
                                    (k_branch, org, dev_label)
                                )
                            except Exception:
                                conn.execute(
                                    "INSERT INTO kiosks(branch,organization,device_name,last_active) VALUES(?,?,?,datetime('now'))",
                                    (k_branch, org, dev_label)
                                )
                            conn.commit()
                            log_action(conn, user, "CREATE KIOSK", dev_label, org)
                            st.success(f"Kiosk '{dev_label}' created for '{k_branch}'.")
                            st.code(kiosk_link(k_branch, org), language="text")
            else:
                st.info("Create a branch first.")

        # ---- PENDING APPROVALS ----
        with tab_pending:
            st.markdown("### Pending Leave Approvals")
            pending_lv = safe_read(
                "SELECT * FROM leaves WHERE organization=? AND status='pending' ORDER BY id DESC",
                conn, params=(org,)
            )
            pending_lv = apply_branch_scope(pending_lv)
            if pending_lv.empty:
                st.success("No pending leave requests.")
            else:
                st.warning(f"{len(pending_lv)} pending leave(s) awaiting action.")
                for _, lv in pending_lv.iterrows():
                    lid = int(lv["id"])
                    st.markdown(
                        f"**{lv['username']}** | {str(lv.get('start_date',''))[:10]} to "
                        f"{str(lv.get('end_date',''))[:10]} | *{lv.get('reason','-')}*"
                    )
                    pa1, pa2, pa3 = st.columns(3)
                    if pa1.button("Approve",         key=f"pa_app_{lid}"):
                        conn.execute("UPDATE leaves SET status='approved' WHERE id=?", (lid,))
                        conn.commit()
                        st.success("Approved.")
                        st.rerun()
                    if pa2.button("Reject",          key=f"pa_rej_{lid}"):
                        conn.execute("UPDATE leaves SET status='rejected' WHERE id=?", (lid,))
                        conn.commit()
                        st.warning("Rejected.")
                        st.rerun()
                    if pa3.button("Request Reapply", key=f"pa_rei_{lid}"):
                        conn.execute("UPDATE leaves SET status='reapply' WHERE id=?", (lid,))
                        conn.commit()
                        st.info("Sent back for more info.")
                        st.rerun()
                    st.divider()

    # =========================================================
    # PAYMENTS
    # =========================================================
    elif menu == "Payments":
        st.subheader("Payments")

        branches_all = safe_read("SELECT * FROM branches WHERE organization=?", conn, params=(org,))
        if branch_scope and not branches_all.empty and "name" in branches_all.columns:
            branches_all = branches_all[branches_all["name"].astype(str) == str(branch_scope)]
        org_row = safe_read(
            "SELECT name, expires_at, status FROM organizations WHERE name=?",
            conn,
            params=(org,),
        )
        payments_df = safe_read(
            "SELECT amount, method, phone, created_at FROM payments WHERE organization=? ORDER BY created_at DESC",
            conn,
            params=(org,),
        )
        cfg_df = safe_read("SELECT * FROM payment_config WHERE id=1", conn)

        cfg = cfg_df.iloc[0] if not cfg_df.empty else pd.Series({
            "paybill": "",
            "till_number": "",
            "bank_name": "",
            "bank_account": "",
            "bank_branch": "",
            "price_single_branch": 1000,
            "price_per_branch": 800,
        })

        branch_count = len(branches_all)
        current_plan_price = calc_plan_price(branch_count, cfg)
        next_plan_price = calc_plan_price(branch_count + 1, cfg)
        increment_cost = max(next_plan_price - current_plan_price, 0)

        p1, p2, p3 = st.columns(3)
        p1.metric("Current Branch Count", branch_count)
        p2.metric("Current Plan Cost", f"KES {current_plan_price:,}")
        p3.metric("+1 Branch Upgrade", f"KES {increment_cost:,}")

        if org_row.empty:
            st.warning("Organization subscription details not found.")
        else:
            expiry_raw = org_row.iloc[0].get("expires_at")
            status_raw = str(org_row.iloc[0].get("status", "active"))
            expiry_dt = pd.to_datetime(expiry_raw, errors="coerce")
            if pd.isna(expiry_dt):
                st.info("No expiry date set yet.")
            else:
                days_left = (expiry_dt.date() - date.today()).days
                if days_left < 0:
                    st.error(f"Subscription expired {abs(days_left)} day(s) ago. Renew payment now.")
                elif days_left <= 7:
                    st.warning(f"Subscription expires in {days_left} day(s). Plan payment now.")
                elif days_left <= 30:
                    st.info(f"Subscription expires in {days_left} day(s).")
                else:
                    st.success(f"Subscription active. {days_left} day(s) remaining.")
            st.caption(f"Organization status: {status_raw}")

        st.markdown("**Payment Methods Available**")
        methods = []
        paybill = str(cfg.get("paybill", "")).strip()
        till = str(cfg.get("till_number", "")).strip()
        bank_name = str(cfg.get("bank_name", "")).strip()
        bank_account = str(cfg.get("bank_account", "")).strip()
        bank_branch = str(cfg.get("bank_branch", "")).strip()

        if paybill:
            methods.append({"Method": "M-Pesa Paybill", "Details": paybill})
        if till:
            methods.append({"Method": "M-Pesa Till", "Details": till})
        if bank_name or bank_account:
            methods.append({
                "Method": "Bank Transfer",
                "Details": f"{bank_name} | Acc: {bank_account} | Branch: {bank_branch}".strip(" |"),
            })

        if methods:
            st.dataframe(pd.DataFrame(methods), use_container_width=True)
        else:
            st.info("No payment method configured yet. Ask master admin to set payment configuration.")

        st.markdown("**Upgrade Plan Preview**")
        upgrade_rows = []
        for add_n in [1, 2, 3, 5, 10]:
            target = branch_count + add_n
            target_price = calc_plan_price(target, cfg)
            upgrade_rows.append({
                "If You Add": f"+{add_n} branches",
                "Total Branches": target,
                "Estimated Plan": f"KES {target_price:,}",
                "Extra Cost": f"KES {max(target_price - current_plan_price, 0):,}",
            })
        st.dataframe(pd.DataFrame(upgrade_rows), use_container_width=True)

        st.markdown("**Recent Payments**")
        if payments_df.empty:
            st.warning("No payments recorded yet for this organization.")
        else:
            payments_df = payments_df.copy()
            payments_df["created_at"] = pd.to_datetime(payments_df["created_at"], errors="coerce")
            total_paid = float(payments_df["amount"].fillna(0).sum())
            st.metric("Total Paid", f"KES {total_paid:,.2f}")
            method_summary = payments_df.groupby("method", dropna=False)["amount"].sum().reset_index()
            method_summary.columns = ["Method", "Total Amount"]
            st.dataframe(method_summary, use_container_width=True)
            st.dataframe(payments_df.head(20), use_container_width=True)

    # =========================================================
    # DEMOGRAPHICS
    # =========================================================
    elif menu == "Analytics" and analytics_view == "Demographics":
        st.subheader("Group Demographics")
        
        if not ANALYTICS_OK:
            st.warning("Analytics module unavailable.")
        else:
            demo_options = ["All Branches"] + branches
            demo_default_idx = demo_options.index(branch_scope) if branch_scope in demo_options else 0
            sel_br_demo = nav_selectbox("Filter by Branch", demo_options, key="demo_branch_sel", index=demo_default_idx)
            branch_filter_demo = None if sel_br_demo == "All Branches" else sel_br_demo
            
            if st.button("Analyze Demographics", type="primary"):
                # Run and save fresh data
                with st.spinner("Analyzing demographics..."):
                    try:
                        from Analytics.group_demographics import analyze_group_demographics, save_group_demographics_to_db
                        users_df_d   = safe_read("SELECT * FROM users WHERE organization=?", conn, params=(org,))
                        ratings_df_d = safe_read("SELECT * FROM ratings WHERE organization=?", conn, params=(org,))
                        att_df_d     = safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(org,))
                        # Apply role filter
                        if ANALYTICS_OK and not users_df_d.empty and "role" in users_df_d.columns:
                            excluded = ["master", "super_admin"]
                            users_df_d = users_df_d[~users_df_d["role"].isin(excluded)]
                            if not ratings_df_d.empty and "ratee" in ratings_df_d.columns:
                                ratings_df_d = ratings_df_d[~ratings_df_d["ratee"].isin(users_df_d["username"] == False)]
                        demo_result = analyze_group_demographics(ratings_df_d, att_df_d, users_df_d, org, branch_filter_demo)
                        save_group_demographics_to_db(org, branch_filter_demo or "all", demo_result)
                        st.success("Demographics updated.")
                    except Exception as e:
                        st.error(f"Error: {e}")
            
            # Display statistics
            try:
                stats = get_demographic_statistics(org, branch_filter_demo)
                
                if stats:
                    st.divider()
                    st.markdown("### Gender Distribution")
                    
                    gd = stats.get("gender_distribution", {})
                    total_emp = gd.get("total_employees", 0)
                    male_c    = gd.get("male_count", 0)
                    female_c  = gd.get("female_count", 0)
                    other_c   = gd.get("other_count", 0)
                    
                    gc1, gc2, gc3, gc4 = st.columns(4)
                    gc1.metric("Total Employees", total_emp)
                    gc2.metric("Male", male_c)
                    gc3.metric("Female", female_c)
                    gc4.metric("Other/Unknown", other_c)
                    
                    # Group type breakdown
                    st.divider()
                    st.markdown("### Groups by Type")
                    
                    groups_by_type = stats.get("groups_by_type", {})
                    if groups_by_type:
                        gbt_df = pd.DataFrame([
                            {"Group Type": k, "Count": v} for k, v in groups_by_type.items()
                        ])
                        st.dataframe(gbt_df, use_container_width=True)
                    
                    # High risk groups
                    st.divider()
                    st.markdown("### High Risk Groups")
                    
                    high_risk = stats.get("high_risk_groups", [])
                    if high_risk:
                        for grp in high_risk:
                            st.error(
                                f"**{grp.get('group_type', 'Group')}** - {grp.get('members', [])} - Risk: {grp.get('risk_level', 'High')}"
                            )
                    else:
                        st.success("No high-risk groups detected.")
                else:
                    st.info("No demographic data yet. Click 'Analyze Demographics' to generate.")
            
            except Exception as e:
                st.error(f"Could not load demographic stats: {e}")
            
            # Detailed group table
            st.divider()
            st.markdown("### Group Details")
            
            risk_filter = nav_selectbox("Filter by Risk Level", ["All", "high", "medium", "low"], key="demo_risk_sel")
            risk_val = None if risk_filter == "All" else risk_filter
            
            try:
                group_details = get_group_details_for_super_admin(org, branch_filter_demo, risk_val)
                if group_details:
                    gd_df = pd.DataFrame(group_details)
                    show_cols = [c for c in ["group_type", "members", "risk_level", "member_count", "avg_rating", "male_count", "female_count"] if c in gd_df.columns]
                    st.dataframe(gd_df[show_cols], use_container_width=True)
                else:
                    st.info("No group detail records found.")
            except Exception as e:
                st.error(f"Could not load group details: {e}")

    # =========================================================
    # ANALYTICS
    # =========================================================
    elif menu == "Analytics" and analytics_view == "Performance":
        st.subheader("Analytics")

        tab_by_branch, tab_overall = st.tabs(["By Branch", "Overall All Branches"])

        with tab_by_branch:
            if branches:
                perf_branch_options = ["All Branches"] + branches
                perf_branch_default_idx = perf_branch_options.index(branch_scope) if branch_scope in perf_branch_options else 0
                sel_br = nav_selectbox("Select Branch", perf_branch_options, key="analytics_br_sel", index=perf_branch_default_idx)

                if sel_br == "All Branches":
                    ratings = apply_branch_scope(safe_read("SELECT * FROM ratings WHERE organization=?", conn, params=(org,)))
                    attendance = apply_branch_scope(safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(org,)))
                    schedules = apply_branch_scope(safe_read("SELECT * FROM schedules WHERE organization=?", conn, params=(org,)))
                    users_br = apply_branch_scope(safe_read("SELECT * FROM users WHERE organization=?", conn, params=(org,)))
                    leaves_br = apply_branch_scope(safe_read("SELECT * FROM leaves WHERE organization=?", conn, params=(org,)))
                    messages_br = apply_branch_scope(safe_read("SELECT * FROM messages WHERE organization=?", conn, params=(org,)))
                else:
                    ratings = safe_read("SELECT * FROM ratings WHERE organization=? AND branch=?", conn, params=(org, sel_br))
                    attendance = safe_read("SELECT * FROM attendance WHERE organization=? AND branch=?", conn, params=(org, sel_br))
                    schedules = safe_read("SELECT * FROM schedules WHERE organization=? AND branch=?", conn, params=(org, sel_br))
                    users_br = safe_read("SELECT * FROM users WHERE organization=? AND branch=?", conn, params=(org, sel_br))
                    leaves_br = safe_read("SELECT * FROM leaves WHERE organization=? AND branch=?", conn, params=(org, sel_br))
                    messages_br = safe_read("SELECT * FROM messages WHERE organization=? AND branch=?", conn, params=(org, sel_br))

                bm1, bm2, bm3 = st.columns(3)
                bm1.metric("Ratings",           len(ratings))
                bm2.metric("Attendance Records", len(attendance))
                bm3.metric("Users",             len(users_br))

                if not ANALYTICS_OK:
                    st.warning("Analytics modules not available.")
                elif ratings.empty:
                    st.info("No ratings data for this branch.")
                else:
                    st.markdown("### Branch Performance Ladder")
                    branch_per_user = (
                        ratings.groupby("rated", as_index=False)["score"]
                        .mean()
                        .rename(columns={"rated": "User", "score": "Avg Score"})
                    )
                    top_branch = branch_per_user.sort_values("Avg Score", ascending=False).head(5).reset_index(drop=True)
                    low_branch = branch_per_user.sort_values("Avg Score", ascending=True).head(5).reset_index(drop=True)

                    lp1, lp2, lp3 = st.columns(3)
                    lp1.metric("Branch Average", round(float(ratings["score"].mean()), 1))
                    lp2.metric("Top Performer", str(top_branch.iloc[0]["User"]) if not top_branch.empty else "N/A")
                    lp3.metric("Lowest Performer", str(low_branch.iloc[0]["User"]) if not low_branch.empty else "N/A")

                    lcol1, lcol2 = st.columns(2)
                    with lcol1:
                        st.markdown("**Top 5**")
                        st.dataframe(top_branch, use_container_width=True)
                    with lcol2:
                        st.markdown("**Lowest 5**")
                        st.dataframe(low_branch, use_container_width=True)

                    st.markdown("**Branch Recommendations (for low performers)**")
                    if low_branch.empty:
                        st.info("No low performer signals in this branch right now.")
                    else:
                        for _, row in low_branch.iterrows():
                            user_name = str(row.get("User", ""))
                            user_score = float(row.get("Avg Score", 0))
                            if user_score < 55:
                                st.warning(f"ACTION: {user_name} ({user_score:.1f}) needs immediate coaching plan and weekly review.")
                            elif user_score < 70:
                                st.info(f"SUPPORT: {user_name} ({user_score:.1f}) needs targeted mentorship and topic-based improvement goals.")
                            else:
                                st.success(f"KEEP: {user_name} ({user_score:.1f}) is stable but should maintain consistency.")

                    branch_scope_label = sel_br if sel_br != "All Branches" else (branch_scope if branch_scope else "All Branches")
                    render_favorite_relationship_section(ratings, branch_scope_label)

                    st.divider()

                    at1, at2, at3, at4, at5, at6 = st.tabs([
                        "Reports", "Insights", "Leadership", "Powermap", "Predictions", "Decisions"
                    ])
                    with at1:
                        try:
                            reports_panel()
                        except Exception as e:
                            st.warning(f"Reports unavailable: {e}")
                    with at2:
                        try:
                            for i in generate_insights(ratings, attendance, leaves_br, users_br, messages_br):
                                st.info(i)
                        except Exception as e:
                            st.warning(f"Insights unavailable: {e}")
                    with at3:
                        try:
                            leaders = detect_leaders(ratings, attendance, leaves_br, users_br, messages_br)
                            if isinstance(leaders, tuple):
                                leaders_df, leaders_insights = leaders
                                if leaders_insights:
                                    for li in leaders_insights:
                                        st.write(f"{li}")
                                if safe_df(leaders_df):
                                    st.dataframe(leaders_df, use_container_width=True)
                            elif safe_df(leaders):
                                st.dataframe(leaders, use_container_width=True)
                        except Exception as e:
                            st.warning(f"Leadership unavailable: {e}")
                    with at4:
                        try:
                            display_powermap(ratings)
                        except Exception as e:
                            st.warning(f"Powermap unavailable: {e}")
                    with at5:
                        try:
                            preds = predict_future(ratings, attendance, users_br)
                            if isinstance(preds, list):
                                for p in preds:
                                    st.warning(p)
                            else:
                                st.warning(str(preds))
                        except Exception as e:
                            st.warning(f"Predictions unavailable: {e}")
                    with at6:
                        try:
                            recs = management_recommendations(ratings, attendance, schedules)
                            if isinstance(recs, list):
                                if recs:
                                    for r in recs:
                                        st.success(r)
                                else:
                                    st.info("No urgent decisions right now. Keep monitoring weekly ratings and attendance.")
                            else:
                                st.success(str(recs))
                        except Exception as e:
                            st.warning(f"Decisions unavailable: {e}")
            else:
                st.info("No branches available.")

        with tab_overall:
            ratings_all = apply_branch_scope(safe_read("SELECT * FROM ratings WHERE organization=?", conn, params=(org,)))
            attendance_all = apply_branch_scope(safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(org,)))
            users_all = apply_branch_scope(safe_read("SELECT * FROM users WHERE organization=?", conn, params=(org,)))
            leaves_all = apply_branch_scope(safe_read("SELECT * FROM leaves WHERE organization=?", conn, params=(org,)))
            messages_all = apply_branch_scope(safe_read("SELECT * FROM messages WHERE organization=?", conn, params=(org,)))
            schedules_all = apply_branch_scope(safe_read("SELECT * FROM schedules WHERE organization=?", conn, params=(org,)))
            warnings_all = apply_branch_scope(safe_read("SELECT * FROM warnings WHERE organization=?", conn, params=(org,)))

            if ratings_all.empty:
                st.info("No ratings data yet.")
            else:
                ob1, ob2 = st.columns(2)
                ob1.metric("Total Ratings",         len(ratings_all))
                ob2.metric("Attendance Records",    len(attendance_all))

                st.markdown("**Branch Performance Comparison**")
                branch_avg = ratings_all.groupby("branch").agg(
                    Avg_Score=("score", "mean"),
                    Rating_Count=("score", "count"),
                    Rated_Users=("rated", "nunique"),
                ).reset_index().rename(columns={"branch": "Branch"})
                branch_avg["Avg_Score"] = branch_avg["Avg_Score"].round(2)

                if not attendance_all.empty and "branch" in attendance_all.columns and "status" in attendance_all.columns:
                    attendance_compare = attendance_all.copy()
                    attendance_compare["is_late"] = attendance_compare["status"].astype(str).str.upper() == "LATE"
                    late_view = attendance_compare.groupby("branch").agg(
                        Late_Records=("is_late", "sum"),
                        Attendance_Records=("is_late", "count"),
                    ).reset_index()
                    late_view["Late_Rate_%"] = (
                        (late_view["Late_Records"] / late_view["Attendance_Records"].replace(0, 1)) * 100
                    ).round(1)
                    branch_avg = branch_avg.merge(
                        late_view.rename(columns={"branch": "Branch"})[["Branch", "Late_Rate_%"]],
                        on="Branch",
                        how="left",
                    )

                branch_avg = branch_avg.sort_values(["Avg_Score", "Rating_Count"], ascending=[False, False])
                compare_scope_label = "All Branches" if not branch_scope else f"{branch_scope} (selected branch)"
                st.caption(f"Scope: {compare_scope_label}")

                if len(branch_avg) >= 2:
                    best_branch_name = str(branch_avg.iloc[0]["Branch"])
                    best_branch_score = float(branch_avg.iloc[0]["Avg_Score"])
                    worst_branch_name = str(branch_avg.iloc[-1]["Branch"])
                    worst_branch_score = float(branch_avg.iloc[-1]["Avg_Score"])
                    gap = best_branch_score - worst_branch_score
                    bc1, bc2, bc3 = st.columns(3)
                    bc1.metric("Best Branch", f"{best_branch_name} ({best_branch_score:.1f})")
                    bc2.metric("Lowest Branch", f"{worst_branch_name} ({worst_branch_score:.1f})")
                    bc3.metric("Performance Gap", f"{gap:.1f}")
                elif len(branch_avg) == 1:
                    only_branch_name = str(branch_avg.iloc[0]["Branch"])
                    only_branch_score = float(branch_avg.iloc[0]["Avg_Score"])
                    st.info(f"Current comparison scope has one branch: {only_branch_name} (avg score {only_branch_score:.1f}).")

                if PLOTLY_OK:
                    try:
                        fig = px.bar(
                            branch_avg, x="Branch", y="Avg_Score",
                            color="Avg_Score", color_continuous_scale="Teal",
                            title="Branch Comparison Performance"
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    except Exception:
                        st.dataframe(branch_avg, use_container_width=True)
                else:
                    st.dataframe(branch_avg, use_container_width=True)

                st.markdown("### Overall Best Performers")
                with st.expander("How Ranking Is Calculated", expanded=False):
                    st.markdown(
                        """
                        - Primary rule: higher average score ranks higher.
                        - Tie-breaker 1: if average scores are equal, higher rating count ranks higher.
                        - Tie-breaker 2: if still tied, current dataframe order is used.
                        - Best Employee: top ranked user with role = employee.
                        - Best Admin: top ranked user with role in admin/superadmin.
                        - Best Overall: top ranked user across all roles in current scope.
                        - Scope rule: ranking uses current Branch View selection (All Branches or one branch).
                        """
                    )
                user_scores = ratings_all.groupby("rated").agg(
                    avg_score=("score", "mean"),
                    rating_count=("score", "count"),
                ).reset_index().rename(columns={"rated": "username"})
                user_scores["avg_score"] = user_scores["avg_score"].round(2)

                role_map = pd.DataFrame(columns=["username", "role", "branch"])
                if not users_all.empty and "username" in users_all.columns:
                    role_cols = [c for c in ["username", "role", "branch"] if c in users_all.columns]
                    role_map = users_all[role_cols].copy()
                    role_map["role"] = role_map.get("role", "").astype(str).str.lower()
                    role_map = role_map.drop_duplicates(subset=["username"], keep="last")

                ranked = user_scores.merge(role_map, on="username", how="left")
                ranked["role"] = ranked["role"].fillna("unknown")
                ranked["branch"] = ranked.get("branch", "").fillna("")
                ranked = ranked.sort_values(["avg_score", "rating_count"], ascending=[False, False])

                employee_ranked = ranked[ranked["role"] == "employee"].copy()
                admin_ranked = ranked[ranked["role"].isin(["admin", "superadmin", "super_admin"])].copy()

                best_all = ranked.head(1)
                best_employee = employee_ranked.head(1)
                best_admin = admin_ranked.head(1)

                p1, p2, p3 = st.columns(3)
                p1.metric(
                    "Best Employee",
                    f"{best_employee.iloc[0]['username']} ({best_employee.iloc[0]['avg_score']:.1f})" if not best_employee.empty else "N/A",
                )
                p2.metric(
                    "Best Admin",
                    f"{best_admin.iloc[0]['username']} ({best_admin.iloc[0]['avg_score']:.1f})" if not best_admin.empty else "N/A",
                )
                p3.metric(
                    "Best Overall",
                    f"{best_all.iloc[0]['username']} ({best_all.iloc[0]['avg_score']:.1f})" if not best_all.empty else "N/A",
                )

                st.markdown("**Top Performers - Employees**")
                if employee_ranked.empty:
                    st.info("No employee performance rows in this scope.")
                else:
                    st.dataframe(employee_ranked[["username", "branch", "avg_score", "rating_count"]].head(10), use_container_width=True)

                st.markdown("**Top Performers - Admins**")
                if admin_ranked.empty:
                    st.info("No admin performance rows in this scope.")
                else:
                    st.dataframe(admin_ranked[["username", "branch", "avg_score", "rating_count"]].head(10), use_container_width=True)

                st.markdown("**Top Performers - All Roles**")
                st.dataframe(ranked[["username", "role", "branch", "avg_score", "rating_count"]].head(15), use_container_width=True)

                # Show low performers with the same scope + ranking rules (inverse order).
                low_employee = employee_ranked.sort_values(["avg_score", "rating_count"], ascending=[True, True])
                low_admin = admin_ranked.sort_values(["avg_score", "rating_count"], ascending=[True, True])
                low_all = ranked.sort_values(["avg_score", "rating_count"], ascending=[True, True])

                st.markdown("**Low Performers - Employees**")
                if low_employee.empty:
                    st.info("No employee performance rows in this scope.")
                else:
                    low_employee_view = low_employee[["username", "branch", "avg_score", "rating_count"]].head(10).copy()

                    def _employee_gang_signal(employee_name):
                        if ratings_all.empty or "rated" not in ratings_all.columns or "score" not in ratings_all.columns:
                            return "No data"

                        incoming = ratings_all[ratings_all["rated"].astype(str) == str(employee_name)]
                        if incoming.empty:
                            return "No data"

                        incoming_low = incoming[pd.to_numeric(incoming["score"], errors="coerce") < 45]
                        total_raters = int(incoming["rater"].astype(str).nunique()) if "rater" in incoming.columns else int(len(incoming))
                        low_raters = int(incoming_low["rater"].astype(str).nunique()) if "rater" in incoming_low.columns else int(len(incoming_low))
                        low_ratio = low_raters / max(total_raters, 1)

                        outgoing = ratings_all[ratings_all.get("rater", "").astype(str) == str(employee_name)] if "rater" in ratings_all.columns else pd.DataFrame()
                        outgoing_avg = float(pd.to_numeric(outgoing.get("score"), errors="coerce").mean()) if not outgoing.empty else 0.0

                        warn_count = 0
                        if not warnings_all.empty and "username" in warnings_all.columns:
                            warn_count = int((warnings_all["username"].astype(str) == str(employee_name)).sum())

                        late_rate = 0.0
                        absent_rate = 0.0
                        if not attendance_all.empty and "username" in attendance_all.columns and "status" in attendance_all.columns:
                            emp_att = attendance_all[attendance_all["username"].astype(str) == str(employee_name)]
                            if not emp_att.empty:
                                late_rate = float((emp_att["status"].astype(str).str.upper() == "LATE").mean() * 100)
                                absent_rate = float((emp_att["status"].astype(str).str.upper() == "ABSENT").mean() * 100)

                        # Differentiation: likely gang pressure vs likely true low performance/behavior issue.
                        if total_raters >= 4 and low_raters >= 3 and low_ratio >= 0.60 and outgoing_avg >= 55 and warn_count == 0 and late_rate < 25 and absent_rate < 10:
                            return f"Possible gang against employee ({low_raters}/{total_raters} low raters)"
                        if total_raters >= 4 and low_raters >= 3 and low_ratio >= 0.60:
                            return f"Heavy negative cluster ({low_raters}/{total_raters})"
                        return "No clear gang signal"

                    def _employee_low_reason(employee_name):
                        reasons = []

                        row = low_employee[low_employee["username"].astype(str) == str(employee_name)]
                        if not row.empty:
                            avg_score = float(row.iloc[0].get("avg_score", 0) or 0)
                            rating_count = int(row.iloc[0].get("rating_count", 0) or 0)
                            if avg_score < 52:
                                reasons.append(f"Very low peer rating ({avg_score:.1f})")
                            elif avg_score < 55:
                                reasons.append(f"Below-target peer rating ({avg_score:.1f})")
                            if rating_count < 5:
                                reasons.append("Few ratings captured (low confidence)")

                        if not attendance_all.empty and "username" in attendance_all.columns and "status" in attendance_all.columns:
                            emp_att = attendance_all[attendance_all["username"].astype(str) == str(employee_name)]
                            if not emp_att.empty:
                                late_rate = float((emp_att["status"].astype(str).str.upper() == "LATE").mean() * 100)
                                absent_rate = float((emp_att["status"].astype(str).str.upper() == "ABSENT").mean() * 100)
                                if late_rate >= 25:
                                    reasons.append(f"Frequent lateness ({late_rate:.0f}% records)")
                                if absent_rate >= 10:
                                    reasons.append(f"Absenteeism risk ({absent_rate:.0f}% records)")

                        if not warnings_all.empty and "username" in warnings_all.columns:
                            warn_count = int((warnings_all["username"].astype(str) == str(employee_name)).sum())
                            if warn_count >= 1:
                                reasons.append(f"Warning history ({warn_count})")

                        if not reasons:
                            reasons.append("Low ranking from current performance mix")

                        return "; ".join(reasons[:3])

                    low_employee_view["Reason"] = low_employee_view["username"].apply(_employee_low_reason)
                    low_employee_view["Staff Gang Signal"] = low_employee_view["username"].apply(_employee_gang_signal)
                    st.dataframe(
                        low_employee_view[["username", "branch", "avg_score", "rating_count", "Reason", "Staff Gang Signal"]],
                        use_container_width=True,
                    )

                    suspected_emp_gang = low_employee_view[
                        low_employee_view["Staff Gang Signal"].astype(str).str.contains("Possible gang against employee|Heavy negative cluster", case=False, na=False)
                    ]
                    if not suspected_emp_gang.empty:
                        st.warning("Potential staff-gang signals detected against the following employee(s):")
                        for _, row in suspected_emp_gang.iterrows():
                            st.warning(f"- {row['username']}: {row['Staff Gang Signal']}")

                st.markdown("**Low Performers - Admins**")
                if low_admin.empty:
                    st.info("No admin performance rows in this scope.")
                else:
                    low_admin_view = low_admin[["username", "branch", "avg_score", "rating_count"]].head(10).copy()

                    def _admin_gang_signal(admin_name):
                        # Signal when many staff rate the admin very low, but admin's own conduct signals are not strongly bad.
                        if ratings_all.empty or "rated" not in ratings_all.columns or "score" not in ratings_all.columns:
                            return "No data"

                        incoming = ratings_all[ratings_all["rated"].astype(str) == str(admin_name)]
                        if incoming.empty:
                            return "No data"

                        incoming_low = incoming[pd.to_numeric(incoming["score"], errors="coerce") < 45]
                        total_raters = int(incoming["rater"].astype(str).nunique()) if "rater" in incoming.columns else int(len(incoming))
                        low_raters = int(incoming_low["rater"].astype(str).nunique()) if "rater" in incoming_low.columns else int(len(incoming_low))
                        low_ratio = (low_raters / max(total_raters, 1))

                        outgoing = ratings_all[ratings_all.get("rater", "").astype(str) == str(admin_name)] if "rater" in ratings_all.columns else pd.DataFrame()
                        outgoing_avg = float(pd.to_numeric(outgoing.get("score"), errors="coerce").mean()) if not outgoing.empty else 0.0

                        warn_count = 0
                        if not warnings_all.empty and "username" in warnings_all.columns:
                            warn_count = int((warnings_all["username"].astype(str) == str(admin_name)).sum())

                        late_rate = 0.0
                        if not attendance_all.empty and "username" in attendance_all.columns and "status" in attendance_all.columns:
                            admin_att = attendance_all[attendance_all["username"].astype(str) == str(admin_name)]
                            if not admin_att.empty:
                                late_rate = float((admin_att["status"].astype(str).str.upper() == "LATE").mean() * 100)

                        # Potential gang signal: many low raters, while admin is not showing strong negative outgoing/discipline indicators.
                        if total_raters >= 4 and low_raters >= 3 and low_ratio >= 0.60 and outgoing_avg >= 55 and warn_count == 0 and late_rate < 25:
                            return f"Possible staff gang ({low_raters}/{total_raters} low raters)"
                        if total_raters >= 4 and low_raters >= 3 and low_ratio >= 0.60:
                            return f"Heavy negative cluster ({low_raters}/{total_raters})"
                        return "No clear gang signal"

                    def _admin_low_reason(admin_name):
                        reasons = []

                        # Ratings reason
                        row = low_admin[low_admin["username"].astype(str) == str(admin_name)]
                        if not row.empty:
                            avg_score = float(row.iloc[0].get("avg_score", 0) or 0)
                            rating_count = int(row.iloc[0].get("rating_count", 0) or 0)
                            if avg_score < 52:
                                reasons.append(f"Very low peer rating ({avg_score:.1f})")
                            elif avg_score < 55:
                                reasons.append(f"Below-target peer rating ({avg_score:.1f})")
                            if rating_count < 5:
                                reasons.append("Few ratings captured (low confidence)")

                        # Attendance discipline reason
                        if not attendance_all.empty and "username" in attendance_all.columns and "status" in attendance_all.columns:
                            admin_att = attendance_all[attendance_all["username"].astype(str) == str(admin_name)]
                            if not admin_att.empty:
                                late_rate = float((admin_att["status"].astype(str).str.upper() == "LATE").mean() * 100)
                                absent_rate = float((admin_att["status"].astype(str).str.upper() == "ABSENT").mean() * 100)
                                if late_rate >= 25:
                                    reasons.append(f"Frequent lateness ({late_rate:.0f}% records)")
                                if absent_rate >= 10:
                                    reasons.append(f"Absenteeism risk ({absent_rate:.0f}% records)")

                        # Conduct reason from warning records
                        if not warnings_all.empty and "username" in warnings_all.columns:
                            warn_count = int((warnings_all["username"].astype(str) == str(admin_name)).sum())
                            if warn_count >= 1:
                                reasons.append(f"Warning history ({warn_count})")

                        if not reasons:
                            reasons.append("Low ranking from current performance mix")

                        return "; ".join(reasons[:3])

                    low_admin_view["Reason"] = low_admin_view["username"].apply(_admin_low_reason)
                    low_admin_view["Staff Gang Signal"] = low_admin_view["username"].apply(_admin_gang_signal)
                    st.dataframe(
                        low_admin_view[["username", "branch", "avg_score", "rating_count", "Reason", "Staff Gang Signal"]],
                        use_container_width=True,
                    )

                    suspected_gang = low_admin_view[
                        low_admin_view["Staff Gang Signal"].astype(str).str.contains("Possible staff gang|Heavy negative cluster", case=False, na=False)
                    ]
                    if not suspected_gang.empty:
                        st.warning("Potential staff-gang signals detected against the following admin(s):")
                        for _, row in suspected_gang.iterrows():
                            st.warning(f"- {row['username']}: {row['Staff Gang Signal']}")

                st.markdown("**Low Performers - All Roles**")
                st.dataframe(low_all[["username", "role", "branch", "avg_score", "rating_count"]].head(15), use_container_width=True)

                render_favorite_relationship_section(ratings_all, compare_scope_label)

                st.divider()
                st.markdown("### Organization Leadership Intelligence")
                if ANALYTICS_OK:
                    try:
                        leaders = detect_leaders(ratings_all, attendance_all, leaves_all, users_all, messages_all)
                        if isinstance(leaders, tuple):
                            leaders_df, leaders_insights = leaders
                            if leaders_insights:
                                for li in leaders_insights:
                                    st.info(li)
                            if safe_df(leaders_df):
                                st.dataframe(leaders_df, use_container_width=True)
                        elif safe_df(leaders):
                            st.dataframe(leaders, use_container_width=True)
                    except Exception as e:
                        st.warning(f"Leadership intelligence unavailable: {e}")

                    st.markdown("### Organization Recommendations")
                    try:
                        org_recs = management_recommendations(ratings_all, attendance_all, schedules_all)
                        if isinstance(org_recs, list):
                            if org_recs:
                                for rec in org_recs:
                                    st.success(rec)
                            else:
                                st.info("No urgent organization-wide decisions right now. Continue monitoring trends across branches.")
                        else:
                            st.success(str(org_recs))
                    except Exception as e:
                        st.warning(f"Organization recommendations unavailable: {e}")

    # =========================================================
    # GUEST EXPERIENCE (ANALYTICS)
    # =========================================================
    elif menu == "Analytics" and analytics_view == "Guest Experience":
        st.subheader("Guest Experience")

        feedback_df = safe_read(
            """
            SELECT id, organization, branch, feedback_scope, target_username,
                   stars, message, is_anonymous, client_name, created_at
            FROM client_feedback
            WHERE organization=?
            ORDER BY id DESC
            """,
            conn,
            params=(org,),
        )

        if feedback_df.empty:
            st.info("No guest feedback submitted yet.")
        else:
            feedback_df["stars"] = pd.to_numeric(feedback_df["stars"], errors="coerce").fillna(0)
            feedback_df["scope_label"] = feedback_df["feedback_scope"].astype(str).map({
                "individual": "Individual",
                "general": "General",
            }).fillna("General")
            feedback_df["target_label"] = feedback_df["target_username"].fillna("").astype(str)
            feedback_df.loc[feedback_df["target_label"].str.strip() == "", "target_label"] = "The whole team"
            feedback_df["guest_name"] = feedback_df.apply(
                lambda r: "Anonymous" if int(r.get("is_anonymous", 1)) == 1 else (str(r.get("client_name", "")).strip() or "Anonymous"),
                axis=1,
            )
            feedback_df["stars_display"] = feedback_df["stars"].apply(lambda s: "*" * int(s) if int(s) > 0 else "-")

            view_mode = nav_radio(
                "View Mode",
                ["Whole Organization", "By Branch"],
                key="feedback_view_mode",
                horizontal=True,
            )

            view_df = feedback_df.copy()
            if view_mode == "By Branch":
                branch_options = sorted([b for b in view_df["branch"].dropna().astype(str).unique().tolist() if b])
                if branch_options:
                    selected_branch = nav_selectbox("Branch", branch_options, key="feedback_branch_filter")
                    view_df = view_df[view_df["branch"].astype(str) == selected_branch]

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Feedback", len(view_df))
            avg_stars = view_df['stars'].mean()
            m2.metric("Average Stars", f"{avg_stars:.1f} {'*' * round(avg_stars)}" if not view_df.empty else "-")
            m3.metric("General (whole team)", int((view_df["feedback_scope"].astype(str) == "general").sum()))
            m4.metric("Individual Staff", int((view_df["feedback_scope"].astype(str) == "individual").sum()))

            # --- Individual Staff Messages ---
            individual_df = view_df[view_df["feedback_scope"].astype(str) == "individual"].copy()
            if not individual_df.empty:
                st.markdown("---")
                st.markdown("#### Individual Staff Feedback")
                for _, row in individual_df.iterrows():
                    staff = str(row.get("target_username", "")).strip() or "-"
                    msg = str(row.get("message", "")).strip()
                    guest = str(row.get("guest_name", "Anonymous"))
                    stars_str = row.get("stars_display", "-")
                    ts = str(row.get("created_at", ""))[:16]
                    branch_name = str(row.get("branch", ""))
                    header = f"**{stars_str} | For: {staff}** - _{branch_name}_ | {ts}"
                    with st.expander(header, expanded=False):
                        if msg:
                            st.write(f"**Message:** {clean_display_text(msg)}")
                        else:
                            st.caption("No written message.")
                        if guest != "Anonymous":
                            st.caption(f"From: {clean_display_text(guest)}")
                        else:
                            st.caption("Submitted anonymously")

            # --- Branch Summary ---
            st.markdown("---")
            st.markdown("#### Branch Summary")
            branch_summary = (
                view_df.groupby("branch", dropna=False)
                .agg(feedback_count=("id", "count"), avg_stars=("stars", "mean"))
                .reset_index()
                .sort_values("feedback_count", ascending=False)
            )
            branch_summary["avg_stars"] = branch_summary["avg_stars"].round(1)
            st.dataframe(branch_summary, use_container_width=True)

            # --- Staff summary ---
            if not individual_df.empty:
                st.markdown("#### Staff Rating Summary")
                individual_summary = (
                    individual_df.groupby("target_username", dropna=False)
                    .agg(feedback_count=("id", "count"), avg_stars=("stars", "mean"))
                    .reset_index()
                    .rename(columns={"target_username": "staff"})
                    .sort_values("avg_stars", ascending=False)
                )
                individual_summary["avg_stars"] = individual_summary["avg_stars"].round(1)
                st.dataframe(individual_summary, use_container_width=True)

            # --- All Entries Table + Export ---
            st.markdown("---")
            st.markdown("#### All Feedback Entries")
            export_df = view_df[["created_at", "branch", "scope_label", "target_label", "stars", "stars_display", "guest_name", "message"]].copy()
            export_df.columns = ["Date", "Branch", "Type", "For", "Stars", "Rating", "Guest", "Message"]

            st.dataframe(export_df[["Date", "Branch", "Type", "For", "Rating", "Guest", "Message"]], use_container_width=True)

            csv_data = export_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "â¬‡ï¸ Download as CSV",
                data=csv_data,
                file_name=f"guest_experience_{org}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    # =========================================================
    # ATTENDANCE
    # =========================================================
    elif menu == "Attendance":
        st.subheader("Attendance Dashboard")

        all_att = apply_branch_scope(safe_read("SELECT * FROM attendance WHERE organization=?", conn, params=(org,)))
        all_lateness = apply_branch_scope(safe_read(
            "SELECT username, approved_for_date, reason, approved_by, status, actual_reason, used_at FROM lateness_approvals WHERE organization=?",
            conn,
            params=(org,),
        ))

        att_ind, att_br, att_all, att_adm = st.tabs([
            "Individual", "Per Branch", "All Branches", "Admins"
        ])

        with att_ind:
            if branch_scope:
                all_users_df = safe_read(
                    "SELECT username FROM users WHERE organization=? AND branch=? ORDER BY username",
                    conn, params=(org, branch_scope)
                )
            else:
                all_users_df = safe_read(
                    "SELECT username FROM users WHERE organization=? ORDER BY username",
                    conn, params=(org,)
                )
            if all_users_df.empty:
                st.info("No users.")
            else:
                sel_emp = nav_selectbox("Select Employee", all_users_df["username"].tolist(), key="att_emp_sel")
                emp_att = safe_read(
                    "SELECT date, clock_in, clock_out, status FROM attendance WHERE organization=? AND username=? ORDER BY date DESC",
                    conn, params=(org, sel_emp)
                )
                if emp_att.empty:
                    st.info(f"No attendance records for {sel_emp}.")
                else:
                    emp_att["date"] = pd.to_datetime(emp_att["date"], errors="coerce")
                    ar = nav_selectbox("Range", ["All","Today","This Week","This Month"], key="ind_att_range")
                    if ar == "Today":
                        emp_att = emp_att[emp_att["date"].dt.date == date.today()]
                    elif ar == "This Week":
                        emp_att = emp_att[emp_att["date"] >= pd.Timestamp.now() - pd.Timedelta(days=6)]
                    elif ar == "This Month":
                        emp_att = emp_att[emp_att["date"] >= pd.Timestamp.now() - pd.Timedelta(days=29)]

                    emp_att = annotate_attendance_lateness(emp_att, all_lateness)

                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Records",    len(emp_att))
                    m2.metric("True Late",  int(emp_att["true_late"].sum()))
                    m3.metric("Approved Late", int(emp_att["approved_late"].sum()))
                    m4.metric("Full Days",  int(emp_att["clock_out"].astype(str).str.strip().ne("").sum()))
                    st.dataframe(
                        emp_att[[c for c in ["date", "clock_in", "clock_out", "late_status_label", "lateness_request_status", "lateness_reason", "lateness_admin_note", "lateness_approved_by"] if c in emp_att.columns]],
                        use_container_width=True,
                    )

        with att_br:
            if branches:
                att_branch_options = ["All Branches"] + branches
                att_branch_default_idx = att_branch_options.index(branch_scope) if branch_scope in att_branch_options else 0
                br_sel = nav_selectbox("Select Branch", att_branch_options, key="att_br_sel", index=att_branch_default_idx)
                if br_sel == "All Branches":
                    br_att = all_att.copy()
                else:
                    br_att = safe_read(
                        "SELECT username, date, clock_in, clock_out, status FROM attendance WHERE organization=? AND branch=? ORDER BY date DESC",
                        conn, params=(org, br_sel)
                    )
                if br_att.empty:
                    st.info("No attendance for this branch.")
                else:
                    br_att["date"] = pd.to_datetime(br_att["date"], errors="coerce")
                    br_range = nav_selectbox("Range", ["All","Today","This Week","This Month"], key="br_att_range")
                    if br_range == "Today":
                        br_att = br_att[br_att["date"].dt.date == date.today()]
                    elif br_range == "This Week":
                        br_att = br_att[br_att["date"] >= pd.Timestamp.now() - pd.Timedelta(days=6)]
                    elif br_range == "This Month":
                        br_att = br_att[br_att["date"] >= pd.Timestamp.now() - pd.Timedelta(days=29)]

                    br_att = annotate_attendance_lateness(br_att, all_lateness)

                    bm1, bm2, bm3, bm4, bm5 = st.columns(5)
                    bm1.metric("Records",    len(br_att))
                    bm2.metric("True Late",  int(br_att["true_late"].sum()))
                    bm3.metric("Approved Late", int(br_att["approved_late"].sum()))
                    bm4.metric("Present",    int(br_att["clock_in"].astype(str).str.strip().ne("").sum()))
                    bm5.metric("Full Clock", int(br_att["clock_out"].astype(str).str.strip().ne("").sum()))
                    st.dataframe(
                        br_att[[c for c in ["username", "date", "clock_in", "clock_out", "late_status_label", "lateness_request_status", "lateness_reason", "lateness_admin_note", "lateness_approved_by"] if c in br_att.columns]],
                        use_container_width=True,
                    )

                    st.markdown("**Latecomers**")
                    late_df = br_att[br_att["true_late"] == True]  # noqa: E712
                    if late_df.empty:
                        st.success("No latecomers in this range.")
                    else:
                        st.dataframe(late_df[[c for c in ["username", "date", "clock_in", "late_status_label"] if c in late_df.columns]], use_container_width=True)

                    st.markdown("**Approved Late Arrivals**")
                    approved_late_df = br_att[br_att["approved_late"] == True]  # noqa: E712
                    if approved_late_df.empty:
                        st.info("No approved late arrivals in this range.")
                    else:
                        st.dataframe(
                            approved_late_df[[c for c in ["username", "date", "clock_in", "late_status_label", "lateness_reason", "lateness_approved_by"] if c in approved_late_df.columns]],
                            use_container_width=True,
                        )

                    st.markdown("**Shift Summary**")
                    try:
                        br_att["clock_in_dt"]  = pd.to_datetime(br_att["clock_in"],  errors="coerce")
                        br_att["clock_out_dt"] = pd.to_datetime(br_att["clock_out"], errors="coerce")
                        br_att["hours"] = (
                            br_att["clock_out_dt"] - br_att["clock_in_dt"]
                        ).dt.total_seconds() / 3600
                        avg_hrs = br_att["hours"].mean()
                        st.metric("Avg Hours per Shift", f"{round(avg_hrs, 1)}h" if pd.notna(avg_hrs) else "N/A")
                    except Exception:
                        pass
            else:
                st.info("No branches.")

        with att_all:
            if all_att.empty:
                st.info("No attendance data yet.")
            else:
                all_att2 = all_att.copy()
                all_att2["date"] = pd.to_datetime(all_att2["date"], errors="coerce")
                ov_r = nav_selectbox("Range", ["All","Today","This Week","This Month"], key="ov_att_range")
                if ov_r == "Today":
                    all_att2 = all_att2[all_att2["date"].dt.date == date.today()]
                elif ov_r == "This Week":
                    all_att2 = all_att2[all_att2["date"] >= pd.Timestamp.now() - pd.Timedelta(days=6)]
                elif ov_r == "This Month":
                    all_att2 = all_att2[all_att2["date"] >= pd.Timestamp.now() - pd.Timedelta(days=29)]

                all_att2 = annotate_attendance_lateness(all_att2, all_lateness)

                am1, am2, am3, am4 = st.columns(4)
                am1.metric("Total Records",       len(all_att2))
                am2.metric("True Late",           int(all_att2["true_late"].sum()) if not all_att2.empty else 0)
                am3.metric("Approved Late",       int(all_att2["approved_late"].sum()) if not all_att2.empty else 0)
                am4.metric("Branches with Data",  all_att2["branch"].nunique() if not all_att2.empty else 0)

                if not all_att2.empty:
                    br_break = all_att2.groupby("branch").agg(
                        Records=("username","count"),
                        True_Late=("true_late", "sum"),
                        Approved_Late=("approved_late", "sum")
                    ).reset_index()
                    st.markdown("**Branch Breakdown**")
                    st.dataframe(br_break, use_container_width=True)
                    st.dataframe(
                        all_att2[[c for c in ["username","branch","date","clock_in","clock_out","late_status_label","lateness_request_status","lateness_reason","lateness_approved_by"] if c in all_att2.columns]].sort_values("date", ascending=False),
                        use_container_width=True
                    )

        with att_adm:
            st.markdown("**Admin / Manager Attendance**")
            adm_att = safe_read(
                """SELECT a.username, a.branch, a.date, a.clock_in, a.clock_out, a.status
                   FROM attendance a
                   JOIN users u ON a.username=u.username AND a.organization=u.organization
                   WHERE a.organization=? AND u.role IN ('admin','superadmin')
                   ORDER BY a.date DESC""",
                conn, params=(org,)
            )
            if adm_att.empty:
                st.info("No admin attendance records.")
            else:
                adm_att = apply_branch_scope(adm_att)
                adm_att["date"] = pd.to_datetime(adm_att["date"], errors="coerce")
                ar2 = nav_selectbox("Range", ["All","Today","This Week","This Month"], key="adm_att_range")
                if ar2 == "Today":
                    adm_att = adm_att[adm_att["date"].dt.date == date.today()]
                elif ar2 == "This Week":
                    adm_att = adm_att[adm_att["date"] >= pd.Timestamp.now() - pd.Timedelta(days=6)]
                elif ar2 == "This Month":
                    adm_att = adm_att[adm_att["date"] >= pd.Timestamp.now() - pd.Timedelta(days=29)]

                adm_att = annotate_attendance_lateness(adm_att, all_lateness)

                aa1, aa2, aa3 = st.columns(3)
                aa1.metric("Records", len(adm_att))
                aa2.metric("True Late", int(adm_att["true_late"].sum()) if not adm_att.empty else 0)
                aa3.metric("Approved Late", int(adm_att["approved_late"].sum()) if not adm_att.empty else 0)
                st.dataframe(
                    adm_att[[c for c in ["username", "branch", "date", "clock_in", "clock_out", "late_status_label", "lateness_request_status", "lateness_reason", "lateness_approved_by"] if c in adm_att.columns]],
                    use_container_width=True,
                )

    # =========================================================
    # KIOSK
    # =========================================================
    elif menu == "Staff Check In":
        st.subheader("Staff Check In Management")

        kiosks_df = safe_read("SELECT * FROM kiosks WHERE organization=?", conn, params=(org,))
        if safe_df(kiosks_df):
            st.dataframe(kiosks_df, use_container_width=True)
        else:
            st.info("No kiosks created yet.")

        st.divider()

        k_create, k_manage = st.tabs(["Create Kiosk", "Manage Kiosks"])

        with k_create:
            if branches:
                with st.form("create_kiosk_full", clear_on_submit=False):
                    kf_br   = st.selectbox("Branch", branches, key="kiosk_full_br")
                    kf_name = st.text_input("Device / Kiosk Label")
                    kf_sub  = st.form_submit_button("Create & Get Link")
                    if kf_sub:
                        dev_name = kf_name.strip() or f"{kf_br}-Kiosk"
                        dup = safe_read(
                            "SELECT id FROM kiosks WHERE branch=? AND organization=? AND device_name=?",
                            conn, params=(kf_br, org, dev_name)
                        )
                        if not dup.empty:
                            st.error("Kiosk name already exists for this branch.")
                        else:
                            try:
                                conn.execute(
                                    "INSERT INTO kiosks(branch,organization,device_name,last_active,status) VALUES(?,?,?,datetime('now'),'active')",
                                    (kf_br, org, dev_name)
                                )
                            except Exception:
                                conn.execute(
                                    "INSERT INTO kiosks(branch,organization,device_name,last_active) VALUES(?,?,?,datetime('now'))",
                                    (kf_br, org, dev_name)
                                )
                            conn.commit()
                            log_action(conn, user, "CREATE KIOSK", dev_name, org)
                            st.success(f"Kiosk '{dev_name}' created for branch '{kf_br}'.")
                            st.markdown("**Kiosk Access Link** - open on the kiosk device:")
                            st.code(kiosk_link(kf_br, org), language="text")
                            st.info("Bookmark this URL on the kiosk browser for daily use.")
            else:
                st.info("Create a branch first before adding a kiosk.")

        with k_manage:
            kiosks_m = safe_read("SELECT * FROM kiosks WHERE organization=?", conn, params=(org,))
            if kiosks_m.empty:
                st.info("No kiosks to manage.")
            else:
                for _, k in kiosks_m.iterrows():
                    kid_raw  = k.get("id")
                    kid      = int(kid_raw) if pd.notna(kid_raw) else None
                    kname    = str(k.get("device_name", "Unknown"))
                    kbr      = str(k.get("branch", ""))
                    kstatus  = str(k.get("status", "active"))

                    with st.expander(f"{kname} - {kbr} [{kstatus}]"):
                        st.code(kiosk_link(kbr, org), language="text")

                        if kid is not None:
                            ek1, ek2, ek3 = st.columns(3)

                            if ek1.button("Lock",   key=f"k_lock_{kid}"):
                                try:
                                    conn.execute("UPDATE kiosks SET status='locked' WHERE id=?", (kid,))
                                    conn.commit()
                                    st.success(f"'{kname}' locked.")
                                    st.rerun()
                                except Exception:
                                    st.warning("status column not in kiosks schema.")

                            if ek2.button("Unlock", key=f"k_unlock_{kid}"):
                                try:
                                    conn.execute("UPDATE kiosks SET status='active' WHERE id=?", (kid,))
                                    conn.commit()
                                    st.success(f"'{kname}' unlocked.")
                                    st.rerun()
                                except Exception:
                                    st.warning("status column not in kiosks schema.")

                            if ek3.button("Delete", key=f"k_del_{kid}"):
                                conn.execute("DELETE FROM kiosks WHERE id=?", (kid,))
                                conn.commit()
                                log_action(conn, user, "DELETE KIOSK", kname, org)
                                st.success(f"Kiosk '{kname}' deleted.")
                                st.rerun()

                            with st.form(f"edit_kiosk_{kid}", clear_on_submit=False):
                                new_kname = st.text_input("Rename Kiosk (blank = keep current)", key=f"kn_{kid}")
                                new_kbr   = st.selectbox("Reassign Branch", branches if branches else [kbr], key=f"kb_{kid}")
                                if st.form_submit_button("Save Changes"):
                                    final_kname = new_kname.strip() or kname
                                    conn.execute(
                                        "UPDATE kiosks SET device_name=?, branch=? WHERE id=?",
                                        (final_kname, new_kbr, kid)
                                    )
                                    conn.commit()
                                    st.success(f"Kiosk updated: '{final_kname}' in '{new_kbr}'.")
                                    st.rerun()

    # =========================================================
    # SETTINGS
    # =========================================================
    elif menu == "Settings":
        st.subheader("Settings")

        feedback_settings = safe_read(
            "SELECT enabled, allow_named FROM client_feedback_settings WHERE organization=? LIMIT 1",
            conn,
            params=(org,),
        )
        feedback_enabled = bool(int(feedback_settings.iloc[0].get("enabled", 0))) if not feedback_settings.empty else False
        allow_named_feedback = bool(int(feedback_settings.iloc[0].get("allow_named", 1))) if not feedback_settings.empty else True

        st.markdown("### Guest Experience Controls")
        with st.form("client_feedback_control_form", clear_on_submit=False):
            kiosk_feedback_on = st.toggle("Enable Guest Experience on Staff Check In page", value=feedback_enabled)
            named_feedback_on = st.toggle("Allow guests to submit their name", value=allow_named_feedback)
            if st.form_submit_button("Save Guest Experience Controls"):
                conn.execute(
                    """
                    INSERT INTO client_feedback_settings(organization, enabled, allow_named, updated_at)
                    VALUES(?,?,?,datetime('now'))
                    ON CONFLICT(organization) DO UPDATE SET
                        enabled=excluded.enabled,
                        allow_named=excluded.allow_named,
                        updated_at=datetime('now')
                    """,
                    (org, int(kiosk_feedback_on), int(named_feedback_on)),
                )
                conn.commit()
                log_action(conn, user, "UPDATE GUEST EXPERIENCE SETTINGS", "SYSTEM", org)
                st.success("Guest Experience controls updated.")

        settings = safe_read("SELECT * FROM settings WHERE id=1", conn)
        if settings.empty:
            st.error("Settings row not found. Run db.create_tables() to initialize.")
        else:
            s = settings.iloc[0]

            st.markdown("### Branch Working Hours")
            st.caption("Kiosk uses this order: holiday rule, branch day-hours, personal staff schedule, then global default.")
            if branches:
                selected_branch_hours = st.selectbox("Branch", branches, key="settings_branch_hours_branch")
                branch_hours_df = safe_read(
                    "SELECT day_name, work_start, work_end, off_day FROM branch_working_hours WHERE organization=? AND branch=?",
                    conn,
                    params=(org, selected_branch_hours),
                )
                hours_lookup = {
                    str(row.get("day_name", "")): row
                    for _, row in branch_hours_df.iterrows()
                }
                day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

                with st.form("branch_working_hours_form", clear_on_submit=False):
                    for day_name in day_names:
                        existing_row = hours_lookup.get(day_name)
                        default_start = str(existing_row.get("work_start", s.get("work_start", "09:00"))) if existing_row is not None else str(s.get("work_start", "09:00"))
                        default_end = str(existing_row.get("work_end", s.get("work_end", "18:00"))) if existing_row is not None else str(s.get("work_end", "18:00"))
                        default_off = bool(int(existing_row.get("off_day", 0))) if existing_row is not None else False

                        d1, d2, d3 = st.columns([1.2, 1, 1])
                        with d1:
                            st.checkbox(f"{day_name} Closed", value=default_off, key=f"branch_hours_off_{day_name}")
                        with d2:
                            st.text_input(f"{day_name} Start", value=default_start, key=f"branch_hours_start_{day_name}")
                        with d3:
                            st.text_input(f"{day_name} End", value=default_end, key=f"branch_hours_end_{day_name}")

                    if st.form_submit_button("Save Branch Working Hours"):
                        conn.execute(
                            "DELETE FROM branch_working_hours WHERE organization=? AND branch=?",
                            (org, selected_branch_hours),
                        )
                        rows_to_save = []
                        for day_name in day_names:
                            rows_to_save.append((
                                org,
                                selected_branch_hours,
                                day_name,
                                str(st.session_state.get(f"branch_hours_start_{day_name}", s.get("work_start", "09:00"))).strip() or str(s.get("work_start", "09:00")),
                                str(st.session_state.get(f"branch_hours_end_{day_name}", s.get("work_end", "18:00"))).strip() or str(s.get("work_end", "18:00")),
                                1 if st.session_state.get(f"branch_hours_off_{day_name}", False) else 0,
                            ))
                        conn.executemany(
                            """
                            INSERT INTO branch_working_hours(
                                organization, branch, day_name, work_start, work_end, off_day, updated_at
                            )
                            VALUES(?,?,?,?,?,?,datetime('now'))
                            """,
                            rows_to_save,
                        )
                        conn.commit()
                        log_action(conn, user, "UPDATE BRANCH WORKING HOURS", selected_branch_hours, org)
                        st.success("Branch working hours saved.")
            else:
                st.info("Create branches first to set branch working hours.")

            st.markdown("### Holidays And Special Days")
            st.caption("Use 'All Branches' for company-wide closures, or target one branch for different holiday hours.")
            holiday_profile = safe_read(
                "SELECT country_code, subdivision, auto_detect_holidays FROM organization_holiday_profiles WHERE organization=? LIMIT 1",
                conn,
                params=(org,),
            )
            holiday_country = str(holiday_profile.iloc[0].get("country_code", "KE")) if not holiday_profile.empty else "KE"
            holiday_subdivision = str(holiday_profile.iloc[0].get("subdivision", "")) if not holiday_profile.empty else ""
            auto_detect_holidays = bool(int(holiday_profile.iloc[0].get("auto_detect_holidays", 0))) if not holiday_profile.empty else False

            st.markdown("#### Official Holiday Detection")
            if HOLIDAYS_OK:
                with st.form("holiday_profile_form", clear_on_submit=False):
                    holiday_country_val = st.text_input(
                        "Country Code",
                        value=holiday_country,
                        help="Examples: KE for Kenya, UG for Uganda, TZ for Tanzania, RW for Rwanda.",
                    )
                    holiday_subdivision_val = st.text_input(
                        "Region / Subdivision (optional)",
                        value=holiday_subdivision,
                        help="Use only if the selected country supports subdivisions in the holiday library.",
                    )
                    auto_detect_holidays_val = st.toggle(
                        "Automatically detect official holidays",
                        value=auto_detect_holidays,
                    )
                    if st.form_submit_button("Save Holiday Detection Settings"):
                        conn.execute(
                            """
                            INSERT INTO organization_holiday_profiles(
                                organization, country_code, subdivision, auto_detect_holidays, updated_at
                            )
                            VALUES(?,?,?,?,datetime('now'))
                            ON CONFLICT(organization) DO UPDATE SET
                                country_code=excluded.country_code,
                                subdivision=excluded.subdivision,
                                auto_detect_holidays=excluded.auto_detect_holidays,
                                updated_at=datetime('now')
                            """,
                            (
                                org,
                                holiday_country_val.strip().upper() or "KE",
                                holiday_subdivision_val.strip(),
                                int(auto_detect_holidays_val),
                            ),
                        )
                        conn.commit()
                        log_action(conn, user, "UPDATE HOLIDAY PROFILE", holiday_country_val.strip().upper() or "KE", org)
                        st.success("Holiday detection settings saved.")

                preview_year = st.number_input("Preview Official Holidays For Year", min_value=2024, max_value=2100, value=date.today().year, step=1)
                preview_df = get_holiday_preview(holiday_country, holiday_subdivision, int(preview_year))
                if preview_df.empty:
                    st.caption("No automatic holiday preview available for the current country/subdivision combination.")
                else:
                    st.dataframe(preview_df, use_container_width=True)
                    st.caption("Manual holiday rules above still override automatically detected official holidays.")
            else:
                st.warning("Official holiday detection package is not installed yet. Add the new requirements and restart the app to enable this feature.")

            holiday_scope_options = ["All Branches"] + branches if branches else ["All Branches"]
            with st.form("holiday_settings_form", clear_on_submit=False):
                holiday_scope = st.selectbox("Holiday Scope", holiday_scope_options, key="holiday_scope")
                holiday_dt = st.date_input("Date", value=date.today(), key="holiday_date")
                holiday_name = st.text_input("Holiday Or Event Name", key="holiday_name")
                holiday_closed = st.toggle("Closed For The Day", value=True, key="holiday_closed")
                holiday_start = st.text_input("Working Start (if open)", value=str(s.get("work_start", "09:00")), key="holiday_start")
                holiday_end = st.text_input("Working End (if open)", value=str(s.get("work_end", "18:00")), key="holiday_end")
                if st.form_submit_button("Save Holiday Rule"):
                    holiday_branch = "" if holiday_scope == "All Branches" else holiday_scope
                    conn.execute(
                        """
                        INSERT INTO organization_holidays(
                            organization, branch, holiday_date, holiday_name, is_closed, work_start, work_end, updated_at
                        )
                        VALUES(?,?,?,?,?,?,?,datetime('now'))
                        ON CONFLICT(organization, branch, holiday_date) DO UPDATE SET
                            holiday_name=excluded.holiday_name,
                            is_closed=excluded.is_closed,
                            work_start=excluded.work_start,
                            work_end=excluded.work_end,
                            updated_at=datetime('now')
                        """,
                        (
                            org,
                            holiday_branch,
                            holiday_dt.strftime("%Y-%m-%d"),
                            holiday_name.strip(),
                            int(holiday_closed),
                            holiday_start.strip() or str(s.get("work_start", "09:00")),
                            holiday_end.strip() or str(s.get("work_end", "18:00")),
                        ),
                    )
                    conn.commit()
                    log_action(conn, user, "SAVE HOLIDAY RULE", holiday_scope, org)
                    st.success("Holiday rule saved.")

            holiday_rules_df = safe_read(
                "SELECT id, branch, holiday_date, holiday_name, is_closed, work_start, work_end FROM organization_holidays WHERE organization=? ORDER BY holiday_date DESC, branch ASC",
                conn,
                params=(org,),
            )
            if not holiday_rules_df.empty:
                holiday_rules_view = holiday_rules_df.copy()
                holiday_rules_view["branch"] = holiday_rules_view["branch"].fillna("")
                holiday_rules_view.loc[holiday_rules_view["branch"].astype(str).str.strip() == "", "branch"] = "All Branches"
                holiday_rules_view["status"] = holiday_rules_view["is_closed"].apply(lambda x: "Closed" if int(x) == 1 else "Open With Special Hours")
                st.dataframe(
                    holiday_rules_view[["holiday_date", "branch", "holiday_name", "status", "work_start", "work_end"]],
                    use_container_width=True,
                )

                holiday_choices = [
                    f"{row['holiday_date']} | {('All Branches' if str(row['branch']).strip() == '' else row['branch'])} | {row['holiday_name'] or 'Holiday'}"
                    for _, row in holiday_rules_df.iterrows()
                ]
                selected_holiday_label = st.selectbox("Delete Holiday Rule", holiday_choices, key="delete_holiday_rule")
                selected_holiday_idx = holiday_choices.index(selected_holiday_label)
                selected_holiday_id = int(holiday_rules_df.iloc[selected_holiday_idx]["id"])
                if st.button("Delete Selected Holiday Rule", key="delete_holiday_rule_btn"):
                    conn.execute("DELETE FROM organization_holidays WHERE id=?", (selected_holiday_id,))
                    conn.commit()
                    log_action(conn, user, "DELETE HOLIDAY RULE", str(selected_holiday_id), org)
                    st.success("Holiday rule deleted.")

            with st.form("settings_form", clear_on_submit=False):
                rating_on = st.toggle("Enable Ratings",   value=bool(s["rating_open"]))
                anon_on   = st.toggle("Anonymous Rating", value=bool(s["anonymous_rating"]))
                ws_val    = st.text_input("Work Start Time (HH:MM)", value=str(s.get("work_start","09:00")))
                we_val    = st.text_input("Work End Time (HH:MM)",   value=str(s.get("work_end","18:00")))
                lm_val    = st.number_input("Late Grace (minutes)", min_value=0, value=int(s.get("late_minutes", 15)))
                if st.form_submit_button("Save Settings"):
                    conn.execute(
                        "UPDATE settings SET rating_open=?, anonymous_rating=?, work_start=?, work_end=?, late_minutes=? WHERE id=1",
                        (int(rating_on), int(anon_on), ws_val, we_val, int(lm_val))
                    )
                    conn.commit()
                    log_action(conn, user, "UPDATE SETTINGS", "SYSTEM", org)
                    st.success("Settings saved.")

        st.divider()
        with st.form("change_my_pw", clear_on_submit=False):
            new_pass  = st.text_input("New Password",     type="password")
            conf_pass = st.text_input("Confirm Password", type="password")
            if st.form_submit_button("Update My Password"):
                if not valid_pass(new_pass):
                    st.error("Password must be at least 4 characters.")
                elif new_pass != conf_pass:
                    st.error("Passwords do not match.")
                else:
                    conn.execute("UPDATE users SET password=? WHERE username=?", (hash_password(new_pass), user))
                    conn.commit()
                    st.success("Password updated.")

    # =========================================================
    # LOGS
    # =========================================================
    elif menu == "Logs":
        st.subheader("Audit Logs")

        log_from = nav_date_input("From", value=date.today() - timedelta(days=30), key="log_from")
        log_to = nav_date_input("To", value=date.today(), key="log_to")

        logs = safe_read(
            "SELECT * FROM audit_logs WHERE organization=? ORDER BY created_at DESC",
            conn, params=(org,)
        )
        logs = apply_branch_scope(logs)

        if logs.empty:
            st.info("No logs found.")
        else:
            logs["created_at"] = pd.to_datetime(logs["created_at"], errors="coerce")
            filtered = logs[
                (logs["created_at"].dt.date >= log_from) &
                (logs["created_at"].dt.date <= log_to)
            ]
            st.markdown(f"**{len(filtered)} log(s)** in selected date range.")
            st.dataframe(filtered, use_container_width=True)
