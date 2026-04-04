
import pandas as pd
from datetime import datetime


def stability_analysis(ratings_df, attendance_df=None, users_df=None):

    if ratings_df.empty:
        return pd.DataFrame(), []

    insights = []

    # =========================
    # ORIGINAL LOGIC (KEPT ✅)
    # =========================
    avg = ratings_df.groupby("rated")["score"].mean().reset_index()
    avg.columns = ["Employee", "Avg_Score"]

    def classify(score):
        if score < 35:
            return "🚨 Toxic / Conflict Risk"
        elif score < 47:
            return "📚 Needs Improvement"
        elif score > 85:
            return "⭐ High Performer"
        else:
            return "✅ Stable"

    avg["Status"] = avg["Avg_Score"].apply(classify)

    # =====================================================
    # 🔥 FAKE RATING DETECTION
    # =====================================================
    overall_mean = ratings_df["score"].mean()

    if overall_mean > 85:
        insights.append("⚠ Ratings too high → Possible fake / fear-based scoring")

    if overall_mean < 40:
        insights.append("⚠ Ratings too low → Possible sabotage or toxic culture")

    # =====================================================
    # 🎯 SABOTAGE DETECTION
    # =====================================================
    low_scores = ratings_df[ratings_df["score"] < 40]

    if len(low_scores) > len(ratings_df) * 0.4:
        insights.append("🚨 High negative ratings → Possible sabotage behavior")

    # =====================================================
    # 📊 PERFORMANCE VARIANCE
    # =====================================================
    variance = ratings_df.groupby("rated")["score"].std()

    unstable = variance[variance > 20]

    for user in unstable.index:
        insights.append(f"⚠ {user} has unstable performance (inconsistent ratings)")

    # =====================================================
    # ⏰ ATTENDANCE ANALYSIS
    # =====================================================
    late_total = 0
    absent_total = 0

    if attendance_df is not None and not attendance_df.empty:

        attendance_df["clock_in"] = pd.to_datetime(attendance_df["clock_in"], errors="coerce")
        attendance_df["date"] = pd.to_datetime(attendance_df["date"], errors="coerce")

        current_month = datetime.now().month

        for user in attendance_df["username"].unique():

            df = attendance_df[
                (attendance_df["username"] == user) &
                (attendance_df["date"].dt.month == current_month)
            ]

            late = len(df[df["clock_in"].dt.hour > 9])

            days_present = df["date"].nunique()
            total_days = datetime.now().day
            absent = total_days - days_present

            late_total += late
            absent_total += absent

            if late >= 5:
                insights.append(f"⚠ {user} frequent lateness → rule discipline issue")

            if absent >= 3:
                insights.append(f"🚫 {user} high absenteeism")

    # =====================================================
    # 👑 ADMIN DISCIPLINE
    # =====================================================
    if users_df is not None and attendance_df is not None:

        admins = users_df[users_df["role"] == "admin"]["username"]

        for admin in admins:

            df = attendance_df[attendance_df["username"] == admin]

            if df.empty:
                continue

            late = len(df[df["clock_in"].dt.hour > 9])

            if late >= 3:
                insights.append(f"🚨 Admin {admin} poor discipline (late frequently)")

    # =====================================================
    # 🧠 DEFECTIVE / DEFENSIVE STAFF
    # =====================================================
    for user, score in avg.set_index("Employee")["Avg_Score"].items():

        if score < 40:
            insights.append(f"🚨 {user} possibly defensive / resistant to system")

        elif score > 90:
            insights.append(f"⚠ {user} extremely high score → possible bias/fake ratings")

    # =====================================================
    # 🔥 FINAL SYSTEM STATE
    # =====================================================
    risk_score = 0

    if overall_mean < 55:
        risk_score += 2

    if late_total > 20:
        risk_score += 1

    if absent_total > 15:
        risk_score += 2

    if len(low_scores) > len(ratings_df) * 0.4:
        risk_score += 2

    # =========================
    # FINAL DECISION
    # =========================
    if risk_score <= 2:
        system_state = "✅ STABLE"

    elif risk_score <= 4:
        system_state = "⚠ RISKY (Monitor Closely)"

    elif risk_score <= 6:
        system_state = "🚨 CRITICAL (Immediate Action Required)"

    else:
        system_state = "🔥 EMERGENCY (Restructure Team Immediately)"

    insights.append(f"📊 System Status: {system_state}")

    return avg.sort_values(by="Avg_Score"), list(set(insights))


