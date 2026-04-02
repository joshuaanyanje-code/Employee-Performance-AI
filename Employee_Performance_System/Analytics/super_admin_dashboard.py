import pandas as pd
from datetime import datetime, timedelta
import json

try:
    from ..database.db import get_connection
except ImportError:
    from database.db import get_connection

try:
    from .decision_engine import generate_super_admin_intelligence
    from .group_demographics import analyze_group_demographics
except ImportError:
    from Analytics.decision_engine import generate_super_admin_intelligence
    from Analytics.group_demographics import analyze_group_demographics


def _safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _calculate_payment_trend(payments_df):
    if payments_df is None or payments_df.empty or "created_at" not in payments_df.columns:
        return {
            "total_collected": 0.0,
            "last_30_days": 0.0,
            "previous_30_days": 0.0,
            "trend_direction": "Stable",
            "trend_delta": 0.0,
            "payment_count": 0,
        }

    payments_df = payments_df.copy()
    payments_df["created_at"] = pd.to_datetime(payments_df["created_at"], errors="coerce")
    payments_df["amount"] = pd.to_numeric(payments_df.get("amount"), errors="coerce").fillna(0)

    now = datetime.now()
    last_30_cutoff = now - timedelta(days=30)
    previous_30_cutoff = now - timedelta(days=60)

    last_30 = payments_df[payments_df["created_at"] >= last_30_cutoff]
    previous_30 = payments_df[
        (payments_df["created_at"] >= previous_30_cutoff) &
        (payments_df["created_at"] < last_30_cutoff)
    ]

    last_30_total = _safe_float(last_30["amount"].sum())
    previous_30_total = _safe_float(previous_30["amount"].sum())
    trend_delta = last_30_total - previous_30_total

    if trend_delta > 0:
        trend_direction = "Growing"
    elif trend_delta < 0:
        trend_direction = "Declining"
    else:
        trend_direction = "Stable"

    return {
        "total_collected": _safe_float(payments_df["amount"].sum()),
        "last_30_days": last_30_total,
        "previous_30_days": previous_30_total,
        "trend_direction": trend_direction,
        "trend_delta": trend_delta,
        "payment_count": int(len(payments_df)),
    }


def _build_branch_business_snapshot(ratings_df, attendance_df, users_df, branches_df):
    branch_rows = []

    branch_names = []
    if branches_df is not None and not branches_df.empty and "name" in branches_df.columns:
        branch_names.extend(branches_df["name"].dropna().astype(str).tolist())
    if users_df is not None and not users_df.empty and "branch" in users_df.columns:
        branch_names.extend(users_df["branch"].dropna().astype(str).tolist())
    if ratings_df is not None and not ratings_df.empty and "branch" in ratings_df.columns:
        branch_names.extend(ratings_df["branch"].dropna().astype(str).tolist())

    for branch_name in sorted(set([b for b in branch_names if str(b).strip()])):
        ratings_branch = ratings_df[ratings_df["branch"] == branch_name] if ratings_df is not None and not ratings_df.empty else pd.DataFrame()
        attendance_branch = attendance_df[attendance_df["branch"] == branch_name] if attendance_df is not None and not attendance_df.empty else pd.DataFrame()
        users_branch = users_df[users_df["branch"] == branch_name] if users_df is not None and not users_df.empty else pd.DataFrame()

        avg_score = _safe_float(ratings_branch["score"].mean()) if not ratings_branch.empty else 0.0
        team_size = int(len(users_branch)) if not users_branch.empty else 0
        admin_count = int((users_branch["role"] == "admin").sum()) if not users_branch.empty and "role" in users_branch.columns else 0
        active_user_count = int((users_branch["status"] == "active").sum()) if not users_branch.empty and "status" in users_branch.columns else team_size
        attendance_records = int(len(attendance_branch)) if not attendance_branch.empty else 0
        coverage = round((attendance_records / max(team_size, 1)), 2) if team_size else 0.0

        branch_rows.append({
            "branch": branch_name,
            "avg_score": round(avg_score, 2),
            "team_size": team_size,
            "active_users": active_user_count,
            "admin_count": admin_count,
            "attendance_records": attendance_records,
            "attendance_coverage": coverage,
            "ratings_count": int(len(ratings_branch)) if not ratings_branch.empty else 0,
        })

    if not branch_rows:
        return {
            "ranking": [],
            "best_branch": None,
            "attention_branch": None,
        }

    branch_df = pd.DataFrame(branch_rows)
    branch_df["business_score"] = (
        branch_df["avg_score"].fillna(0) * 0.55 +
        branch_df["attendance_coverage"].fillna(0) * 10 * 0.25 +
        branch_df["active_users"].fillna(0) * 0.20
    )
    branch_df = branch_df.sort_values(["business_score", "avg_score"], ascending=False)

    return {
        "ranking": branch_df.to_dict("records"),
        "best_branch": branch_df.iloc[0].to_dict() if not branch_df.empty else None,
        "attention_branch": branch_df.sort_values(["business_score", "avg_score"], ascending=True).iloc[0].to_dict() if not branch_df.empty else None,
    }


