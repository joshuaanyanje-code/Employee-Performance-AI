import pandas as pd
from datetime import datetime, timedelta


def _build_consistency_summary(ratings_df, recent_days=None):
    if ratings_df is None or ratings_df.empty:
        return pd.DataFrame()

    df = ratings_df.copy()
    if recent_days is not None and "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
        df = df[df["created_at"] >= datetime.now() - timedelta(days=recent_days)]

    if df.empty:
        return pd.DataFrame()

    summary = (
        df.groupby("rated")
        .agg(
            avg_score=("score", "mean"),
            score_std=("score", "std"),
            rating_count=("score", "count"),
            rater_count=("rater", "nunique"),
            min_score=("score", "min"),
            max_score=("score", "max"),
        )
        .reset_index()
        .rename(columns={"rated": "employee"})
    )

    summary["score_std"] = summary["score_std"].fillna(0)
    total_raters = max(df["rater"].astype(str).nunique(), 1)
    summary["coverage_ratio"] = summary["rater_count"] / total_raters
    return summary.sort_values(
        ["coverage_ratio", "avg_score", "score_std", "rating_count"],
        ascending=[False, False, True, False],
    ).reset_index(drop=True)


def _compute_monthly_leader_attendance_summary(attendance_df, username):
    if attendance_df is None or attendance_df.empty:
        return {
            "grace_checkins": 0,
            "late_checkouts": 0,
            "early_exits": 0,
            "lateness_requests": 0,
        }

    df = attendance_df.copy()
    df["date"] = pd.to_datetime(df.get("date"), errors="coerce")
    df["clock_in"] = pd.to_datetime(df.get("clock_in"), errors="coerce")
    df["clock_out"] = pd.to_datetime(df.get("clock_out"), errors="coerce")
    df = df[df["username"] == username]
    df = df[df["date"].dt.month == datetime.now().month]

    grace_checkins = len(
        df[
            (df["clock_in"].dt.hour == 9)
            & (df["clock_in"].dt.minute <= 15)
        ]
    )

    late_checkouts = len(df[df["clock_out"].dt.hour > 18])
    early_exits = len(df[df["clock_out"].dt.hour < 18])

    lateness_requests = 0
    if "lateness_request_status" in df.columns:
        lateness_requests = len(
            df[
                df["lateness_request_status"].astype(str).str.lower().isin(
                    ["pending", "approved", "used"]
                )
            ]
        )
    elif "approved_late" in df.columns:
        lateness_requests = int(df["approved_late"].fillna(False).astype(bool).sum())

    return {
        "grace_checkins": grace_checkins,
        "late_checkouts": late_checkouts,
        "early_exits": early_exits,
        "lateness_requests": lateness_requests,
    }


