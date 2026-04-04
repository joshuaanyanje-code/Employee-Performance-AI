import streamlit as st
import pandas as pd
from database.db import get_connection

from Analytics.leadership import detect_leaders
from Analytics.stability import stability_analysis
from Analytics.powermap import build_relationship_graph
from Analytics.insights import generate_insights
from Analytics.prediction import predict_future
from Analytics.decision_engine import management_recommendations
try:
    from Analytics.analytics_filter import filter_ratings_by_role, filter_users_by_role
except Exception:
    from .analytics_filter import filter_ratings_by_role, filter_users_by_role


def reports_panel():

    st.header("📊 Team Intelligence Reports")

    conn = get_connection()
    organization = str(st.session_state.get("organization", "") or "").strip()
    viewer_role = str(st.session_state.get("role", "superadmin") or "superadmin").strip().lower()
    current_user = str(st.session_state.get("username", "") or "").strip().lower()

    if organization:
        ratings = pd.read_sql("SELECT * FROM ratings WHERE organization=?", conn, params=(organization,))
        users = pd.read_sql("SELECT * FROM users WHERE organization=?", conn, params=(organization,))
        attendance = pd.read_sql("SELECT * FROM attendance WHERE organization=?", conn, params=(organization,))
    else:
        ratings = pd.read_sql("SELECT * FROM ratings", conn)
        users = pd.read_sql("SELECT * FROM users", conn)
        attendance = pd.read_sql("SELECT * FROM attendance", conn)

    users = filter_users_by_role(users, viewer_role=viewer_role)
    ratings = filter_ratings_by_role(ratings, viewer_role=viewer_role)

    if not users.empty and "username" in users.columns and current_user:
        users = users[users["username"].astype(str).str.strip().str.lower() != current_user].copy()

    allowed_users = set(users["username"].dropna().astype(str).str.strip().tolist()) if not users.empty and "username" in users.columns else set()

    if allowed_users and not ratings.empty:
        ratings = ratings[
            ratings["rater"].astype(str).str.strip().isin(allowed_users)
            & ratings["rated"].astype(str).str.strip().isin(allowed_users)
        ].copy()
    elif not ratings.empty:
        ratings = ratings.iloc[0:0].copy()

    if allowed_users and not attendance.empty and "username" in attendance.columns:
        attendance = attendance[attendance["username"].astype(str).str.strip().isin(allowed_users)].copy()

    if ratings.empty:
        st.warning("No data yet")
        return

    # =========================
    # REPORT MENU
    # =========================
    report = st.selectbox("Select Report", [

        "Overview",
        "Performance",
        "Attendance",
        "Consistency",
        "Non Voters",
        "Skipped Ratings",
        "Mutual Conflicts",
        "Group Rejection",
        "Leadership",
        "Conflicts & Behavior",
        "Stability",
        "Relationship Map",
        "AI Insights",
        "Predictions",
        "Management Actions"
    ])

    # =====================================================
    # OVERVIEW
    # =====================================================
    if report == "Overview":

        c1, c2, c3 = st.columns(3)

        c1.metric("Users", len(users))
        c2.metric("Ratings", len(ratings))
        c3.metric("Attendance Records", len(attendance))

    # =====================================================
    # PERFORMANCE
    # =====================================================
    elif report == "Performance":

        avg = ratings.groupby("rated")["score"].mean().sort_values(ascending=False)
        st.bar_chart(avg)

    # =====================================================
    # ATTENDANCE
    # =====================================================
    elif report == "Attendance":

        st.subheader("Attendance Summary")

        if attendance.empty:
            st.info("No attendance records available yet.")
        else:
            attendance_view = attendance.copy()

            if "date" in attendance_view.columns:
                attendance_view["date"] = pd.to_datetime(attendance_view["date"], errors="coerce")
            if "clock_in" in attendance_view.columns:
                attendance_view["clock_in"] = pd.to_datetime(attendance_view["clock_in"], errors="coerce")
            if "clock_out" in attendance_view.columns:
                attendance_view["clock_out"] = pd.to_datetime(attendance_view["clock_out"], errors="coerce")

            attendance_view["late_flag"] = (
                attendance_view["clock_in"].dt.hour.gt(9) if "clock_in" in attendance_view.columns else False
            )

            c1, c2, c3 = st.columns(3)
            c1.metric("Records", len(attendance_view))
            c2.metric("Staff Seen", attendance_view["username"].nunique() if "username" in attendance_view.columns else 0)
            c3.metric("Late Clock-ins", int(attendance_view["late_flag"].sum()) if "late_flag" in attendance_view.columns else 0)

            if {"username", "date"}.issubset(attendance_view.columns):
                attendance_summary = (
                    attendance_view.groupby("username", dropna=False)
                    .agg(days_present=("date", "nunique"), late_count=("late_flag", "sum"))
                    .reset_index()
                    .rename(columns={"username": "Employee"})
                    .sort_values(["late_count", "days_present"], ascending=[False, False])
                )
                st.dataframe(attendance_summary, use_container_width=True)
            else:
                st.dataframe(attendance_view, use_container_width=True)

    # =====================================================
    # CONSISTENCY
    # =====================================================
    elif report == "Consistency":

        st.subheader("Rating Consistency")

        consistency_df = (
            ratings.groupby("rated", dropna=False)
            .agg(Avg_Score=("score", "mean"), Rating_Count=("score", "count"), Std_Dev=("score", "std"))
            .reset_index()
            .rename(columns={"rated": "Employee"})
        )

        consistency_df["Avg_Score"] = consistency_df["Avg_Score"].round(1)
        consistency_df["Std_Dev"] = consistency_df["Std_Dev"].fillna(0).round(1)

        def consistency_label(std_dev):
            if std_dev <= 5:
                return "Highly Consistent"
            if std_dev <= 12:
                return "Moderately Consistent"
            return "Volatile"

        consistency_df["Consistency"] = consistency_df["Std_Dev"].apply(consistency_label)
        consistency_df = consistency_df.sort_values(["Std_Dev", "Avg_Score"], ascending=[True, False])
        st.dataframe(consistency_df, use_container_width=True)

        highly_consistent = consistency_df[consistency_df["Consistency"] == "Highly Consistent"]["Employee"].tolist()
        volatile_staff = consistency_df[consistency_df["Consistency"] == "Volatile"]["Employee"].tolist()

        if highly_consistent:
            st.success("Most consistent: " + ", ".join(highly_consistent[:5]))
        if volatile_staff:
            st.warning("Needs closer monitoring: " + ", ".join(volatile_staff[:5]))

    # =====================================================
    # NON VOTERS
    # =====================================================
    elif report == "Non Voters":

        raters = ratings["rater"].unique().tolist()
        all_users = users["username"].tolist()

        non_voters = [u for u in all_users if u not in raters]

        if non_voters:
            for u in non_voters:
                st.warning(f"{u} is not participating in rating")
        else:
            st.success("All users participated")

    # =====================================================
    # SKIPPED RATINGS 🔥
    # =====================================================
    elif report == "Skipped Ratings":

        st.subheader("🚫 Skipped / Partial Ratings")

        all_users = users["username"].tolist()
        skipped_summary = []

        for rater in ratings["rater"].unique():

            rated_people = ratings[ratings["rater"] == rater]["rated"].unique().tolist()
            expected = [u for u in all_users if u != rater]

            skipped = [u for u in expected if u not in rated_people]

            if len(rated_people) == 0:
                st.error(f"{rater} rated nobody → HIGH RISK")

            elif skipped:
                st.warning(f"{rater} skipped {len(skipped)} people")

                for s in skipped:
                    st.write(f"➤ Skipped: {s}")

                skipped_summary.append({
                    "user": rater,
                    "skipped": len(skipped),
                    "people": ", ".join(skipped)
                })

        if skipped_summary:
            st.dataframe(pd.DataFrame(skipped_summary), use_container_width=True)

    # =====================================================
    # MUTUAL CONFLICT 🔥🔥
    # =====================================================
    elif report == "Mutual Conflicts":

        st.subheader("⚔ Mutual Conflict Detection")

        all_users = sorted(set(users["username"].dropna().astype(str).str.strip().tolist()))

        conflict_pairs = []

        for idx, u1 in enumerate(all_users):
            for u2 in all_users[idx + 1:]:

                rated_by_u1 = ratings[ratings["rater"].astype(str) == u1]["rated"].astype(str).tolist()
                rated_by_u2 = ratings[ratings["rater"].astype(str) == u2]["rated"].astype(str).tolist()

                if u2 not in rated_by_u1 and u1 not in rated_by_u2:
                    conflict_pairs.append((u1, u2))

        if conflict_pairs:
            for a, b in conflict_pairs:
                st.error(f"⚠ {a} and {b} are mutually avoiding each other → CONFIRMED CONFLICT")

        else:
            st.success("No strong mutual conflicts detected")

    # =====================================================
    # GROUP REJECTION 🔥🔥🔥
    # =====================================================
    elif report == "Group Rejection":

        st.subheader("🚫 Group Rejection Detection")

        all_users = users["username"].tolist()

        rejection_data = []

        for target in all_users:

            skipped_by = []

            for rater in all_users:

                if rater == target:
                    continue

                rated_people = ratings[ratings["rater"] == rater]["rated"].tolist()

                if target not in rated_people:
                    skipped_by.append(rater)

            if len(skipped_by) >= 3:

                st.error(f"🚫 {target} is being avoided by {len(skipped_by)} people")

                for s in skipped_by:
                    st.write(f"➤ Skipped by: {s}")

                rejection_data.append({
                    "target": target,
                    "skipped_by_count": len(skipped_by),
                    "people": ", ".join(skipped_by)
                })

        if rejection_data:
            st.dataframe(pd.DataFrame(rejection_data), use_container_width=True)

        else:
            st.success("No group rejection detected")

    # =====================================================
    # LEADERSHIP
    # =====================================================
    elif report == "Leadership":

        leaders_result = detect_leaders(ratings, attendance, None, users, None)

        if isinstance(leaders_result, tuple):
            leaders_df, leader_insights = leaders_result
        else:
            leaders_df, leader_insights = leaders_result, []

        if isinstance(leaders_df, pd.DataFrame) and not leaders_df.empty:
            st.dataframe(leaders_df, use_container_width=True)
        else:
            st.info("No leadership scores available yet.")

        if leader_insights:
            st.markdown("### Leadership Insights")
            for item in leader_insights:
                st.info(item)

    # =====================================================
    # CONFLICTS
    # =====================================================
    elif report == "Conflicts & Behavior":

        conflicts = ratings[ratings["score"] < 40]

        if conflicts.empty:
            st.success("No major conflict behavior detected in the current report scope.")
        else:
            for _, row in conflicts.iterrows():
                st.warning(f"{row['rater']} → {row['rated']} low rating")

    # =====================================================
    # STABILITY
    # =====================================================
    elif report == "Stability":

        stability_result = stability_analysis(ratings, attendance, users)

        if isinstance(stability_result, tuple):
            stability_df, stability_insights = stability_result
        else:
            stability_df, stability_insights = stability_result, []

        if isinstance(stability_df, pd.DataFrame) and not stability_df.empty:
            st.dataframe(stability_df, use_container_width=True)
        else:
            st.info("No stability data available yet.")

        if stability_insights:
            st.markdown("### Stability Insights")
            for item in stability_insights:
                st.info(item)

    # =====================================================
    # RELATIONSHIP MAP
    # =====================================================
    elif report == "Relationship Map":

        fig = build_relationship_graph(ratings)

        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No relationship map data is available for the current scope.")

    # =====================================================
    # AI INSIGHTS
    # =====================================================
    elif report == "AI Insights":

        insights = generate_insights(ratings, attendance, None, users, None)

        if insights:
            for i in insights:
                st.info(i)
        else:
            st.info("No AI insights generated yet for the current scope.")

    # =====================================================
    # PREDICTIONS
    # =====================================================
    elif report == "Predictions":

        preds = predict_future(ratings, attendance, users)

        if preds:
            for p in preds:
                st.warning(p)
        else:
            st.info("No prediction signals are available yet.")

    # =====================================================
    # MANAGEMENT ACTIONS
    # =====================================================
    elif report == "Management Actions":

        recs = management_recommendations(ratings, attendance)

        if recs:
            for r in recs:
                st.success(r)
        else:
            st.info("No urgent management actions are recommended right now.")


