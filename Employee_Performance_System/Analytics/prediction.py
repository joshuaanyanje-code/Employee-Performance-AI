import pandas as pd
from datetime import datetime, timedelta


def predict_future(ratings_df, attendance_df=None, users_df=None):

    insights = []

    if ratings_df.empty:
        return insights

    # =========================
    # PREP
    # =========================
    avg_scores = ratings_df.groupby("rated")["score"].mean()
    summary = (
        ratings_df.groupby("rated")
        .agg(
            avg_score=("score", "mean"),
            score_std=("score", "std"),
            rating_count=("score", "count"),
            rater_count=("rater", "nunique"),
        )
        .reset_index()
        .rename(columns={"rated": "employee"})
    )
    summary["score_std"] = summary["score_std"].fillna(0)
    total_raters = max(ratings_df["rater"].astype(str).nunique(), 1)
    summary["coverage_ratio"] = summary["rater_count"] / total_raters

    # =========================
    # ORIGINAL LOGIC (KEPT ✅)
    # =========================
    promotion_pick = summary[
        (summary["avg_score"] >= 85) & (summary["rating_count"] >= 3) & (summary["score_std"] <= 12)
    ].sort_values(["coverage_ratio", "avg_score", "score_std", "rating_count"], ascending=[False, False, True, False]).head(1)

    selected_top_name = None
    if not promotion_pick.empty:
        row = promotion_pick.iloc[0]
        selected_top_name = str(row["employee"])
        insights.append(
            f"📈 Promotion candidate: {selected_top_name} consistently scores well across team raters ({row['avg_score']:.1f}%)."
        )

    risk = avg_scores[avg_scores < 45]
    for name in risk.index:
        insights.append(f"🚪 Possible resignation risk: {name}")

    conflicts = ratings_df[ratings_df["score"] < 35]
    grouped = conflicts.groupby(["rater", "rated"]).size()

    for pair in grouped.index:
        insights.append(f"⚠ Conflict escalation risk between {pair[0]} and {pair[1]}")

    # =====================================================
    # 🔥 QUIT PREDICTION ENGINE
    # =====================================================
    if attendance_df is not None and not attendance_df.empty:

        attendance_df["clock_in"] = pd.to_datetime(attendance_df["clock_in"], errors="coerce")
        attendance_df["clock_out"] = pd.to_datetime(attendance_df["clock_out"], errors="coerce")
        attendance_df["date"] = pd.to_datetime(attendance_df["date"], errors="coerce")

        current_month = datetime.now().month

        for user in attendance_df["username"].unique():

            df = attendance_df[
                (attendance_df["username"] == user) &
                (attendance_df["date"].dt.month == current_month)
            ]

            late = len(df[df["clock_in"].dt.hour > 9])
            early = len(df[df["clock_out"].dt.hour < 18])
            days_present = df["date"].nunique()
            total_days = datetime.now().day
            absent = total_days - days_present

            score = avg_scores.get(user, 0)

            # 🔥 STRONG QUIT SIGNAL
            if score < 55 and (late >= 3 or absent >= 2 or early >= 3):
                insights.append(f"🚨 {user} likely to quit soon (low performance + bad attendance)")

            # 🔥 SILENT QUITTING
            if late >= 3 and early >= 3:
                insights.append(f"⚠ {user} shows silent quitting behavior")

    # =====================================================
    # 👥 PAIR QUITTING (RELATIONSHIP)
    # =====================================================
    strong_links = ratings_df[ratings_df["score"] > 85]

    pairs = strong_links.groupby(["rater", "rated"]).size()

    for (r1, r2), count in pairs.items():
        if count >= 3:
            if avg_scores.get(r1, 0) < 55 and avg_scores.get(r2, 0) < 55:
                insights.append(f"👥 {r1} & {r2} may quit together (strong link + low morale)")

    # =====================================================
    # 📈 NEXT TOP PERFORMER (TREND)
    # =====================================================
    if "created_at" in ratings_df.columns:

        ratings_df["created_at"] = pd.to_datetime(ratings_df["created_at"], errors="coerce")

        recent = ratings_df[ratings_df["created_at"] >= datetime.now() - timedelta(days=14)]

        if not recent.empty:
            recent_summary = (
                recent.groupby("rated")
                .agg(
                    avg_score=("score", "mean"),
                    score_std=("score", "std"),
                    rating_count=("score", "count"),
                    rater_count=("rater", "nunique"),
                )
                .reset_index()
                .rename(columns={"rated": "employee"})
            )
            recent_summary["score_std"] = recent_summary["score_std"].fillna(0)
            total_recent_raters = max(recent["rater"].astype(str).nunique(), 1)
            recent_summary["coverage_ratio"] = recent_summary["rater_count"] / total_recent_raters

            rising = recent_summary[
                (recent_summary["rating_count"] >= 3) & (recent_summary["avg_score"] >= 70)
            ].sort_values(["coverage_ratio", "avg_score", "score_std", "rating_count"], ascending=[False, False, True, False]).head(1)

            if not rising.empty:
                next_name = str(rising.iloc[0]["employee"])
                if next_name != selected_top_name:
                    insights.append(
                        f"📈 {next_name} is the strongest next top performer based on recent consistent ratings from the team."
                    )

    # =====================================================
    # 👑 ADMIN RELIABILITY
    # =====================================================
    if users_df is not None:

        admins = users_df[users_df["role"] == "admin"]["username"]

        for admin in admins:

            score = avg_scores.get(admin, 0)

            if score >= 75:
                insights.append(f"👑 {admin} is a reliable admin (stable leadership)")

            elif score < 55:
                insights.append(f"🚨 {admin} is an unreliable admin (risk to branch)")

    # =====================================================
    # 🏢 BRANCH FUTURE
    # =====================================================
    if "branch" in ratings_df.columns:

        branch_scores = ratings_df.groupby("branch")["score"].mean()

        for branch, score in branch_scores.items():

            if score >= 75:
                insights.append(f"📊 {branch} likely to grow (strong team + leadership)")

            elif score < 55:
                insights.append(f"⚠ {branch} at risk of collapse (low performance trends)")

    # =====================================================
    # ⚠ CONFLICT / BIAS TREND
    # =====================================================
    low_scores = ratings_df[ratings_df["score"] < 40]

    if not low_scores.empty:
        insights.append("⚠ Conflict trend increasing in team")

    high_scores = ratings_df[ratings_df["score"] > 85]

    if len(high_scores) > len(ratings_df) * 0.6:
        insights.append("⚠ Favoritism trend increasing (ratings too high)")

    # =====================================================
    # 💰 BUSINESS GROWTH SIGNAL
    # =====================================================
    overall = ratings_df["score"].mean()

    if overall >= 75:
        insights.append("💰 Business growth likely strong")

    elif overall < 55:
        insights.append("⚠ Business performance declining")

    return list(set(insights))