# =====================================================
# 🔥 DEEP FAVORITISM DETECTION
# =====================================================
def detect_favoritism(ratings_df, users_df=None):
    """Detects various favoritism patterns."""
    
    insights = []
    
    if ratings_df.empty:
        return insights
    
    # Get all employees and group by rater
    all_employees = ratings_df["rated"].unique()
    num_employees = len(all_employees)
    
    for rater in ratings_df["rater"].unique():
        rater_ratings = ratings_df[ratings_df["rater"] == rater]
        
        high_ratings = rater_ratings[rater_ratings["score"] > 78]
        low_ratings = rater_ratings[rater_ratings["score"] < 60]
        
        high_rated = high_ratings["rated"].unique()
        low_rated = low_ratings["rated"].unique()
        
        # RULE 1: In group of 5+, only 2 or less rate high, rest rate <60%
        if num_employees >= 5:
            if len(high_rated) <= 2 and len(low_rated) >= (num_employees - 2):
                individuals = ", ".join(high_rated)
                insights.append(f"⚠ FAVORITISM DETECTED: {rater} favors {individuals} (group of {num_employees} employees)")
        
        # RULE 2: In group of <4, one rated very high (>78%)
        if num_employees < 4 and len(high_rated) == 1:
            favored = high_rated[0]
            avg_rating = rater_ratings[rater_ratings["rated"] == favored]["score"].mean()
            if avg_rating > 78:
                insights.append(f"⚠ FAVORITISM in small group: {rater} rates {favored} very high ({avg_rating:.0f}%) - possible bias")
        
        # RULE 3: Rater gives everyone very high (>82%) - not genuine/joker
        if rater_ratings["score"].mean() > 82 and len(rater_ratings) >= 5:
            insights.append(f"🚨 UNREALISTIC RATER: {rater} rates almost everyone >82% - not genuine, possible 'I don't care' attitude")
    
    # =========================
    # MANAGER/ADMIN FAVORITISM
    # =========================
    if users_df is not None and not users_df.empty:
        
        admins = users_df[users_df["role"].isin(["admin", "manager"])]["username"].unique()
        
        for admin in admins:
            admin_ratings = ratings_df[ratings_df["rater"] == admin]
            
            if admin_ratings.empty:
                continue
            
            # RULE 4: Admin rates one person very high (>75%) in all topics, peer rated low
            high_rated = admin_ratings[admin_ratings["score"] > 75]["rated"].unique()
            
            if len(high_rated) == 1:
                favored_by_admin = high_rated[0]
                peer_ratings = ratings_df[(ratings_df["rated"] == favored_by_admin) & (ratings_df["rater"] != admin)]
                
                if not peer_ratings.empty:
                    peer_avg = peer_ratings["score"].mean()
                    if peer_avg < 60:
                        insights.append(f"⚠ ADMIN BIAS: {admin} rates {favored_by_admin} high ({admin_ratings[admin_ratings['rated']==favored_by_admin]['score'].mean():.0f}%) but peers rate low ({peer_avg:.0f}%)")
            
            # RULE 5: Admin & one employee rate each other very high (>78%) in all topics
            for employee in ratings_df["rated"].unique():
                admin_to_emp = admin_ratings[admin_ratings["rated"] == employee]["score"].mean()
                emp_to_admin = ratings_df[(ratings_df["rater"] == employee) & (ratings_df["rated"] == admin)]["score"].mean()
                
                if admin_to_emp > 78 and emp_to_admin > 78:
                    insights.append(f"👥 SUSPICIOUS GROUP: {admin} (admin) & {employee} rate each other very high (>{admin_to_emp:.0f}%) - possible group/taking sides")
    
    return list(set(insights))