def detect_leaders(ratings_df, attendance_df=None, leaves_df=None, users_df=None, messages_df=None):

    if ratings_df.empty:
        return pd.DataFrame(), []

    insights = []

    # =========================
    # BASE LEADERBOARD (CONSISTENCY-AWARE)
    # =========================
    summary = _build_consistency_summary(ratings_df)
    if summary.empty:
        return pd.DataFrame(), []

    leaders = summary[["employee", "avg_score"]].rename(columns={"avg_score": "leadership_score"})

    # =====================================================
    # 🏆 TOP PERFORMER PER BRANCH (>=91)
    # =====================================================
    if "branch" in ratings_df.columns:

        branch_scores = ratings_df.groupby(["branch", "rated"])["score"].mean().reset_index()

        for branch in branch_scores["branch"].unique():

            df = branch_scores[branch_scores["branch"] == branch]

            top = df.sort_values("score", ascending=False).head(1)

            if not top.empty and top.iloc[0]["score"] >= 91:
                insights.append(
                    f"🏆 {top.iloc[0]['rated']} is top performer in {branch} ({round(top.iloc[0]['score'],1)})"
                )

    # =====================================================
    # 📈 CONSISTENCY / NEXT TOP PERFORMER
    # =====================================================
    recent_summary = _build_consistency_summary(ratings_df, recent_days=30)
    if not recent_summary.empty:
        recent_candidate = recent_summary[
            (recent_summary["avg_score"] >= 75) & (recent_summary["rating_count"] >= 3)
        ].head(1)
        if not recent_candidate.empty:
            row = recent_candidate.iloc[0]
            insights.append(
                f"🏆 Next top performer: {row['employee']} ({row['avg_score']:.1f}%) with consistent ratings from {int(row['rater_count'])} raters."
            )

    # =====================================================
    # 🚀 PROMOTION LOGIC (5 CONSISTENT HIGH SCORES)
    # =====================================================
    promotion_candidates = []
    for user in ratings_df["rated"].unique():

        df = ratings_df[ratings_df["rated"] == user].sort_values("created_at")
        last5 = df.tail(5)

        if len(last5) == 5:
            scores = last5["score"].values
            if all(s >= 85 for s in scores):
                drop = max(scores) - min(scores)
                if drop <= 10:
                    promotion_candidates.append({
                        "employee": user,
                        "avg_score": float(last5["score"].mean()),
                        "spread": float(drop),
                    })

    if promotion_candidates:
        promo_df = pd.DataFrame(promotion_candidates).sort_values(
            ["avg_score", "spread", "employee"], ascending=[False, True, True]
        )
        best_promo = promo_df.iloc[0]
        insights.append(
            f"🚀 Promotion-ready performer: {best_promo['employee']} ({best_promo['avg_score']:.1f}%) with very consistent recent ratings."
        )

    # =====================================================
    # 👑 ADMIN PERFORMANCE
    # =====================================================
    if users_df is not None:

        admins = users_df[users_df["role"] == "admin"]["username"]

        admin_scores = leaders[leaders["employee"].isin(admins)]

        for _, row in admin_scores.iterrows():

            name = row["employee"]
            score = row["leadership_score"]

            if score < 55:
                insights.append(f"🚨 Admin {name} is WEAK (below 55)")

            elif 60 <= score < 75:
                insights.append(f"👍 Admin {name} is doing OK but needs improvement")

            elif score >= 75:
                insights.append(f"👑 Admin {name} is performing well")

        if attendance_df is not None and not attendance_df.empty:
            for admin in admins.unique():
                attendance_flags = _compute_monthly_leader_attendance_summary(attendance_df, admin)
                if (
                    attendance_flags["grace_checkins"] < 4
                    and attendance_flags["late_checkouts"] > 6
                    and attendance_flags["early_exits"] <= 1
                    and attendance_flags["lateness_requests"] <= 1
                ):
                    insights.append(
                        f"⚠ Admin {admin} leadership alert: very few monthly grace check-ins ({attendance_flags['grace_checkins']}), "
                        f"many after-hours check-outs ({attendance_flags['late_checkouts']}), and low formal request activity ({attendance_flags['lateness_requests']})."
                    )

    # =====================================================
    # 🏢 BRANCH STABILITY SCORE
    # =====================================================
    if all(x is not None for x in [attendance_df, leaves_df, users_df]):

        if not attendance_df.empty:

            attendance_df["clock_in"] = pd.to_datetime(attendance_df["clock_in"], errors="coerce")

        for branch in ratings_df["branch"].unique():

            branch_users = users_df[users_df["branch"] == branch]["username"]

            # --- attendance ---
            att = attendance_df[attendance_df["username"].isin(branch_users)] if attendance_df is not None else pd.DataFrame()

            late = 0
            if not att.empty:
                late = len(att[att["clock_in"].dt.hour > 9])

            # --- leaves ---
            lv = leaves_df[leaves_df["branch"] == branch] if leaves_df is not None else pd.DataFrame()
            leave_count = len(lv)

            # --- admin score ---
            admins = users_df[(users_df["branch"] == branch) & (users_df["role"] == "admin")]["username"]
            admin_scores = leaders[leaders["employee"].isin(admins)]

            avg_admin = admin_scores["leadership_score"].mean() if not admin_scores.empty else 0

            # --- messaging ---
            msg_count = 0
            if messages_df is not None and not messages_df.empty:
                msg_count = len(messages_df[messages_df["branch"] == branch])

            # =========================
            # DECISION
            # =========================
            if late < 5 and leave_count < 5 and avg_admin >= 70 and msg_count < 10:
                insights.append(f"🏢 {branch} is STABLE and well managed")

            elif late > 10 or leave_count > 10:
                insights.append(f"⚠ {branch} shows instability (attendance/leaves issue)")

            elif avg_admin < 55:
                insights.append(f"🚨 {branch} unstable due to weak leadership")

            elif msg_count > 20:
                insights.append(f"⚠ {branch} has high internal issues (too many messages/conflicts)")

    return leaders.head(10), list(set(insights))


