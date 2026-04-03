import pandas as pd
from datetime import datetime

# Import new deep analytics functions
try:
    from .stability import (
        detect_favoritism,
        detect_isolation_and_low_performers,
        detect_bad_seed_toxic_influence,
        calculate_team_health_score
    )
    from .powermap import detect_synchronized_groups
    from .prediction import (
        detect_psychological_withdrawal,
        detect_family_issues_and_personal_crisis,
        detect_conflict_resolution_and_reconciliation,
        detect_cooperation_and_timing,
        detect_team_alignment
    )
    from .leadership import (
        detect_popular_influencers,
        detect_elite_top_performers,
        detect_bad_managers,
        detect_well_performing_branches
    )
    from .analytics_filter import apply_role_filters_to_analytics, sanitize_analytics_output
except ImportError:
    from Analytics.stability import (
        detect_favoritism,
        detect_isolation_and_low_performers,
        detect_bad_seed_toxic_influence,
        calculate_team_health_score
    )
    from Analytics.powermap import detect_synchronized_groups
    from Analytics.prediction import (
        detect_psychological_withdrawal,
        detect_family_issues_and_personal_crisis,
        detect_conflict_resolution_and_reconciliation,
        detect_cooperation_and_timing,
        detect_team_alignment
    )
    from Analytics.leadership import (
        detect_popular_influencers,
        detect_elite_top_performers,
        detect_bad_managers,
        detect_well_performing_branches
    )
    from Analytics.analytics_filter import apply_role_filters_to_analytics, sanitize_analytics_output