# =====================================================
# 🔥 POWER ABUSE / RETALIATION RISK DETECTION
# =====================================================
def detect_power_abuse_and_retaliation(ratings_df, users_df=None, leaves_df=None, warnings_df=None, messages_df=None):
    """Detects evidence-based manager abuse, retaliation, and favoritism-risk patterns."""

    insights = []

    if users_df is None or users_df.empty or "username" not in users_df.columns or "role" not in users_df.columns:
        return insights

    users_work = users_df.copy()
    users_work["username"] = users_work["username"].astype(str).str.strip()
    role_norm = users_work["role"].astype(str).str.lower()
    admins = users_work[role_norm.isin(["admin", "manager"])]["username"].dropna().astype(str).str.strip().unique().tolist()

    if not admins:
        return insights

    ratings_work = pd.DataFrame() if ratings_df is None else ratings_df.copy()
    if not ratings_work.empty and {"rater", "rated", "score"}.issubset(ratings_work.columns):
        ratings_work["rater"] = ratings_work["rater"].astype(str).str.strip()
        ratings_work["rated"] = ratings_work["rated"].astype(str).str.strip()
        ratings_work["score"] = pd.to_numeric(ratings_work["score"], errors="coerce")
        ratings_work = ratings_work.dropna(subset=["score"])

        for admin in admins:
            admin_given = ratings_work[ratings_work["rater"] == admin]
            if admin_given.empty:
                continue

            if admin_given["rated"].nunique() >= 2 and len(admin_given) >= 4:
                summary = (
                    admin_given.groupby("rated")["score"]
                    .agg(["mean", "count"])
                    .reset_index()
                    .sort_values("mean")
                )
                if len(summary) >= 2:
                    low_row = summary.iloc[0]
                    high_row = summary.iloc[-1]
                    spread = float(high_row["mean"] - low_row["mean"])
                    if spread >= 25 and int(low_row["count"]) >= 2 and int(high_row["count"]) >= 2:
                        insights.append(
                            f"⚠ POWER ABUSE RISK: Admin {admin} rates {str(high_row['rated'])} much higher ({float(high_row['mean']):.0f}%) than {str(low_row['rated'])} ({float(low_row['mean']):.0f}%) - review for favoritism or targeting"
                        )

            for employee in admin_given["rated"].dropna().astype(str).unique():
                target_scores = admin_given[admin_given["rated"] == employee]["score"]
                peer_scores = ratings_work[(ratings_work["rated"] == employee) & (ratings_work["rater"] != admin)]["score"]

                if len(target_scores) >= 2 and len(peer_scores) >= 2:
                    admin_avg = float(target_scores.mean())
                    peer_avg = float(peer_scores.mean())

                    if admin_avg <= 50 and (peer_avg - admin_avg) >= 18 and peer_avg >= 60:
                        insights.append(
                            f"🚨 RETALIATION RISK: Admin {admin} rates {employee} much lower ({admin_avg:.0f}%) than peers ({peer_avg:.0f}%)"
                        )
                    elif admin_avg >= 78 and (admin_avg - peer_avg) >= 18 and peer_avg <= 60:
                        insights.append(
                            f"⚠ FAVOR PROTECTION RISK: Admin {admin} consistently shields {employee} with high ratings ({admin_avg:.0f}%) while peers average {peer_avg:.0f}%"
                        )

                emp_to_admin = ratings_work[(ratings_work["rater"] == employee) & (ratings_work["rated"] == admin)]["score"]
                if len(target_scores) >= 2 and len(emp_to_admin) >= 2:
                    admin_to_emp_avg = float(target_scores.mean())
                    emp_to_admin_avg = float(emp_to_admin.mean())
                    if admin_to_emp_avg >= 82 and emp_to_admin_avg >= 82:
                        insights.append(
                            f"⚠ BOUNDARY RISK: Admin {admin} and {employee} exchange unusually high mutual ratings ({admin_to_emp_avg:.0f}% / {emp_to_admin_avg:.0f}%)"
                        )

    if warnings_df is not None and not warnings_df.empty and {"username", "message"}.issubset(warnings_df.columns):
        warn_work = warnings_df.copy()
        warn_work["message"] = warn_work["message"].astype(str)
        warn_work["username"] = warn_work["username"].astype(str).str.strip()
        warn_work["sender_guess"] = warn_work["message"].str.extract(r"\[From\s+([^\]]+)\]", expand=False).fillna("").astype(str).str.strip()
        warn_work = warn_work[warn_work["sender_guess"].isin(admins)]

        for (sender_guess, target_user), group in warn_work.groupby(["sender_guess", "username"]):
            if len(group) >= 3:
                insights.append(
                    f"🚨 DISCIPLINE TARGETING RISK: Admin {sender_guess} issued {len(group)} warnings to {target_user} - review for repeated pressure or retaliation"
                )

    if leaves_df is not None and not leaves_df.empty and {"username", "status", "approved_by"}.issubset(leaves_df.columns):
        leave_work = leaves_df.copy()
        leave_work["username"] = leave_work["username"].astype(str).str.strip()
        leave_work["status"] = leave_work["status"].astype(str).str.lower().str.strip()
        leave_work["approved_by"] = leave_work["approved_by"].fillna("").astype(str).str.strip()
        leave_work = leave_work[leave_work["approved_by"].isin(admins)]
        adverse = leave_work[leave_work["status"].isin(["rejected", "reapply"])]

        for (approver, target_user), group in adverse.groupby(["approved_by", "username"]):
            if len(group) >= 2:
                total_reviews = len(leave_work[leave_work["approved_by"] == approver])
                if total_reviews and (len(group) / total_reviews) >= 0.4:
                    insights.append(
                        f"⚠ LEAVE PRESSURE RISK: Admin {approver} made {len(group)} adverse leave decisions for {target_user} - review for fairness or retaliation"
                    )

    return list(set(insights))