def _build_business_intelligence(ratings_df, attendance_df, users_df, payments_df, branches_df, dashboard):
    payment_trend = _calculate_payment_trend(payments_df)
    branch_snapshot = _build_branch_business_snapshot(ratings_df, attendance_df, users_df, branches_df)

    active_branches = 0
    if branches_df is not None and not branches_df.empty:
        if "status" in branches_df.columns:
            active_branches = int((branches_df["status"].fillna("active") == "active").sum())
        else:
            active_branches = int(len(branches_df))

    total_employees = int(len(users_df)) if users_df is not None and not users_df.empty else 0
    avg_score = _safe_float(ratings_df["score"].mean()) if ratings_df is not None and not ratings_df.empty else 0.0
    attendance_coverage = 0.0
    if attendance_df is not None and not attendance_df.empty and total_employees:
        attendance_coverage = round(len(attendance_df) / total_employees, 2)

    business_health_score = min(
        100.0,
        round(
            avg_score * 0.5 +
            (attendance_coverage * 10) * 0.2 +
            min(active_branches * 8, 20) +
            (10 if payment_trend["trend_direction"] == "Growing" else 5 if payment_trend["trend_direction"] == "Stable" else 0),
            2,
        ),
    )

    if business_health_score >= 75:
        business_health_status = "Strong"
    elif business_health_score >= 55:
        business_health_status = "Stable"
    else:
        business_health_status = "Needs Attention"

    growth_opportunities = []
    operational_risks = []
    priorities = []

    best_branch = branch_snapshot.get("best_branch")
    attention_branch = branch_snapshot.get("attention_branch")

    if best_branch and best_branch.get("avg_score", 0) >= 75:
        growth_opportunities.append(
            f"Scale what works in {best_branch['branch']}: it leads on internal performance with a score of {best_branch['avg_score']:.1f}."
        )

    if payment_trend["trend_direction"] == "Growing":
        growth_opportunities.append(
            f"Cash movement is improving: last 30 days collected {payment_trend['last_30_days']:.2f} versus {payment_trend['previous_30_days']:.2f} in the previous period."
        )
    elif payment_trend["trend_direction"] == "Declining":
        operational_risks.append(
            f"Collections are down by {abs(payment_trend['trend_delta']):.2f} compared with the previous 30-day period."
        )

    if attention_branch and attention_branch.get("avg_score", 0) < 60:
        operational_risks.append(
            f"{attention_branch['branch']} is the weakest branch snapshot and should be reviewed for leadership, staffing, or process issues."
        )

    if branch_snapshot.get("ranking"):
        branches_without_admin = [
            row["branch"] for row in branch_snapshot["ranking"] if row.get("admin_count", 0) == 0
        ]
        if branches_without_admin:
            operational_risks.append(
                "Branches without an assigned admin: " + ", ".join(branches_without_admin[:5])
            )

        low_coverage = [
            row["branch"] for row in branch_snapshot["ranking"] if row.get("attendance_coverage", 0) < 3
        ]
        if low_coverage:
            priorities.append(
                "Tighten operational discipline in: " + ", ".join(low_coverage[:5])
            )

    if avg_score >= 75:
        priorities.append("Protect quality momentum and convert top-performing practices into repeatable operating standards.")
    else:
        priorities.append("Raise operational quality before expansion by improving branch consistency, attendance discipline, and manager oversight.")

    if not growth_opportunities:
        growth_opportunities.append("Build growth by improving consistency first, then copy the highest-performing branch model across other locations.")

    return {
        "business_health_score": business_health_score,
        "business_health_status": business_health_status,
        "active_branches": active_branches,
        "total_employees": total_employees,
        "average_internal_score": round(avg_score, 2),
        "attendance_coverage": attendance_coverage,
        "payment_trend": payment_trend,
        "branch_snapshot": branch_snapshot,
        "growth_opportunities": growth_opportunities[:5],
        "operational_risks": operational_risks[:5],
        "priorities": priorities[:5],
        "recommended_focus": dashboard.get("recommendations", [])[:5],
    }