# =====================================================
# 🔥 PSYCHOLOGICAL WITHDRAWAL / QUIET QUITTING
# =====================================================
def detect_psychological_withdrawal(ratings_df):
    """Detects employees withdrawing psychologically (likely to quit)."""
    
    insights = []
    
    if ratings_df.empty or "created_at" not in ratings_df.columns:
        return insights
    
    ratings_df = ratings_df.copy()
    ratings_df["created_at"] = pd.to_datetime(ratings_df["created_at"], errors="coerce")
    
    for employee in ratings_df["rated"].unique():
        emp_ratings = ratings_df[ratings_df["rated"] == employee].sort_values("created_at")
        
        if len(emp_ratings) < 3:
            continue
        
        # RULE 1: Rates everyone in exact patterns (60, 70, 80, 90)
        scores = emp_ratings["score"].values
        unique_scores = set(scores)
        
        if unique_scores.issubset({60, 70, 80, 90}) and len(scores) >= 5:
            insights.append(f"⚠ PSYCHOLOGICAL WITHDRAWAL: {employee} uses repetitive pattern ratings (60,70,80,90) - 'I don't care' attitude = quit risk")
        
        # RULE 2: Volatile score pattern + bad seed behavior
        recent_5 = emp_ratings.tail(5)
        if len(recent_5) >= 4:
            score_range = recent_5["score"].max() - recent_5["score"].min()
            if score_range >= 40:
                insights.append(f"⚠ VOLATILE RATINGS: {employee} fluctuates wildly ({recent_5['score'].min():.0f}%-{recent_5['score'].max():.0f}%) - unstable/withdrawing")
        
        # RULE 3: Rates others very low while inconsistent with self
        emp_to_others = ratings_df[ratings_df["rater"] == employee]["score"]
        if not emp_to_others.empty:
            gave_low = len(emp_to_others[emp_to_others < 40])
            if gave_low >= 3 and scores.mean() > 65:
                insights.append(f"🚨 WITHDRAWAL SIGNAL: {employee} gives very low ratings to others but receives high - possible retaliation/withdrawal")
    
    return list(set(insights))