# =====================================================
# 🔥 EMPLOYEE-TO-EMPLOYEE GANG-UP / TARGETING DETECTION
# =====================================================
def detect_peer_gangup_and_targeting(ratings_df, users_df=None):
    """Detects peer targeting, clique favoritism, and gang-up behavior among non-admin staff."""

    insights = []

    if ratings_df is None or ratings_df.empty:
        return insights

    ratings_work = ratings_df.copy()
    ratings_work["rater"] = ratings_work["rater"].astype(str).str.strip()
    ratings_work["rated"] = ratings_work["rated"].astype(str).str.strip()
    ratings_work["score"] = pd.to_numeric(ratings_work["score"], errors="coerce")
    ratings_work = ratings_work.dropna(subset=["score"])

    non_admin_users = None
    if users_df is not None and not users_df.empty and {"username", "role"}.issubset(users_df.columns):
        role_norm = users_df["role"].astype(str).str.lower()
        non_admin_users = set(
            users_df[~role_norm.isin(["admin", "manager", "superadmin", "super_admin", "master"])]["username"]
            .dropna()
            .astype(str)
            .str.strip()
            .tolist()
        )
        ratings_work = ratings_work[
            ratings_work["rater"].isin(non_admin_users) & ratings_work["rated"].isin(non_admin_users)
        ]

    if ratings_work.empty:
        return insights

    for target in ratings_work["rated"].unique():
        target_rows = ratings_work[ratings_work["rated"] == target]
        low_rows = target_rows[target_rows["score"] < 45]
        low_raters = sorted(set(low_rows["rater"].astype(str)))
        total_raters = int(target_rows["rater"].nunique())

        if len(low_raters) >= 3:
            joined = ", ".join(low_raters[:5])
            insights.append(
                f"🚨 PEER GANG-UP RISK: {target} is being rated very low by {len(low_raters)} peers ({joined})"
            )
        elif total_raters >= 4 and len(low_raters) >= max(2, int(total_raters * 0.6)):
            joined = ", ".join(low_raters[:5])
            insights.append(
                f"⚠ PEER TARGETING CLUSTER: {target} receives concentrated low ratings from {len(low_raters)}/{total_raters} peers ({joined})"
            )

    for rater in ratings_work["rater"].unique():
        rater_rows = ratings_work[ratings_work["rater"] == rater]
        if rater_rows.empty:
            continue

        low_targets = rater_rows[rater_rows["score"] < 40]["rated"].value_counts()
        for target, count in low_targets.items():
            if int(count) >= 2:
                insights.append(
                    f"⚠ REPEATED PEER TARGETING: {rater} repeatedly rates {target} very low ({int(count)} times)"
                )

        high_targets = rater_rows[rater_rows["score"] > 85]["rated"].value_counts()
        for target, count in high_targets.items():
            if int(count) >= 3:
                mutual_scores = ratings_work[(ratings_work["rater"] == target) & (ratings_work["rated"] == rater)]["score"]
                if not mutual_scores.empty and float(mutual_scores.mean()) > 80:
                    insights.append(
                        f"⚠ PEER CLIQUE / MUTUAL FAVORITISM: {rater} and {target} exchange unusually high scores"
                    )

    return list(set(insights))