def generate_super_admin_intelligence(
    ratings_df,
    attendance_df=None,
    leaves_df=None,
    users_df=None,
    messages_df=None,
    schedules_df=None
):
    """
    Generates deep intelligence report for super admin with all detected patterns,
    risks, and AI recommendations.
    
    Automatically filters out super_admin and master_admin from all analytics.
    """
    
    # =====================================================
    # APPLY ROLE FILTERS (Exclude super_admin, master_admin)
    # =====================================================
    ratings_df, attendance_df, users_df, messages_df = apply_role_filters_to_analytics(
        ratings_df, 
        attendance_df, 
        users_df, 
        messages_df,
        viewer_role="super_admin"
    )
    
    report = {
        "executive_summary": [],
        "critical_alerts": [],
        "recommendations": [],
        "team_health": {},
        "individuals_of_focus": {},
        "positive_highlights": [],
    }
    
    if ratings_df.empty:
        return report
    
    # =========================
    # TEAM HEALTH ASSESSMENT
    # =========================
    overall_health_score, health_status = calculate_team_health_score(ratings_df)
    report["team_health"] = {
        "overall_score": overall_health_score,
        "status": health_status,
        "threshold_explanation": ">=75% = Healthy, <65% = Unhealthy"
    }
    
    # =========================
    # DEEP ANALYSIS ACROSS ALL DIMENSIONS
    # =========================
    favoritism_flags = detect_favoritism(ratings_df, users_df)
    isolation_flags = detect_isolation_and_low_performers(ratings_df, users_df)
    bad_seed_flags = detect_bad_seed_toxic_influence(ratings_df, messages_df)
    
    group_insights, group_dict = detect_synchronized_groups(attendance_df, ratings_df, users_df, leaves_df)
    
    withdrawal_flags = detect_psychological_withdrawal(ratings_df)
    family_issue_flags = detect_family_issues_and_personal_crisis(ratings_df, attendance_df, messages_df)
    conflict_flags = detect_conflict_resolution_and_reconciliation(ratings_df)
    cooperation_flags = detect_cooperation_and_timing(ratings_df)
    alignment_flags = detect_team_alignment(ratings_df)
    
    influencer_insights = detect_popular_influencers(ratings_df, users_df, attendance_df)
    top_performer_insights = detect_elite_top_performers(ratings_df)
    bad_manager_insights, bad_manager_actions = detect_bad_managers(ratings_df, users_df, attendance_df, messages_df)
    branch_insights = detect_well_performing_branches(ratings_df, users_df, attendance_df, messages_df)
    
    # =========================
    # EXECUTIVE SUMMARY
    # =========================
    report["executive_summary"] = [
        f"📊 {health_status} | Overall Score: {overall_health_score:.1f}%",
        f"👥 Employees: {ratings_df['rated'].nunique()} | Raters: {ratings_df['rater'].nunique()}",
        f"⚠ Favoritism Flags: {len(favoritism_flags)}",
        f"👥 Groups Detected: {len(group_dict)}",
        f"🚨 Critical Issues: {len(isolation_flags) + len(bad_seed_flags)}",
    ]
    
    # =========================
    # CRITICAL ALERTS (Super Admin Priority)
    # =========================
    report["critical_alerts"] = []
    
    # Critical: Isolation cases
    for flag in isolation_flags:
        if "ISOLATION DETECTED" in flag or "CRITICAL PERFORMANCE" in flag:
            report["critical_alerts"].append(flag)
    
    # Critical: Bad seeds
    for flag in bad_seed_flags:
        if "BAD SEED" in flag or "TOXIC" in flag:
            report["critical_alerts"].append(flag)
    
    # Critical: Bad managers
    for flag in bad_manager_insights:
        if "BAD MANAGER" in flag:
            report["critical_alerts"].append(flag)
    
    # Critical: Groups with possible relationships/dating
    for insight in group_insights:
        if "DATING" in insight or "RELATIONSHIP" in insight:
            report["critical_alerts"].append(insight)
    
    report["critical_alerts"] = list(set(report["critical_alerts"]))[:10]  # Top 10
    
    # =========================
    # RECOMMENDATIONS
    # =========================
    report["recommendations"] = []
    
    # Add bad manager actions
    report["recommendations"].extend(bad_manager_actions)
    
    # Add favoritism fix recommendations
    for flag in favoritism_flags:
        if "FAVORITISM" in flag:
            report["recommendations"].append(f"ACTION: Investigate & address - {flag}")
    
    # Add retention tactics for withdrawal employees
    for flag in withdrawal_flags:
        if "WITHDRAWAL" in flag:
            employee = flag.split(":")[1].strip().split()[0]
            report["recommendations"].append(f"RETENTION: Bring Bridge training for {employee}, offer flex time, understand personal issues")
    
    # Add family issue support
    for flag in family_issue_flags:
        if "FAMILY" in flag or "PERSONAL" in flag:
            employee = flag.split(":")[1].strip().split()[0]
            report["recommendations"].append(f"SUPPORT: Offer leave, flex time, temporary relief duties to {employee}")
    
    # Add group investigation recommendations
    for insight in group_insights:
        if "GROUP DETECTED" in insight or "DATING" in insight:
            report["recommendations"].append(f"INVESTIGATE: {insight} - Consider shift change or deployment")
    
    # Add cooperation recommendations
    if "NON-COOPERATIVE" in str(cooperation_flags):
        report["recommendations"].append("ENGAGEMENT: Branch showing low engagement in ratings - conduct team meeting")
    
    # Add conflict resolution recommendations
    for flag in conflict_flags:
        if "ESCALATION" in flag:
            report["recommendations"].append(f"MEDIATION: {flag} - Facilitate discussion")
        elif "RECONCILIATION" in flag:
            report["recommendations"].append(f"RECOGNIZE: {flag}")
    
    report["recommendations"] = list(set(report["recommendations"]))[:15]
    
    # =========================
    # INDIVIDUALS OF FOCUS
    # =========================
    # Problematic individuals
    problematic = []
    at_risk = []
    high_performers = []
    
    for insight in isolation_flags + bad_seed_flags + withdrawal_flags:
        if any(x in insight for x in ["ISOLATION", "CRITICAL", "BAD SEED", "WITHDRAWAL"]):
            parts = insight.split(":")
            if len(parts) > 1:
                person = parts[1].strip().split()[0]
                problematic.append(person)
    
    for insight in family_issue_flags:
        if "FAMILY" in insight or "PERSONAL" in insight:
            parts = insight.split(":")
            if len(parts) > 1:
                person = parts[1].strip().split()[0]
                at_risk.append(person)
    
    for insight in top_performer_insights:
        parts = insight.split(":")
        if len(parts) > 1:
            person = parts[1].strip().split()[0]
            high_performers.append(person)
    
    report["individuals_of_focus"] = {
        "problematic": list(set(problematic))[:5],
        "at_risk_retention": list(set(at_risk))[:5],
        "high_performers": list(set(high_performers))[:5],
    }
    
    # =========================
    # POSITIVE HIGHLIGHTS
    # =========================
    report["positive_highlights"] = []
    report["positive_highlights"].extend(influencer_insights[:3])
    report["positive_highlights"].extend(top_performer_insights[:3])
    report["positive_highlights"].extend(branch_insights[:3])
    report["positive_highlights"].extend(alignment_flags[:2])
    
    if "COOPERATIVE BRANCH" in str(cooperation_flags):
        report["positive_highlights"].append("✅ Branch showing strong engagement and cooperation")
    
    report["positive_highlights"] = list(set(report["positive_highlights"]))[:10]

    # =========================
    # BIASNESS & FAVORITISM (full detail for dedicated UI section)
    # =========================
    report["favoritism_analysis"] = favoritism_flags

    # =========================
    # ISOLATION ANALYSIS (full detail for dedicated UI section)
    # =========================
    report["isolation_analysis"] = isolation_flags

    return report