def _forecast_next_value(values):
    clean_values = [float(v) for v in values if pd.notna(v)]
    if not clean_values:
        return 0.0
    if len(clean_values) == 1:
        return round(clean_values[0], 2)

    deltas = [clean_values[index] - clean_values[index - 1] for index in range(1, len(clean_values))]
    avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
    return round(max(clean_values[-1] + avg_delta, 0.0), 2)


def _build_monthly_business_trends(ratings_df, payments_df, attendance_df):
    trend_df = pd.DataFrame()

    if ratings_df is not None and not ratings_df.empty and "created_at" in ratings_df.columns:
        ratings_work = ratings_df.copy()
        ratings_work["created_at"] = pd.to_datetime(ratings_work["created_at"], errors="coerce")
        ratings_work = ratings_work.dropna(subset=["created_at"])
        if not ratings_work.empty:
            ratings_work["month"] = ratings_work["created_at"].dt.to_period("M").astype(str)
            rating_monthly = ratings_work.groupby("month", as_index=False)["score"].mean()
            rating_monthly.rename(columns={"score": "avg_internal_score"}, inplace=True)
            trend_df = rating_monthly if trend_df.empty else trend_df.merge(rating_monthly, on="month", how="outer")

    if payments_df is not None and not payments_df.empty and "created_at" in payments_df.columns:
        payments_work = payments_df.copy()
        payments_work["created_at"] = pd.to_datetime(payments_work["created_at"], errors="coerce")
        payments_work["amount"] = pd.to_numeric(payments_work["amount"], errors="coerce").fillna(0)
        payments_work = payments_work.dropna(subset=["created_at"])
        if not payments_work.empty:
            payments_work["month"] = payments_work["created_at"].dt.to_period("M").astype(str)
            payment_monthly = payments_work.groupby("month", as_index=False)["amount"].sum()
            payment_monthly.rename(columns={"amount": "collections"}, inplace=True)
            trend_df = payment_monthly if trend_df.empty else trend_df.merge(payment_monthly, on="month", how="outer")

    if attendance_df is not None and not attendance_df.empty:
        attendance_work = attendance_df.copy()
        date_col = "date" if "date" in attendance_work.columns else "clock_in"
        if date_col in attendance_work.columns:
            attendance_work[date_col] = pd.to_datetime(attendance_work[date_col], errors="coerce")
            attendance_work = attendance_work.dropna(subset=[date_col])
            if not attendance_work.empty:
                attendance_work["month"] = attendance_work[date_col].dt.to_period("M").astype(str)
                attendance_monthly = attendance_work.groupby("month", as_index=False).size()
                attendance_monthly.rename(columns={"size": "attendance_records"}, inplace=True)
                trend_df = attendance_monthly if trend_df.empty else trend_df.merge(attendance_monthly, on="month", how="outer")

    if trend_df.empty:
        return {
            "monthly_rows": [],
            "forecast": {},
            "trend_summary": [],
        }

    trend_df = trend_df.fillna(0).sort_values("month").tail(6)
    next_month = (pd.Period(trend_df.iloc[-1]["month"], freq="M") + 1).strftime("%Y-%m")

    forecast = {
        "next_month": next_month,
        "collections": _forecast_next_value(trend_df.get("collections", pd.Series(dtype=float)).tolist()),
        "avg_internal_score": _forecast_next_value(trend_df.get("avg_internal_score", pd.Series(dtype=float)).tolist()),
        "attendance_records": _forecast_next_value(trend_df.get("attendance_records", pd.Series(dtype=float)).tolist()),
    }

    trend_summary = []
    if "collections" in trend_df.columns and len(trend_df) >= 2:
        latest = float(trend_df.iloc[-1]["collections"])
        previous = float(trend_df.iloc[-2]["collections"])
        if latest > previous:
            trend_summary.append("Monthly collections are improving.")
        elif latest < previous:
            trend_summary.append("Monthly collections dipped in the latest period.")

    if "avg_internal_score" in trend_df.columns and len(trend_df) >= 2:
        latest = float(trend_df.iloc[-1]["avg_internal_score"])
        previous = float(trend_df.iloc[-2]["avg_internal_score"])
        if latest > previous:
            trend_summary.append("Internal performance trend is moving upward.")
        elif latest < previous:
            trend_summary.append("Internal performance trend weakened in the latest period.")

    if "attendance_records" in trend_df.columns and len(trend_df) >= 2:
        latest = float(trend_df.iloc[-1]["attendance_records"])
        previous = float(trend_df.iloc[-2]["attendance_records"])
        if latest < previous:
            trend_summary.append("Attendance activity reduced in the latest month.")

    return {
        "monthly_rows": trend_df.to_dict("records"),
        "forecast": forecast,
        "trend_summary": trend_summary[:5],
    }


