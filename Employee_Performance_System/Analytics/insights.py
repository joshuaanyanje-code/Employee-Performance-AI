import pandas as pd
from datetime import datetime


def generate_insights(ratings_df, attendance_df=None, leaves_df=None, users_df=None, messages_df=None):

    insights = []

    # =========================
    # BASE DATA
    # =========================
    avg_scores = ratings_df.groupby("rated")["score"].mean()

    # --------------------------------
    # ORIGINAL LOGIC (KEPT ✅)
    # --------------------------------
    leaders = avg_scores[avg_scores > 85]

    for name in leaders.index:
        insights.append(f"⭐ Leadership emerging: {name} is receiving very high ratings.")

    conflicts = ratings_df[ratings_df["score"] < 35]

    if not conflicts.empty:
        for _, row in conflicts.iterrows():
            insights.append(f"⚠ Conflict detected between {row['rater']} and {row['rated']}.")

    alliances = ratings_df[ratings_df["score"] > 80]
    grouped = alliances.groupby(["rater", "rated"]).size()

    for pair in grouped.index:
        insights.append(f"🤝 Strong alliance: {pair[0]} and {pair[1]}.")

    risks = avg_scores[avg_scores < 45]

    for name in risks.index:
        insights.append(f"🚪 Resignation risk: {name} has very low peer ratings.")

    overall = ratings_df["score"].mean()
    insights.append(f"📊 Team health score: {round(overall,1)}")

    # =====================================================
    # 🔥 TOP / WORST PERFORMERS
    # =====================================================
    top2 = avg_scores.sort_values(ascending=False).head(2)
    for name, score in top2.items():
        insights.append(f"🏆 Top Performer: {name} ({round(score,1)})")

    worst = avg_scores.sort_values().head(2)
    for name, score in worst.items():
        insights.append(f"⚠ Low Performer: {name} ({round(score,1)})")

    # =====================================================
    # 👑 ADMIN INSIGHTS
    # =====================================================
    if users_df is not None:

        admins = users_df[users_df["role"] == "admin"]["username"]

        admin_scores = avg_scores[avg_scores.index.isin(admins)]

        if not admin_scores.empty:

            best_admin = admin_scores.idxmax()
            worst_admin = admin_scores.idxmin()

            insights.append(f"👑 Best Admin: {best_admin}")
            insights.append(f"⚠ Weak Admin: {worst_admin}")

    # =====================================================
    # 📈 MOST IMPROVED (simple proxy)
    # =====================================================
    if not ratings_df.empty:

        recent = ratings_df.copy()
        recent["created_at"] = pd.to_datetime(recent["created_at"], errors="coerce")

        last_week = recent[recent["created_at"] >= datetime.now() - pd.Timedelta(days=7)]

        if not last_week.empty:

            trend = last_week.groupby("rated")["score"].mean()
            improved = trend.sort_values(ascending=False).head(2)

            for name in improved.index:
                insights.append(f"📈 Most Improved: {name}")

    # =====================================================
    # ⏰ ATTENDANCE INSIGHTS
    # =====================================================
    if attendance_df is not None and not attendance_df.empty:

        attendance_df["date"] = pd.to_datetime(attendance_df["date"], errors="coerce")
        attendance_df["clock_in"] = pd.to_datetime(attendance_df["clock_in"], errors="coerce")

        now = datetime.now()
        current_month = now.month

        month_df = attendance_df[attendance_df["date"].dt.month == current_month]

        # ABSENT
        attendance_count = month_df.groupby("username")["date"].nunique()
        total_days = now.day

        absence = total_days - attendance_count

        if not absence.empty:
            worst_absent = absence.sort_values(ascending=False).head(2)

            for name, val in worst_absent.items():
                insights.append(f"🚫 Most Absent: {name} ({val} days)")

        # LATE
        late = month_df[month_df["clock_in"].dt.hour > 9]

        if not late.empty:
            late_counts = late.groupby("username").size().sort_values(ascending=False).head(3)

            for name, val in late_counts.items():
                insights.append(f"⏰ Frequent Late: {name} ({val} times)")

        # ADMIN ABSENCE
        if users_df is not None:

            admins = users_df[users_df["role"] == "admin"]["username"]

            admin_absent = absence[absence.index.isin(admins)]

            if not admin_absent.empty:
                worst_admin_absent = admin_absent.sort_values(ascending=False).head(1)

                for name in worst_admin_absent.index:
                    insights.append(f"🚨 Admin Absenteeism: {name}")

    # =====================================================
    # 🌴 LEAVES
    # =====================================================
    if leaves_df is not None and not leaves_df.empty:

        leave_counts = leaves_df["username"].value_counts()

        most_leave = leave_counts.head(2)
        no_leave = [u for u in users_df["username"] if u not in leave_counts.index]

        for name in most_leave.index:
            insights.append(f"🌴 Frequent Leave Taker: {name}")

        for name in no_leave[:2]:
            insights.append(f"✅ No Leave Taken: {name}")

    # =====================================================
    # 🏢 BRANCH INTELLIGENCE
    # =====================================================
    if "branch" in ratings_df.columns:

        branch_scores = ratings_df.groupby("branch")["score"].mean()

        top_branch = branch_scores.sort_values(ascending=False).head(2)

        for name, score in top_branch.items():
            if score >= 75:
                insights.append(f"🏢 Top Branch: {name} ({round(score,1)})")

    # =====================================================
    # 💬 MESSAGES
    # =====================================================
    if messages_df is not None and not messages_df.empty:

        msg_branch = messages_df.groupby("branch").size().sort_values(ascending=False).head(1)

        for b in msg_branch.index:
            insights.append(f"💬 Most Active Communication Branch: {b}")

    # =====================================================
    # 🔄 RETENTION (simple proxy)
    # =====================================================
    if users_df is not None:

        retention = users_df.groupby("branch").size().sort_values(ascending=False).head(1)

        for b in retention.index:
            insights.append(f"📊 Strong Retention Branch: {b}")

    return list(set(insights))