def management_recommendations(ratings_df, attendance_df, schedules_df=None):

    recommendations = []

    # =========================
    # PREP DATA
    # =========================
    if not attendance_df.empty:
        attendance_df["clock_in"] = pd.to_datetime(attendance_df["clock_in"], errors="coerce")
        attendance_df["clock_out"] = pd.to_datetime(attendance_df["clock_out"], errors="coerce")
        attendance_df["date"] = pd.to_datetime(attendance_df["date"], errors="coerce")

    avg_scores = ratings_df.groupby("rated")["score"].mean()

    # =========================
    # BASIC PERFORMANCE
    # =========================
    for name, score in avg_scores.items():

        if score > 92:
            recommendations.append(f"⭐ Promote {name}. Strong leadership signals.")

        elif 49 <= score < 55:
            recommendations.append(f"📚 {name} needs training. Performance is average.")

        elif 35 <= score < 52:
            recommendations.append(f"⚠ {name} needs improvement support.")

        elif score < 42:
            recommendations.append(f"🚪 {name} is a retention risk. Engage immediately.")

    # =========================
    # ATTENDANCE ANALYSIS
    # =========================
    if not attendance_df.empty:

        now = datetime.now()
        current_month = now.month

        for user in attendance_df["username"].unique():

            df = attendance_df[attendance_df["username"] == user]
            df = df[df["date"].dt.month == current_month]

            late_count = 0
            early_leave = 0

            for _, row in df.iterrows():

                if pd.notna(row["clock_in"]):
                    if row["clock_in"].hour > 9:
                        late_count += 1

                if pd.notna(row["clock_out"]):
                    if row["clock_out"].hour < 18:
                        early_leave += 1

            # ABSENT
            days_present = df["date"].nunique()
            total_days = now.day
            absent_days = max(total_days - days_present, 0)

            if absent_days >= 2:
                recommendations.append(f"🚫 {user} frequent absence detected. HIGH RISK.")

            if late_count >= 3:
                recommendations.append(f"⚠ {user} frequently late. Staff discipline issue.")

            if late_count >= 3 and absent_days >= 2:
                recommendations.append(f"🔥 {user} lateness + absence → CRITICAL RISK.")

            # EARLY LEAVE
            if early_leave >= 3:
                score = avg_scores.get(user, 0)

                if 50 <= score <= 65:
                    recommendations.append(
                        f"⚠ {user} early leaves + average score → Personal issue. Engage."
                    )
                else:
                    recommendations.append(
                        f"⚠ {user} frequent early leave → disengagement."
                    )

            # EXTREME LATE
            extreme_late = df[df["clock_in"].dt.hour >= 11]

            if len(extreme_late) >= 2:
                score = avg_scores.get(user, 0)

                if score < 55:
                    recommendations.append(
                        f"🚨 {user} extreme lateness + low rating → Quit risk."
                    )

            # QUIET QUITTING
            df_sorted = df.sort_values("date")

            for i in range(len(df_sorted) - 2):

                d1 = df_sorted.iloc[i]
                d2 = df_sorted.iloc[i + 1]
                d3 = df_sorted.iloc[i + 2]

                if (
                    pd.notna(d1["clock_in"]) and d1["clock_in"].hour > 9 and
                    pd.isna(d2["clock_in"]) and
                    pd.notna(d3["clock_out"]) and d3["clock_out"].hour < 18
                ):
                    recommendations.append(
                        f"🚪 {user} quiet quitting pattern detected."
                    )

    # =========================
    # POST OFF DAY
    # =========================
    if schedules_df is not None and not attendance_df.empty:

        for user in attendance_df["username"].unique():

            sched = schedules_df[schedules_df["username"] == user]

            if sched.empty:
                continue

            off_day = sched.iloc[0]["off_day"]

            df = attendance_df[attendance_df["username"] == user].sort_values("date")

            late_after_off = 0

            for i in range(1, len(df)):

                prev_day = df.iloc[i - 1]["date"].strftime("%A")
                curr = df.iloc[i]

                if prev_day == off_day and pd.notna(curr["clock_in"]):
                    if curr["clock_in"].hour > 9:
                        late_after_off += 1

            if late_after_off >= 2:
                recommendations.append(
                    f"⚠ {user} late after off-days → Possible job search."
                )

    # =========================
    # RATING BEHAVIOR
    # =========================
    if not ratings_df.empty:

        for rater in ratings_df["rater"].unique():

            df = ratings_df[ratings_df["rater"] == rater]

            if df["score"].mean() > 76:
                recommendations.append(f"⚠ {rater} gives unrealistic high ratings.")

            high = df[df["score"] > 86]["rated"].tolist()
            low = df[df["score"] < 45]["rated"].tolist()

            if len(high) <= 2 and len(high) > 0:
                recommendations.append(f"⚠ {rater} favoritism → {high}")

            if len(low) <= 2 and len(low) > 0:
                recommendations.append(f"⚠ {rater} conflict → {low}")

    # =====================================================
    # 🔥 ADMIN INTELLIGENCE (UPGRADED)
    # =====================================================
    if not ratings_df.empty and not attendance_df.empty:

        admins = ratings_df["rated"].unique()
        now = datetime.now()
        current_month = now.month

        for admin in admins:

            # =========================
            # RATINGS
            # =========================
            df = ratings_df[ratings_df["rated"] == admin]

            if df.empty:
                continue

            scores = df["score"]
            avg = scores.mean()
            total = len(scores)

            low_ratings = scores[scores < 55].count()
            high_ratings = scores[scores > 80].count()

            # =========================
            # ATTENDANCE FOR ADMIN
            # =========================
            att = attendance_df[attendance_df["username"] == admin]
            att = att[att["date"].dt.month == current_month]

            late = 0
            early = 0

            for _, row in att.iterrows():

                if pd.notna(row["clock_in"]) and row["clock_in"].hour > 9:
                    late += 1

                if pd.notna(row["clock_out"]) and row["clock_out"].hour < 18:
                    early += 1

            absent = max(now.day - att["date"].nunique(), 0)

            # =========================
            # YOUR NEW RULES 🔥
            # =========================

            # BAD ADMIN PERFORMANCE
            if late >= 3 and early >= 3 and avg < 60:
                recommendations.append(
                    f"🚨 Admin {admin} poor discipline + low rating → Not performing / possible exit."
                )

            # TERMINATION LEVEL
            if absent >= 3:
                recommendations.append(
                    f"⛔ Admin {admin} excessive absence → Recommend termination. Not leading by example."
                )

            # BAD EXAMPLE
            if late >= 3 or early >= 3:
                recommendations.append(
                    f"⚠ Admin {admin} poor attendance → Negative leadership example."
                )

            # =========================
            # EXISTING ADMIN LOGIC
            # =========================
            if avg <= 55:
                recommendations.append(
                    f"🚨 Admin {admin} rated poorly → Leadership issue."
                )

            if avg >= 60:
                recommendations.append(
                    f"👍 Admin {admin} doing okay → Encourage listening to staff."
                )

            if 65 <= avg <= 78:
                recommendations.append(
                    f"🎯 Admin {admin} performing well → Recognize."
                )

            if high_ratings == total and total > 3:
                recommendations.append(
                    f"⚠ Admin {admin} overly high ratings → Fear or exaggeration."
                )

            if low_ratings >= (total / 2):
                recommendations.append(
                    f"🚨 Admin {admin} rejected by majority → Immediate intervention."
                )

            if low_ratings <= 2 and high_ratings >= 3:
                recommendations.append(
                    f"⚠ Minor conflict between admin {admin} and few staff."
                )

            # =========================
            # ADMIN BIAS
            # =========================
            admin_given = ratings_df[ratings_df["rater"] == admin]

            if not admin_given.empty:

                high_given = admin_given[admin_given["score"] > 86]["rated"].tolist()
                low_given = admin_given[admin_given["score"] < 45]["rated"].tolist()

                if len(high_given) <= 2 and len(high_given) > 0:
                    recommendations.append(
                        f"⚠ Admin {admin} favoritism → {high_given}"
                    )

                if len(low_given) <= 2 and len(low_given) > 0:
                    recommendations.append(
                        f"⚠ Admin {admin} conflict → {low_given}"
                    )

    return list(set(recommendations))