# =====================================================
# 🔥 ISOLATION & LOW PERFORMER DETECTION
# =====================================================
def detect_isolation_and_low_performers(ratings_df, users_df=None):
    """Detects isolated employees and critical performance issues."""
    
    insights = []
    
    if ratings_df.empty:
        return insights
    
    avg_scores = ratings_df.groupby("rated")["score"].mean()
    
    # RULE 1: Person rated very low by everyone (isolation)
    for employee in avg_scores.index:
        emp_ratings = ratings_df[ratings_df["rated"] == employee]
        low_count = len(emp_ratings[emp_ratings["score"] < 45])
        total_count = len(emp_ratings)
        
        if total_count >= 3 and low_count == total_count:
            insights.append(f"🚨 ISOLATION DETECTED: {employee} rated low (<45%) by EVERYONE ({total_count} raters) - social exclusion risk")
        elif total_count >= 3 and low_count >= (total_count * 0.7):
            insights.append(f"⚠ {employee} rated <45% by {low_count}/{total_count} raters - possible isolation")
    
    # RULE 2: Multiple peers rate one person very low (<45%) in same topics
    for employee in ratings_df["rated"].unique():
        emp_ratings = ratings_df[ratings_df["rated"] == employee]
        very_low = emp_ratings[emp_ratings["score"] < 45]
        
        if len(very_low) >= 3:
            insights.append(f"🚨 CRITICAL PERFORMANCE: {employee} rated <45% by {len(very_low)} peers - immediate management action required")
    
    # RULE 3: One person rates employee low, rest rate high (conflict, not isolation)
    for employee in ratings_df["rated"].unique():
        emp_ratings = ratings_df[ratings_df["rated"] == employee]
        low_ratings = emp_ratings[emp_ratings["score"] < 45]
        high_ratings = emp_ratings[emp_ratings["score"] > 52]
        
        if len(low_ratings) == 1 and len(high_ratings) >= (len(emp_ratings) - 1):
            lone_rater = low_ratings.iloc[0]["rater"]
            insights.append(f"⚠ CONFLICT (NOT PERFORMANCE): {lone_rater} rates {employee} low (~45%) but rest rate >52% - personal conflict, not performance issue")
    
    # RULE 4: Admin rates employee way higher than peers (critical discrepancy)
    if users_df is not None:
        admins = users_df[users_df["role"].isin(["admin", "manager"])]["username"].unique()
        
        for employee in ratings_df["rated"].unique():
            emp_ratings = ratings_df[ratings_df["rated"] == employee]
            peer_ratings = emp_ratings[~emp_ratings["rater"].isin(admins)]
            admin_ratings = emp_ratings[emp_ratings["rater"].isin(admins)]
            
            if not peer_ratings.empty and not admin_ratings.empty:
                peer_avg = peer_ratings["score"].mean()
                admin_avg = admin_ratings["score"].mean()
                
                if peer_avg <= 49 and (admin_avg - peer_avg) >= 15:
                    insights.append(f"🚨 CRITICAL: {employee} rated {peer_avg:.0f}% by peers but {admin_avg:.0f}% by admin - possible admin bias hiding real issue")
    
    return list(set(insights))