# =====================================================
# 👑 POPULAR INFLUENCER / NEUTRAL LEADER DETECTION
# =====================================================
def detect_popular_influencers(ratings_df, users_df=None, attendance_df=None):
    """Detects natural leaders/influencers who are neutral, consistent, and respected."""
    
    insights = []
    
    if ratings_df.empty:
        return insights
    
    # Criteria for popular influencer:
    # - Rated 70-80% by everyone (consistent, respected)
    # - Not in any groups/bias
    # - Low variance (≤15%) in ratings
    # - Rates others 60-70% (neutral, fair)
    # - No absenteeism/lateness for 4+ weeks
    
    avg_received = ratings_df.groupby("rated")["score"].mean()
    
    for employee in avg_received.index:
        emp_ratings_received = ratings_df[ratings_df["rated"] == employee]
        emp_ratings_given = ratings_df[ratings_df["rater"] == employee]
        
        # Check 1: Received 70-80% range
        avg_received_score = emp_ratings_received["score"].mean()
        variance = emp_ratings_received["score"].std()
        
        if 70 <= avg_received_score <= 80 and variance <= 15:
            # Check 2: Rates others fairly (60-70%)
            if not emp_ratings_given.empty:
                given_avg = emp_ratings_given["score"].mean()
                
                if 60 <= given_avg <= 70:
                    # Check 3: Not in groups (no extreme biases)
                    high_given = len(emp_ratings_given[emp_ratings_given["score"] > 85])
                    low_given = len(emp_ratings_given[emp_ratings_given["score"] < 45])
                    
                    if high_given <= 1 and low_given <= 1:
                        insights.append(f"👑 POPULAR INFLUENCER: {employee} - respected ({avg_received_score:.0f}%), fair rater ({given_avg:.0f}%), neutral (no extremes)")
    
    return list(set(insights))


# =====================================================
# 🏆 TOP PERFORMER CONSISTENCY (7% VARIANCE)
# =====================================================
def detect_elite_top_performers(ratings_df):
    """Detects the single strongest consistent top performer."""
    
    if ratings_df.empty:
        return []

    summary = _build_consistency_summary(ratings_df)
    if summary.empty:
        return []

    elite = summary[(summary["avg_score"] >= 85) & (summary["rating_count"] >= 3)].copy()
    if elite.empty:
        return []

    elite["spread"] = elite["max_score"] - elite["min_score"]
    elite = elite.sort_values(["coverage_ratio", "avg_score", "spread", "score_std"], ascending=[False, False, True, True])
    best = elite.iloc[0]

    if best["spread"] <= 7:
        return [
            f"🏆 ELITE TOP PERFORMER: {best['employee']} - Consistent excellence ({best['avg_score']:.0f}%) rated broadly across the team."
        ]

    return [
        f"⭐ TOP PERFORMER: {best['employee']} - Strong consistency ({best['avg_score']:.0f}%) and broad team recognition."
    ]