def _build_branch_action_plan(branch_snapshot):
    weakest_branch = branch_snapshot.get("attention_branch") if isinstance(branch_snapshot, dict) else None
    if not weakest_branch:
        return {
            "target_branch": None,
            "urgency": "Low",
            "actions": [],
            "expected_outcomes": [],
        }

    actions = []
    outcomes = []
    avg_score = float(weakest_branch.get("avg_score", 0) or 0)
    coverage = float(weakest_branch.get("attendance_coverage", 0) or 0)
    admin_count = int(weakest_branch.get("admin_count", 0) or 0)
    ratings_count = int(weakest_branch.get("ratings_count", 0) or 0)

    if admin_count == 0:
        actions.append("Assign a responsible branch leader and enforce weekly operational review.")
        outcomes.append("Improved accountability and faster issue escalation.")

    if avg_score < 60:
        actions.append("Run a 30-day performance recovery plan focused on quality, responsiveness, and team discipline.")
        outcomes.append("Lift internal performance score and reduce risk concentration.")
    elif avg_score < 75:
        actions.append("Coach the branch manager on consistency and replicate practices from the top-ranked branch.")
        outcomes.append("Close the performance gap with stronger branches.")

    if coverage < 3:
        actions.append("Tighten attendance capture and daily operating routines to improve visibility and compliance.")
        outcomes.append("Cleaner operational data and earlier detection of staffing problems.")

    if ratings_count < 10:
        actions.append("Increase branch feedback participation so leadership decisions are based on broader evidence.")
        outcomes.append("More reliable branch diagnosis and better intervention quality.")

    if not actions:
        actions.append("Maintain weekly monitoring and address isolated weak signals before they spread.")
        outcomes.append("Protect current branch stability and prevent regression.")

    urgency = "High" if avg_score < 60 or admin_count == 0 else "Medium" if coverage < 3 else "Low"

    return {
        "target_branch": weakest_branch.get("branch"),
        "urgency": urgency,
        "actions": actions[:5],
        "expected_outcomes": outcomes[:5],
        "branch_snapshot": weakest_branch,
    }


