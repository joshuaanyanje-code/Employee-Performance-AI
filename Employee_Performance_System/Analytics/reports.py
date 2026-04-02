import streamlit as st
import pandas as pd
from database.db import get_connection

from Analytics.leadership import detect_leaders
from Analytics.stability import stability_analysis
from Analytics.powermap import build_relationship_graph
from Analytics.insights import generate_insights
from Analytics.prediction import predict_future
from Analytics.decision_engine import management_recommendations


def reports_panel():

    st.header("📊 Team Intelligence Reports")

    conn = get_connection()

    ratings = pd.read_sql("SELECT * FROM ratings", conn)
    users = pd.read_sql("SELECT * FROM users", conn)
    attendance = pd.read_sql("SELECT * FROM attendance", conn)

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

        all_users = users["username"].tolist()

        conflict_pairs = []

        for u1 in all_users:
            for u2 in all_users:

                if u1 == u2:
                    continue

                rated_by_u1 = ratings[ratings["rater"] == u1]["rated"].tolist()
                rated_by_u2 = ratings[ratings["rater"] == u2]["rated"].tolist()

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

        st.dataframe(detect_leaders(ratings))

    # =====================================================
    # CONFLICTS
    # =====================================================
    elif report == "Conflicts & Behavior":

        conflicts = ratings[ratings["score"] < 40]

        for _, row in conflicts.iterrows():
            st.warning(f"{row['rater']} → {row['rated']} low rating")

    # =====================================================
    # STABILITY
    # =====================================================
    elif report == "Stability":

        st.dataframe(stability_analysis(ratings))

    # =====================================================
    # RELATIONSHIP MAP
    # =====================================================
    elif report == "Relationship Map":

        fig = build_relationship_graph(ratings)

        if fig:
            st.plotly_chart(fig, use_container_width=True)

    # =====================================================
    # AI INSIGHTS
    # =====================================================
    elif report == "AI Insights":

        insights = generate_insights(ratings)

        for i in insights:
            st.info(i)

    # =====================================================
    # PREDICTIONS
    # =====================================================
    elif report == "Predictions":

        preds = predict_future(ratings)

        for p in preds:
            st.warning(p)

    # =====================================================
    # MANAGEMENT ACTIONS
    # =====================================================
    elif report == "Management Actions":

        recs = management_recommendations(ratings, attendance)

        for r in recs:
            st.success(r)


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