# =====================================================
# 👨‍👩‍👧 FAMILY ISSUE / PERSONAL CRISIS DETECTION
# =====================================================
def detect_family_issues_and_personal_crisis(ratings_df, attendance_df=None, messages_df=None):
    """Detects employees going through personal crisis or family issues."""
    
    insights = []
    
    if ratings_df.empty or "created_at" not in ratings_df.columns:
        return insights
    
    ratings_df = ratings_df.copy()
    ratings_df["created_at"] = pd.to_datetime(ratings_df["created_at"], errors="coerce")
    
    avg_received = ratings_df.groupby("rated")["score"].mean()
    
    for employee in ratings_df["rated"].unique():
        emp_ratings = ratings_df[ratings_df["rated"] == employee].sort_values("created_at")
        
        if len(emp_ratings) < 3:
            continue
        
        # RULE 1: Volatile scores (high one week, low next week) + late/early
        if attendance_df is not None and not attendance_df.empty:
            attendance_df_copy = attendance_df.copy()
            attendance_df_copy["clock_in"] = pd.to_datetime(attendance_df_copy["clock_in"], errors="coerce")
            attendance_df_copy["clock_out"] = pd.to_datetime(attendance_df_copy["clock_out"], errors="coerce")
            
            emp_att = attendance_df_copy[attendance_df_copy["username"] == employee]
            late_count = len(emp_att[emp_att["clock_in"].dt.hour > 9])
            early_count = len(emp_att[emp_att["clock_out"].dt.hour < 18])
            
            # Check score volatility
            if len(emp_ratings) >= 4:
                recent_2 = emp_ratings.tail(2)
                prev_2 = emp_ratings.iloc[-4:-2] if len(emp_ratings) >= 4 else pd.DataFrame()
                
                if not prev_2.empty and not recent_2.empty:
                    prev_avg = prev_2["score"].mean()
                    recent_avg = recent_2["score"].mean()
                    
                    if abs(recent_avg - prev_avg) >= 20 and (late_count >= 3 or early_count >= 3):
                        insights.append(f"👨‍👩‍👧 FAMILY ISSUE SUSPECTED: {employee} - volatile scores + late/early departures - possible personal crisis")
        
        # RULE 2: Last to submit rating + high messages to management
        if messages_df is not None and not messages_df.empty:
            emp_msgs = messages_df[messages_df["from_user"] == employee]
            if len(emp_msgs) >= 5:
                is_last_rater = emp_ratings.iloc[-1]["created_at"] == ratings_df["created_at"].max()
                if is_last_rater:
                    insights.append(f"⚠ POSSIBLE PERSONAL ISSUE: {employee} - submits ratings last + sends {len(emp_msgs)} messages to management (seeking help/complaints)")
        
        # RULE 3: High rating volatility + rates others very high (75%+)
        given_high = ratings_df[(ratings_df["rater"] == employee) & (ratings_df["score"] > 75)]
        if len(emp_ratings) >= 4:
            variance = emp_ratings["score"].std()
            if variance > 15 and len(given_high) >= (len(ratings_df[ratings_df["rater"] == employee]) * 0.6):
                insights.append(f"⚠ FAMILY/PERSONAL STRESS: {employee} - high rating variance + overcompensates by rating others high - emotional instability")
    
    return list(set(insights))