# =====================================================
# NEW: COMPREHENSIVE REPORTING FUNCTIONS (BACKEND)
# =====================================================
import json
from datetime import datetime

def generate_analytics_report(organization, branch=None, report_type="executive_summary", generated_by="system"):
    """
    Generates analytical reports with intelligence insights.
    report_type: 'executive_summary', 'favoritism', 'isolation', 'groups', 'performance', 'retention'
    """
    
    try:
        from .decision_engine import generate_super_admin_intelligence
    except:
        from Analytics.decision_engine import generate_super_admin_intelligence
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get ratings data (exclude super_admin, master)
    exclude_roles = ("super_admin", "master")
    
    if branch:
        cursor.execute("""
        SELECT r.* FROM ratings r
        WHERE r.organization = ?
        AND r.branch = ?
        AND r.rater NOT IN (SELECT username FROM users WHERE role IN (?, ?))
        AND r.rated NOT IN (SELECT username FROM users WHERE role IN (?, ?))
        """, (organization, branch, "super_admin", "master", "super_admin", "master"))
    else:
        cursor.execute("""
        SELECT r.* FROM ratings r
        WHERE r.organization = ?
        AND r.rater NOT IN (SELECT username FROM users WHERE role IN (?, ?))
        AND r.rated NOT IN (SELECT username FROM users WHERE role IN (?, ?))
        """, (organization, "super_admin", "master", "super_admin", "master"))
    
    ratings_data = cursor.fetchall()
    ratings_cols = ["id", "rater", "rated", "topic", "score", "branch", "organization", "created_at"]
    ratings_df = pd.DataFrame(ratings_data, columns=ratings_cols) if ratings_data else pd.DataFrame()
    
    # Get other data
    cursor.execute("""
    SELECT * FROM attendance
    WHERE organization = ?
    AND username NOT IN (SELECT username FROM users WHERE role IN (?, ?))
    """, (organization, "super_admin", "master"))
    
    attendance_data = cursor.fetchall()
    attendance_df = pd.DataFrame(attendance_data) if attendance_data else pd.DataFrame()
    
    cursor.execute("SELECT * FROM users WHERE organization = ? AND role NOT IN (?, ?)", 
                  (organization, "super_admin", "master"))
    users_data = cursor.fetchall()
    users_df = pd.DataFrame(users_data) if users_data else pd.DataFrame()
    
    conn.close()
    
    # Generate intelligence
    intelligence = generate_super_admin_intelligence(
        ratings_df,
        attendance_df if not attendance_df.empty else None,
        None,
        users_df if not users_df.empty else None,
        None,
    )
    
    # Build report
    report = {
        "report_type": report_type,
        "organization": organization,
        "branch": branch,
        "generated_by": generated_by,
        "generated_at": datetime.now().isoformat(),
        "metrics": {
            "total_employees": len(users_df),
            "total_ratings": len(ratings_df),
            "team_health_score": intelligence.get("team_health", {}).get("overall_score", 0),
            "critical_issues": len(intelligence.get("critical_alerts", [])),
        },
        "content": {},
    }
    
    # Populate based on report type
    if report_type == "executive_summary":
        report["content"] = {
            "summary": intelligence.get("executive_summary", []),
            "team_health": intelligence.get("team_health", {}),
            "critical_alerts": intelligence.get("critical_alerts", [])[:10],
            "recommendations": intelligence.get("recommendations", [])[:10],
        }
    
    elif report_type == "favoritism":
        report["content"] = {
            "findings": [a for a in intelligence.get("critical_alerts", []) if "FAVORITISM" in a or "BIAS" in a],
        }
    
    elif report_type == "isolation":
        report["content"] = {
            "findings": [a for a in intelligence.get("critical_alerts", []) if "ISOLATION" in a],
        }
    
    elif report_type == "groups":
        report["content"] = {
            "findings": [a for a in intelligence.get("critical_alerts", []) if "GROUP" in a or "DATING" in a],
        }
    
    elif report_type == "performance":
        report["content"] = {
            "top_performers": intelligence.get("positive_highlights", [])[:5],
            "low_performers": [a for a in intelligence.get("critical_alerts", []) if "low" in a.lower()],
        }
    
    elif report_type == "retention":
        report["content"] = {
            "at_risk": intelligence.get("individuals_of_focus", {}).get("at_risk_retention", []),
            "recommendations": [r for r in intelligence.get("recommendations", []) if "support" in r.lower() or "leave" in r.lower()],
        }
    
    return report


def export_report_as_text(report_dict):
    """Exports report as readable text."""
    
    output = []
    output.append(f"\n{'='*70}")
    output.append(f"ANALYTICS REPORT - {report_dict.get('report_type', 'UNKNOWN').upper()}")
    output.append(f"Organization: {report_dict.get('organization')}")
    output.append(f"Branch: {report_dict.get('branch', 'ALL')}")
    output.append(f"Generated: {report_dict.get('generated_at')}")
    output.append(f"{'='*70}\n")
    
    # Metrics
    output.append("📊 KEY METRICS:")
    for key, val in report_dict.get("metrics", {}).items():
        output.append(f"   • {key}: {val}")
    output.append()
    
    # Content
    content = report_dict.get("content", {})
    for section_name, section_data in content.items():
        output.append(f"\n📌 {section_name.upper()}:")
        output.append("-" * 70)
        
        if isinstance(section_data, list):
            for item in section_data[:10]:
                output.append(f"   • {item}")
        elif isinstance(section_data, dict):
            for key, val in list(section_data.items())[:5]:
                output.append(f"   • {key}: {val}")
    
    return "\n".join(output)