# =====================================================
# SUPER ADMIN DASHBOARD - INTELLIGENT VIEW
# =====================================================
def get_super_admin_dashboard(organization, branch=None, super_admin_user=None):
    """
    Generates comprehensive super admin dashboard with intelligence, alerts, metrics.
    Filters out super_admin, master_admin from all analytics.
    """
    
    dashboard = {
        "generated_at": datetime.now().isoformat(),
        "organization": organization,
        "branch": branch,
        "executive_summary": {},
        "critical_alerts": [],
        "key_metrics": {},
        "team_insights": {},
        "group_analysis": {},
        "recommendations": [],
        "individual_focus": {},
        "performance_trends": {},
        "business_intelligence": {},
        "monthly_trends": {},
        "branch_action_plan": {},
    }
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # =========================
    # GET RATINGS (EXCLUDE super_admin, master_admin)
    # =========================
    exclude_roles = ("super_admin", "master")
    
    if branch:
        cursor.execute("""
        SELECT r.* FROM ratings r
        WHERE r.organization = ?
        AND r.branch = ?
        AND r.rater NOT IN (
            SELECT username FROM users WHERE role IN (?, ?)
        )
        AND r.rated NOT IN (
            SELECT username FROM users WHERE role IN (?, ?)
        )
        """, (organization, branch, "super_admin", "master", "super_admin", "master"))
    else:
        cursor.execute("""
        SELECT r.* FROM ratings r
        WHERE r.organization = ?
        AND r.rater NOT IN (
            SELECT username FROM users WHERE role IN (?, ?)
        )
        AND r.rated NOT IN (
            SELECT username FROM users WHERE role IN (?, ?)
        )
        """, (organization, "super_admin", "master", "super_admin", "master"))
    
    ratings_data = cursor.fetchall()
    
    if not ratings_data:
        conn.close()
        return dashboard
    
    ratings_cols = ["id", "rater", "rated", "topic", "score", "branch", "organization", "created_at"]
    ratings_df = pd.DataFrame(ratings_data, columns=ratings_cols)
    
    # =========================
    # GET ATTENDANCE
    # =========================
    cursor.execute("""
    SELECT a.* FROM attendance a
    WHERE a.organization = ?
    AND a.username NOT IN (
        SELECT username FROM users WHERE role IN (?, ?)
    )
    """, (organization, "super_admin", "master"))
    
    attendance_data = cursor.fetchall()
    attendance_cols = ["id", "username", "branch", "organization", "clock_in", "clock_out", "status", "date", "image"]
    attendance_df = pd.DataFrame(attendance_data, columns=attendance_cols) if attendance_data else pd.DataFrame()
    
    # =========================
    # GET USERS (EXCLUDE super_admin, master_admin)
    # =========================
    cursor.execute("""
    SELECT * FROM users
    WHERE organization = ?
    AND role NOT IN (?, ?)
    """, (organization, "super_admin", "master"))
    
    users_data = cursor.fetchall()
    users_cols = ["id", "username", "password", "role", "branch", "organization", "status", "pin", "phone", "gender", "created_at", "exclude_from_analytics"]
    users_df = pd.DataFrame(users_data, columns=users_cols) if users_data else pd.DataFrame()
    
    # =========================
    # GET LEAVES
    # =========================
    cursor.execute("""
    SELECT * FROM leaves
    WHERE organization = ?
    """, (organization,))
    
    leaves_data = cursor.fetchall()
    leaves_cols = ["id", "username", "organization", "branch", "start_date", "end_date", "reason", "status"]
    leaves_df = pd.DataFrame(leaves_data, columns=leaves_cols) if leaves_data else pd.DataFrame()

    # =========================
    # GET BRANCHES
    # =========================
    cursor.execute("""
    SELECT * FROM branches
    WHERE organization = ?
    """, (organization,))

    branches_data = cursor.fetchall()
    branches_cols = ["id", "name", "organization", "status"]
    branches_df = pd.DataFrame(branches_data, columns=branches_cols) if branches_data else pd.DataFrame()

    # =========================
    # GET PAYMENTS
    # =========================
    cursor.execute("""
    SELECT organization, amount, method, phone, created_at FROM payments
    WHERE organization = ?
    ORDER BY created_at DESC
    """, (organization,))

    payments_data = cursor.fetchall()
    payments_cols = ["organization", "amount", "method", "phone", "created_at"]
    payments_df = pd.DataFrame(payments_data, columns=payments_cols) if payments_data else pd.DataFrame()
    
    # =========================
    # GET MESSAGES
    # =========================
    cursor.execute("""
    SELECT * FROM system_messages
    WHERE organization = ?
    AND created_at >= datetime('now', '-7 days')
    ORDER BY created_at DESC
    """, (organization,))
    
    messages_data = cursor.fetchall()
    messages_cols = ["id", "from_user", "to_user", "organization", "branch", "message_type", "subject", "body", "priority", "read_at", "created_at"]
    messages_df = pd.DataFrame(messages_data, columns=messages_cols) if messages_data else pd.DataFrame()
    
    conn.close()
    
    # =====================================================
    # DEEP INTELLIGENCE ANALYSIS
    # =====================================================
    intelligence = generate_super_admin_intelligence(
        ratings_df,
        attendance_df if not attendance_df.empty else None,
        leaves_df if not leaves_df.empty else None,
        users_df if not users_df.empty else None,
        messages_df if not messages_df.empty else None,
    )
    
    # =========================
    # EXECUTIVE SUMMARY
    # =========================
    dashboard["executive_summary"] = {
        "summary_points": intelligence.get("executive_summary", []),
        "total_employees": len(users_df),
        "admins_count": len(users_df[users_df["role"] == "admin"]),
        "team_health_score": intelligence.get("team_health", {}).get("overall_score", 0),
        "team_health_status": intelligence.get("team_health", {}).get("status", "Unknown"),
    }
    
    # =========================
    # CRITICAL ALERTS (Top Priority)
    # =========================
    dashboard["critical_alerts"] = intelligence.get("critical_alerts", [])[:10]
    
    # =========================
    # KEY METRICS
    # =========================
    if not ratings_df.empty:
        dashboard["key_metrics"] = {
            "avg_rating": float(ratings_df["score"].mean()),
            "highest_rated": ratings_df.groupby("rated")["score"].mean().idxmax(),
            "lowest_rated": ratings_df.groupby("rated")["score"].mean().idxmin(),
            "total_ratings": len(ratings_df),
            "rating_topics": len(ratings_df["topic"].unique()),
        }
    
    # =========================
    # TEAM INSIGHTS
    # =========================
    dashboard["team_insights"] = {
        "positive": intelligence.get("positive_highlights", [])[:5],
        "concerns": intelligence.get("critical_alerts", [])[:5],
        "cooperation": "Cooperative" if "COOPERATIVE BRANCH" in str(intelligence.get("critical_alerts")) else "Needs monitoring",
    }
    
    # =========================
    # GROUP ANALYSIS WITH DEMOGRAPHICS
    # =========================
    group_data = analyze_group_demographics(ratings_df, attendance_df, users_df, organization, branch)
    dashboard["group_analysis"] = group_data
    
    # =========================
    # RECOMMENDATIONS
    # =========================
    dashboard["recommendations"] = intelligence.get("recommendations", [])[:15]
    
    # =========================
    # INDIVIDUAL FOCUS
    # =========================
    dashboard["individual_focus"] = intelligence.get("individuals_of_focus", {})
    
    # =========================
    # PERFORMANCE TRENDS (7-day if created_at available)
    # =========================
    if "created_at" in ratings_df.columns:
        ratings_df["created_at"] = pd.to_datetime(ratings_df["created_at"], errors="coerce")
        last_7_days = ratings_df[ratings_df["created_at"] >= datetime.now() - timedelta(days=7)]
        
        if not last_7_days.empty:
            dashboard["performance_trends"] = {
                "last_7_days_avg": float(last_7_days["score"].mean()),
                "trend": "Improving" if last_7_days["score"].mean() > ratings_df["score"].mean() else "Declining",
                "raters_last_7_days": int(last_7_days["rater"].nunique()),
            }

    dashboard["business_intelligence"] = _build_business_intelligence(
        ratings_df,
        attendance_df if not attendance_df.empty else None,
        users_df if not users_df.empty else None,
        payments_df if not payments_df.empty else None,
        branches_df if not branches_df.empty else None,
        dashboard,
    )
    dashboard["monthly_trends"] = _build_monthly_business_trends(
        ratings_df,
        payments_df if not payments_df.empty else None,
        attendance_df if not attendance_df.empty else None,
    )
    dashboard["branch_action_plan"] = _build_branch_action_plan(
        dashboard["business_intelligence"].get("branch_snapshot", {})
    )
    
    return dashboard