# =====================================================
# 🚨 BAD ADMIN / MANAGER DETECTION
# =====================================================
def detect_bad_managers(ratings_df, users_df=None, attendance_df=None, messages_df=None):
    """Detects problematic managers/admins who are not performing well."""
    
    insights = []
    actions = []
    
    if ratings_df.empty or users_df is None:
        return insights, actions
    
    admins = users_df[users_df["role"].isin(["admin", "manager"])]["username"]
    avg_peer_ratings = ratings_df.groupby("rated")["score"].mean()
    avg_given = ratings_df.groupby("rater")["score"].mean()
    
    for admin in admins:
        admin_avg = avg_peer_ratings.get(admin, 0)
        
        # RULE 1: Admin rated low <55% by everyone
        admin_received = ratings_df[ratings_df["rated"] == admin]
        if not admin_received.empty and admin_received["score"].mean() < 55:
            
            # Check if low performers rate them high (bias)
            high_raters = admin_received[admin_received["score"] > 75]
            low_raters = admin_received[admin_received["score"] < 45]
            
            if len(high_raters) <= 2 and len(low_raters) >= (len(admin_received) * 0.5):
                insights.append(f"🚨 BAD MANAGER: {admin} rated low ({admin_avg:.0f}%) by most - possible fear, lack of support")
                actions.append(f"  └─ ACTION: Meet with {admin}, listen to staff concerns, assess leadership fit")
        
        # RULE 2: Admin rates high performers <60% while team rates >70%
        admin_given = ratings_df[ratings_df["rater"] == admin]
        if not admin_given.empty:
            high_performers = avg_peer_ratings[avg_peer_ratings > 70]
            
            for performer in high_performers.index:
                admin_rating = admin_given[admin_given["rated"] == performer]["score"].mean()
                team_rating = high_performers[performer]
                
                if not pd.isna(admin_rating) and admin_rating < 60 and team_rating > 70:
                    insights.append(f"⚠ BIASED MANAGER: {admin} rates high performer {performer} low ({admin_rating:.0f}%) vs team ({team_rating:.0f}%) - possible conflict")
        
        # RULE 3: Late/early attendance pattern in manager
        if attendance_df is not None and not attendance_df.empty:
            attendance_df_copy = attendance_df.copy()
            attendance_df_copy["clock_in"] = pd.to_datetime(attendance_df_copy["clock_in"], errors="coerce")
            attendance_df_copy["clock_out"] = pd.to_datetime(attendance_df_copy["clock_out"], errors="coerce")
            
            admin_att = attendance_df_copy[attendance_df_copy["username"] == admin]
            late = len(admin_att[admin_att["clock_in"].dt.hour > 9])
            early = len(admin_att[admin_att["clock_out"].dt.hour < 18])
            
            if (late >= 5 and early >= 5) and admin_avg < 60:
                insights.append(f"🚨 BAD MANAGER PATTERN: {admin} - poor attendance (late {late}x, early {early}x) + low rating ({admin_avg:.0f}%) - not setting example")

            attendance_flags = _compute_monthly_leader_attendance_summary(attendance_df, admin)
            if (
                attendance_flags["grace_checkins"] < 4
                and attendance_flags["late_checkouts"] > 6
                and attendance_flags["early_exits"] <= 1
                and attendance_flags["lateness_requests"] <= 1
            ):
                insights.append(
                    f"⚠ LEADER ATTENDANCE SIGNAL: {admin} has very few grace check-ins ({attendance_flags['grace_checkins']} this month), "
                    f"frequent late departures ({attendance_flags['late_checkouts']} after hours), and minimal formal exception requests ({attendance_flags['lateness_requests']})."
                )
        
        # RULE 4: Many messages from employees (complaints/fear)
        if messages_df is not None and not messages_df.empty:
            msgs_to_about_admin = messages_df[messages_df["about_user"] == admin]
            if len(msgs_to_about_admin) > 15:
                insights.append(f"⚠ MANAGER ISSUES: {admin} - {len(msgs_to_about_admin)} messages from staff (complaints/fear indicator)")
    
    return list(set(insights)), actions


# =====================================================
# 🏢 WELL-PERFORMING BRANCH DETECTION
# =====================================================
def detect_well_performing_branches(ratings_df, users_df=None, attendance_df=None, messages_df=None):
    """Detects branches that are functioning optimally."""
    
    insights = []
    
    if ratings_df.empty or "branch" not in ratings_df.columns:
        return insights
    
    for branch in ratings_df["branch"].unique():
        branch_ratings = ratings_df[ratings_df["branch"] == branch]
        
        if len(branch_ratings) < 5:
            continue
        
        branch_avg = branch_ratings["score"].mean()
        
        # RULE 1: Everyone 78-95%, everyone rates each other 85%+, admin 80%+, no relationships/dating
        all_scores = branch_ratings["score"].values
        all_high = all(score >= 78 for score in all_scores) and all(score <= 95 for score in all_scores)
        
        avg_pair_ratings = branch_ratings.groupby("rater")["score"].mean().mean()
        
        if branch_avg >= 78 and all_high and avg_pair_ratings >= 85:
            branch_users = users_df[users_df["branch"] == branch]["username"] if users_df is not None else []
            admins = users_df[(users_df["branch"] == branch) & (users_df["role"].isin(["admin", "manager"]))]["username"] if users_df is not None else []
            
            admin_ratings = branch_ratings[branch_ratings["rater"].isin(admins)]
            admin_avg = admin_ratings["score"].mean() if not admin_ratings.empty else 0
            
            # Check for messages (should be low)
            msg_count = len(messages_df[messages_df["branch"] == branch]) if messages_df is not None else 0
            
            if admin_avg >= 75 and msg_count < 5:
                insights.append(f"🏢 EXCELLENT BRANCH: {branch} - Everyone 78-95%, harmonious (avg {branch_avg:.0f}%), admin strong ({admin_avg:.0f}%), minimal conflicts")
                insights.append(f"  └─ RECOMMENDATION: Celebrate team, use as model branch")
    
    return list(set(insights))