# =====================================================
# 🔄 CONFLICT RESOLUTION DETECTION
# =====================================================
def detect_conflict_resolution_and_reconciliation(ratings_df):
    """Detects when conflicts are being resolved or escalating."""
    
    insights = []
    
    if ratings_df.empty or "created_at" not in ratings_df.columns:
        return insights
    
    ratings_df = ratings_df.copy()
    ratings_df["created_at"] = pd.to_datetime(ratings_df["created_at"], errors="coerce")
    
    # Check all pairs
    for rater in ratings_df["rater"].unique():
        for rated in ratings_df["rated"].unique():
            if rater == rated:
                continue
            
            pair_ratings = ratings_df[(ratings_df["rater"] == rater) & (ratings_df["rated"] == rated)].sort_values("created_at")
            
            if len(pair_ratings) < 3:
                continue
            
            # Get last two ratings
            last_two = pair_ratings.tail(2)
            prev_two = pair_ratings.iloc[-4:-2] if len(pair_ratings) >= 4 else pd.DataFrame()
            
            if not prev_two.empty and len(last_two) == 2:
                prev_avg = prev_two["score"].mean()
                current_avg = last_two["score"].mean()
                
                change = current_avg - prev_avg
                
                # RULE 1: 20% decrease = conflict escalation
                if change <= -20 and prev_avg >= 65:
                    insights.append(f"⚠ CONFLICT ESCALATION: {rater} → {rated} dropped {abs(change):.0f}% (from {prev_avg:.0f}% to {current_avg:.0f}%)")
                
                # RULE 2: 20%+ increase after low ratings = reconciliation
                elif change >= 20 and prev_avg <= 45:
                    insights.append(f"✅ RECONCILIATION DETECTED: {rater} & {rated} - ratings improved {change:.0f}% (from {prev_avg:.0f}% to {current_avg:.0f}%) after conflict")
    
    return list(set(insights))


# =====================================================
# 🤝 COOPERATION & RATING TIMING DETECTION
# =====================================================
def detect_cooperation_and_timing(ratings_df):
    """Detects branch cooperation based on rating submission timing."""
    
    insights = []
    
    if ratings_df.empty or "created_at" not in ratings_df.columns:
        return insights
    
    ratings_df = ratings_df.copy()
    ratings_df["created_at"] = pd.to_datetime(ratings_df["created_at"], errors="coerce")
    
    # Get latest rating round
    latest_date = ratings_df["created_at"].max()
    latest_round = ratings_df[ratings_df["created_at"].dt.date == latest_date.date()]
    
    if latest_round.empty:
        return insights
    
    submissions = latest_round.sort_values("created_at")
    
    # Check timing patterns
    cooperative_count = 0
    late_count = 0
    
    for i in range(len(submissions) - 1):
        time1 = submissions.iloc[i]["created_at"]
        time2 = submissions.iloc[i + 1]["created_at"]
        
        time_diff = (time2 - time1).total_seconds() / 60  # minutes
        
        # Near beginning = cooperative
        if i < 3 and time_diff < 60:
            cooperative_count += 1
        
        # Very late (1+ hour after second last) = non-cooperative
        if i >= len(submissions) - 3 and time_diff > 60:
            late_count += 1
    
    if cooperative_count >= (len(submissions) * 0.6):
        insights.append(f"✅ COOPERATIVE BRANCH: Most ratings submitted quickly when round opened - team is engaged")
    elif late_count >= 2:
        insights.append(f"⚠ NON-COOPERATIVE: {late_count} raters submitted very late (1+ hour delay) - possible disengagement or rush")
    
    return list(set(insights))


# =====================================================
# 📊 TEAM ALIGNMENT & CONSISTENCY
# =====================================================
def detect_team_alignment(ratings_df):
    """Detects if team rates consistently (good alignment) or not (poor alignment)."""
    
    insights = []
    
    if ratings_df.empty:
        return insights
    
    # Check consistency of ratings for each subject
    consistency_issues = []
    high_alignment = []
    
    for employee in ratings_df["rated"].unique():
        emp_ratings = ratings_df[ratings_df["rated"] == employee]["score"]
        
        if len(emp_ratings) < 3:
            continue
        
        variance = emp_ratings.std()
        variance_pct = (emp_ratings.max() - emp_ratings.min())
        
        # RULE 1: If all ratings are similar (10% or less difference) = team players / aligned
        if variance_pct <= 10:
            high_alignment.append(employee)
        
        # RULE 2: High variance (30%+) = disagreement / not aligned
        elif variance_pct >= 30:
            consistency_issues.append((employee, variance_pct))
    
    if len(high_alignment) >= (len(ratings_df["rated"].unique()) * 0.7):
        insights.append(f"✅ TEAM ALIGNMENT: {len(high_alignment)} employees rated consistently (±10% variance) - team players, well aligned")
    
    if consistency_issues:
        for employee, var in consistency_issues[:3]:
            insights.append(f"⚠ DISAGREEMENT on {employee}: {var:.0f}% rating variance - raters don't agree on performance")
    
    return list(set(insights))