# =====================================================
# ORGANIZATION-WIDE COMPARISON DASHBOARD
# =====================================================
def get_multi_branch_dashboard(organization, super_admin_user=None):
    """Shows comparison across all branches in organization."""
    
    dashboards_by_branch = {}
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get all branches
    cursor.execute("""
    SELECT DISTINCT branch FROM ratings
    WHERE organization = ?
    """, (organization,))
    
    branches = [b[0] for b in cursor.fetchall()]
    conn.close()
    
    for branch in branches:
        dashboards_by_branch[branch] = get_super_admin_dashboard(organization, branch, super_admin_user)
    
    return {
        "organization": organization,
        "branches": dashboards_by_branch,
        "generated_at": datetime.now().isoformat(),
    }


# =====================================================
# ALERT GENERATION & STORAGE
# =====================================================
def save_dashboard_alerts(organization, branch, dashboard_data):
    """Saves alerts from dashboard to database for tracking."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    for alert in dashboard_data.get("critical_alerts", []):
        
        # Extract severity
        severity = "critical" if "🚨" in alert else "warning" if "⚠" in alert else "info"
        
        cursor.execute("""
        INSERT INTO alerts(organization, branch, alert_type, severity, subject, message, assigned_to, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (organization, branch, "system_intelligence", severity, alert[:50], alert, "super_admin", "open", datetime.now().isoformat()))
    
    conn.commit()
    conn.close()