# =====================================================
# 🔥 BAD SEED / TOXIC INFLUENCE DETECTION
# =====================================================
def detect_bad_seed_toxic_influence(ratings_df, messages_df=None):
    """Detects toxic influencers and bad seeds in the team."""
    
    insights = []
    
    if ratings_df.empty:
        return insights
    
    avg_given = ratings_df.groupby("rater")["score"].mean()
    avg_received = ratings_df.groupby("rated")["score"].mean()
    
    # RULE 1: Bad seed - gives low ratings while rating self high (35-55% avg, but rates self high or rates supporters high)
    for rater in ratings_df["rater"].unique():
        rater_avg = avg_given[rater]
        
        if 35 <= rater_avg <= 55:
            # This person rates low (bad seed range)
            their_ratings = ratings_df[ratings_df["rater"] == rater]
            very_low = their_ratings[their_ratings["score"] < 40]
            high_given = their_ratings[their_ratings["score"] > 78]
            
            if len(very_low) >= 3 and len(high_given) >= 2:
                insights.append(f"🚨 BAD SEED DETECTED: {rater} (avg rating {rater_avg:.0f}%) gives very low ratings (<40% x{len(very_low)}) but high rates select few - toxic influencer")
                
                # Check if bad seed is in a counter-group
                high_raters = high_given["rated"].unique()
                counter_group_avg = ratings_df[ratings_df["rater"].isin(high_raters)]["score"].mean()
                
                if counter_group_avg > 78:
                    insights.append(f"  └─ {rater} is in TOXIC GROUP with {', '.join(high_raters)} (rates themselves high >78%)")
    
    # RULE 2: Measure if team has bad seed vs good performers (35-55% vs 75-90%)
    bad_seeds = avg_given[(avg_given >= 35) & (avg_given <= 55)]
    good_performers = avg_received[avg_received >= 75]
    
    if len(bad_seeds) >= 1 and len(good_performers) >= 2:
        insights.append(f"⚠ TOXIC DYNAMICS: {len(bad_seeds)} bad seed(s) rating mostly low while {len(good_performers)} high performers exist - team morale at risk")
    
    # RULE 3: Message frequency indicates bad seed (sends many messages)
    if messages_df is not None and not messages_df.empty:
        msg_counts = messages_df["from_user"].value_counts()
        
        for sender, count in msg_counts.items():
            if count > 10:  # High message volume
                sender_rating_avg = avg_given.get(sender, 75)
                
                if sender_rating_avg <= 55:
                    insights.append(f"🚨 BAD SEED + HIGH ACTIVITY: {sender} sends {count} messages + rates low ({sender_rating_avg:.0f}%) - likely complaining/negative influence")
    
    return list(set(insights))


# =====================================================
# 🏢 TEAM HEALTH SCORING
# =====================================================
def calculate_team_health_score(ratings_df):
    """
    Calculates overall team health (65% = unhealthy, 75% = healthy).
    """
    
    if ratings_df.empty:
        return 0
    
    overall_avg = ratings_df["score"].mean()
    
    health_status = ""
    if overall_avg < 65:
        health_status = "🚨 UNHEALTHY TEAM"
    elif overall_avg >= 75:
        health_status = "✅ HEALTHY TEAM"
    else:
        health_status = "⚠ MODERATE TEAM HEALTH"
    
    return overall_avg, health_status