# =====================================================
# CONTEXT MENU COMPONENTS FOR DASHBOARD
# =====================================================
def get_employee_context_menu(organization, employee_name):
    """Provides detailed context for specific employee."""
    
    context = {
        "employee": employee_name,
        "organization": organization,
        "details": {},
    }
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # User info
    cursor.execute("""
    SELECT * FROM users
    WHERE username = ? AND organization = ?
    """, (employee_name, organization))
    
    user = cursor.fetchone()
    user_cols = ["id", "username", "password", "role", "branch", "organization", "status", "pin", "phone", "gender", "created_at", "exclude_from_analytics"]
    
    if user:
        user_dict = dict(zip(user_cols, user))
        context["details"]["user"] = user_dict
    
    # Recent ratings
    cursor.execute("""
    SELECT * FROM ratings
    WHERE rated = ? AND organization = ?
    ORDER BY created_at DESC
    LIMIT 10
    """, (employee_name, organization))
    
    recent_ratings = cursor.fetchall()
    context["details"]["recent_ratings"] = [dict(zip(["id", "rater", "rated", "topic", "score", "branch", "organization", "created_at"], r)) for r in recent_ratings]
    
    # Recent attendance
    cursor.execute("""
    SELECT * FROM attendance
    WHERE username = ? AND organization = ?
    ORDER BY date DESC
    LIMIT 5
    """, (employee_name, organization))
    
    recent_attendance = cursor.fetchall()
    att_cols = ["id", "username", "branch", "organization", "clock_in", "clock_out", "status", "date", "image"]
    context["details"]["recent_attendance"] = [dict(zip(att_cols, a)) for a in recent_attendance]
    
    conn.close()
    
    return context
