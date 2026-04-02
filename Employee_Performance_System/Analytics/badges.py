import pandas as pd
from datetime import datetime, timedelta

try:
    from ..database.db import get_connection
except Exception:
    from database.db import get_connection


def _safe_read(conn, query, params=None):
    try:
        if params is None:
            return pd.read_sql(query, conn)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()


BADGE_ICON_RULES = [
    ("Gold", "🥇"),
    ("Silver", "🥈"),
    ("Bronze", "🥉"),
    ("Most Improved", "🚀"),
    ("Best Female", "👩🏆"),
    ("Best Male", "👨🏆"),
    ("Best Employee Entire Org", "👑"),
    ("Best Admin Overall Org", "🛡️"),
]


def get_badge_icon(badge_name):
    badge_name = str(badge_name or "")
    for key, icon in BADGE_ICON_RULES:
        if key in badge_name:
            return icon
    return "🏅"


def _avg_scores_by_user(ratings_df):
    if ratings_df is None or ratings_df.empty:
        return pd.DataFrame(columns=["username", "avg_score", "rating_count"])

    grouped = ratings_df.groupby("rated")["score"].agg(["mean", "count"]).reset_index()
    grouped.columns = ["username", "avg_score", "rating_count"]
    return grouped.sort_values(["avg_score", "rating_count"], ascending=[False, False])


def _get_org_data(conn, organization):
    users_df = _safe_read(
        conn,
        """
        SELECT username, role, branch, organization, status, gender
        FROM users
        WHERE organization=?
        """,
        params=(organization,),
    )
    ratings_df = _safe_read(
        conn,
        """
        SELECT rater, rated, topic, score, branch, organization, created_at
        FROM ratings
        WHERE organization=?
        """,
        params=(organization,),
    )

    attendance_df = _safe_read(
        conn,
        """
        SELECT username, status, date, branch
        FROM attendance
        WHERE organization=?
        """,
        params=(organization,),
    )

    warnings_df = _safe_read(
        conn,
        """
        SELECT username, type, message, created_at
        FROM warnings
        WHERE organization=?
        """,
        params=(organization,),
    )

    if not ratings_df.empty and "created_at" in ratings_df.columns:
        ratings_df = ratings_df.copy()
        ratings_df["created_at"] = pd.to_datetime(ratings_df["created_at"], errors="coerce")

    if not attendance_df.empty and "date" in attendance_df.columns:
        attendance_df = attendance_df.copy()
        attendance_df["date"] = pd.to_datetime(attendance_df["date"], errors="coerce")

    if not warnings_df.empty and "created_at" in warnings_df.columns:
        warnings_df = warnings_df.copy()
        warnings_df["created_at"] = pd.to_datetime(warnings_df["created_at"], errors="coerce")

    return users_df, ratings_df, attendance_df, warnings_df


def _clip_0_100(value):
    try:
        return max(0.0, min(100.0, float(value)))
    except Exception:
        return 0.0


def _build_employee_leaderboard(employees_df, ratings_df, attendance_df, warnings_df):
    if employees_df.empty:
        return pd.DataFrame()

    rows = []
    employee_usernames = employees_df["username"].astype(str).tolist()
    peers_count = max(len(employee_usernames) - 1, 1)

    for _, emp in employees_df.iterrows():
        username = str(emp.get("username", "")).strip()
        if not username:
            continue

        # 1) Performance received by peers
        received = ratings_df[ratings_df["rated"].astype(str) == username] if not ratings_df.empty else pd.DataFrame()
        received_avg = float(received["score"].mean()) if not received.empty else 0.0
        received_count = int(len(received)) if not received.empty else 0
        perf_component = _clip_0_100(received_avg)

        # 2) Attendance discipline
        att = attendance_df[attendance_df["username"].astype(str) == username] if not attendance_df.empty else pd.DataFrame()
        if not att.empty:
            late_rate = float((att["status"].astype(str).str.upper() == "LATE").mean())
            attendance_component = _clip_0_100(100 - (late_rate * 100))
            attendance_count = int(len(att))
        else:
            attendance_component = 50.0
            attendance_count = 0

        # 3) Fairness and genuine rating behavior as rater
        given = ratings_df[ratings_df["rater"].astype(str) == username] if not ratings_df.empty else pd.DataFrame()
        if not given.empty:
            unique_ratees = int(given["rated"].astype(str).nunique())
            diversity_ratio = min(unique_ratees / peers_count, 1.0)
            diversity_component = diversity_ratio * 100

            very_high_ratio = float((given["score"] >= 95).mean())
            abnormal_high_penalty = max(0.0, (very_high_ratio - 0.55) * 120)

            top_targets = given["rated"].astype(str).value_counts().head(2).sum()
            concentration_ratio = float(top_targets / max(len(given), 1))
            favoritism_penalty = max(0.0, (concentration_ratio - 0.60) * 100)

            # Mutual high-rating clique signal
            mutual_high_penalty = 0.0
            mutual_pairs = 0
            for rated_user in given["rated"].astype(str).unique().tolist():
                g1 = given[given["rated"].astype(str) == rated_user]
                g2 = ratings_df[
                    (ratings_df["rater"].astype(str) == rated_user)
                    & (ratings_df["rated"].astype(str) == username)
                ] if not ratings_df.empty else pd.DataFrame()
                if not g1.empty and not g2.empty:
                    if float(g1["score"].mean()) >= 88 and float(g2["score"].mean()) >= 88:
                        mutual_pairs += 1
            if mutual_pairs >= 2:
                mutual_high_penalty = min(25.0, mutual_pairs * 6.0)

            # Gender fairness in outgoing ratings
            gender_map = dict(zip(employees_df["username"].astype(str), employees_df["gender"].astype(str).str.lower())) if "gender" in employees_df.columns else {}
            given_tmp = given.copy()
            given_tmp["ratee_gender"] = given_tmp["rated"].astype(str).map(gender_map)
            male_given = given_tmp[given_tmp["ratee_gender"] == "male"]
            female_given = given_tmp[given_tmp["ratee_gender"] == "female"]
            gender_bias_penalty = 0.0
            if not male_given.empty and not female_given.empty:
                gender_gap = abs(float(male_given["score"].mean()) - float(female_given["score"].mean()))
                if gender_gap > 15:
                    gender_bias_penalty = min(20.0, (gender_gap - 15) * 1.2)

            fairness_component = _clip_0_100(
                (diversity_component * 0.55)
                + ((100 - abnormal_high_penalty) * 0.20)
                + ((100 - favoritism_penalty) * 0.15)
                + ((100 - mutual_high_penalty) * 0.10)
                - gender_bias_penalty
            )
            ratings_given_count = int(len(given))
        else:
            fairness_component = 45.0
            ratings_given_count = 0

        # 4) Conduct / scandal cleanliness
        warns = warnings_df[warnings_df["username"].astype(str) == username] if not warnings_df.empty else pd.DataFrame()
        warning_count = int(len(warns)) if not warns.empty else 0
        conduct_component = _clip_0_100(100 - min(45, warning_count * 10))

        # Final leadership score
        leader_score = _clip_0_100(
            (perf_component * 0.35)
            + (attendance_component * 0.25)
            + (fairness_component * 0.25)
            + (conduct_component * 0.15)
        )

        rows.append({
            "username": username,
            "branch": str(emp.get("branch", "")),
            "leader_score": round(leader_score, 2),
            "performance_component": round(perf_component, 2),
            "attendance_component": round(attendance_component, 2),
            "fairness_component": round(fairness_component, 2),
            "conduct_component": round(conduct_component, 2),
            "received_avg": round(received_avg, 2),
            "received_count": received_count,
            "attendance_count": attendance_count,
            "ratings_given_count": ratings_given_count,
        })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    return out.sort_values(["leader_score", "received_count", "attendance_count"], ascending=[False, False, False])


def compute_badges_for_organization(organization):
    conn = get_connection()
    users_df, ratings_df, attendance_df, warnings_df = _get_org_data(conn, organization)
    conn.close()

    payload = {
        "organization": organization,
        "generated_at": datetime.now().isoformat(),
        "badges": [],
        "summary": {
            "total_badges": 0,
            "unique_holders": 0,
            "categories": {},
            "branch_badges": {},
        },
        "best_employee": None,
        "best_admin": None,
    }

    if users_df.empty or ratings_df.empty:
        return payload

    users_df = users_df.copy()
    users_df["role"] = users_df["role"].astype(str).str.lower()

    employees = users_df[users_df["role"] == "employee"].copy()
    admins = users_df[users_df["role"].isin(["admin", "superadmin", "super_admin"])].copy()

    employee_scores = _avg_scores_by_user(ratings_df[ratings_df["rated"].isin(employees["username"])])
    admin_scores = _avg_scores_by_user(ratings_df[ratings_df["rated"].isin(admins["username"])])
    employee_leaderboard = _build_employee_leaderboard(employees, ratings_df, attendance_df, warnings_df)

    badges = []

    # Organization-level podium for employees (same composite model as best employee)
    org_top = employee_leaderboard.head(3).reset_index(drop=True)
    podium_labels = ["Gold No. 1", "Silver No. 2", "Bronze No. 3"]
    for index, row in org_top.iterrows():
        badges.append({
            "badge": podium_labels[index],
            "holder": row["username"],
            "scope": "organization",
            "organization": organization,
            "branch": "all",
            "category": "org_podium",
            "score": round(float(row["leader_score"]), 2),
            "rating_count": int(row.get("received_count", 0)),
        })

    # Branch-level podium for employees (rating-based style as requested)
    for branch_name in sorted(employees["branch"].dropna().astype(str).unique().tolist()):
        branch_users = employees[employees["branch"] == branch_name]["username"].tolist()
        if not branch_users:
            continue

        branch_scores = _avg_scores_by_user(
            ratings_df[ratings_df["rated"].isin(branch_users)]
        ).head(3).reset_index(drop=True)
        if branch_scores.empty:
            continue

        for index, row in branch_scores.iterrows():
            badges.append({
                "badge": f"{podium_labels[index]} (Branch)",
                "holder": row["username"],
                "scope": "branch",
                "organization": organization,
                "branch": branch_name,
                "category": "branch_podium",
                "score": round(float(row["avg_score"]), 2),
                "rating_count": int(row["rating_count"]),
            })

    # Most improved employee (recent 30d vs previous 30d)
    if not ratings_df.empty and "created_at" in ratings_df.columns:
        now = datetime.now()
        recent_cutoff = now - timedelta(days=30)
        previous_cutoff = now - timedelta(days=60)

        emp_ratings = ratings_df[ratings_df["rated"].isin(employees["username"])].copy()
        recent = emp_ratings[emp_ratings["created_at"] >= recent_cutoff]
        previous = emp_ratings[(emp_ratings["created_at"] >= previous_cutoff) & (emp_ratings["created_at"] < recent_cutoff)]

        if not recent.empty and not previous.empty:
            recent_mean = recent.groupby("rated")["score"].mean().reset_index().rename(columns={"score": "recent_score"})
            previous_mean = previous.groupby("rated")["score"].mean().reset_index().rename(columns={"score": "previous_score"})
            merged = recent_mean.merge(previous_mean, on="rated", how="inner")
            if not merged.empty:
                merged["improvement"] = merged["recent_score"] - merged["previous_score"]
                merged = merged.sort_values("improvement", ascending=False)
                best_imp = merged.iloc[0]
                if float(best_imp["improvement"]) > 0:
                    badges.append({
                        "badge": "Most Improved",
                        "holder": str(best_imp["rated"]),
                        "scope": "organization",
                        "organization": organization,
                        "branch": str(employees[employees["username"] == str(best_imp["rated"])] ["branch"].iloc[0]) if not employees[employees["username"] == str(best_imp["rated"])] .empty else "",
                        "category": "most_improved",
                        "score": round(float(best_imp["improvement"]), 2),
                        "rating_count": 0,
                    })

    # Best female/male overall org
    if not employees.empty and "gender" in employees.columns:
        for gender_value, badge_name in [("female", "Best Female Overall Org"), ("male", "Best Male Overall Org")]:
            gender_users = employees[employees["gender"].astype(str).str.lower() == gender_value]["username"].tolist()
            if gender_users:
                gender_scores = _avg_scores_by_user(ratings_df[ratings_df["rated"].isin(gender_users)])
                if not gender_scores.empty:
                    top_row = gender_scores.iloc[0]
                    badges.append({
                        "badge": badge_name,
                        "holder": top_row["username"],
                        "scope": "organization",
                        "organization": organization,
                        "branch": str(employees[employees["username"] == top_row["username"]]["branch"].iloc[0]) if not employees[employees["username"] == top_row["username"]].empty else "",
                        "category": "gender_award",
                        "score": round(float(top_row["avg_score"]), 2),
                        "rating_count": int(top_row["rating_count"]),
                    })

    # Best employee overall org
    if not employee_leaderboard.empty:
        best_emp = employee_leaderboard.iloc[0]
        payload["best_employee"] = {
            "username": best_emp["username"],
            "score": round(float(best_emp["leader_score"]), 2),
            "organization": organization,
            "branch": str(best_emp.get("branch", "")),
            "model": "leadership_composite",
            "components": {
                "performance": float(best_emp.get("performance_component", 0)),
                "attendance": float(best_emp.get("attendance_component", 0)),
                "fairness": float(best_emp.get("fairness_component", 0)),
                "conduct": float(best_emp.get("conduct_component", 0)),
            },
        }
        badges.append({
            "badge": "Best Employee Entire Org",
            "holder": best_emp["username"],
            "scope": "organization",
            "organization": organization,
            "branch": payload["best_employee"]["branch"],
            "category": "best_employee_org",
            "score": round(float(best_emp["leader_score"]), 2),
            "rating_count": int(best_emp.get("received_count", 0)),
        })

    # Best admin overall org
    if not admin_scores.empty:
        best_adm = admin_scores.iloc[0]
        payload["best_admin"] = {
            "username": best_adm["username"],
            "score": round(float(best_adm["avg_score"]), 2),
            "organization": organization,
            "branch": str(admins[admins["username"] == best_adm["username"]]["branch"].iloc[0]) if not admins[admins["username"] == best_adm["username"]].empty else "",
        }
        badges.append({
            "badge": "Best Admin Overall Org",
            "holder": best_adm["username"],
            "scope": "organization",
            "organization": organization,
            "branch": payload["best_admin"]["branch"],
            "category": "best_admin_org",
            "score": round(float(best_adm["avg_score"]), 2),
            "rating_count": int(best_adm["rating_count"]),
        })

    badges_df = pd.DataFrame(badges)
    payload["badges"] = badges

    if not badges_df.empty:
        payload["summary"]["total_badges"] = len(badges_df)
        payload["summary"]["unique_holders"] = int(badges_df["holder"].nunique())
        payload["summary"]["categories"] = badges_df.groupby("category").size().to_dict()
        payload["summary"]["branch_badges"] = badges_df.groupby("branch").size().to_dict()

    return payload


def build_holder_badge_map(organization):
    payload = compute_badges_for_organization(organization)
    badges = payload.get("badges", [])
    out = {}
    for badge in badges:
        holder = str(badge.get("holder", "")).strip()
        badge_name = str(badge.get("badge", "")).strip()
        if not holder or not badge_name:
            continue
        icon = get_badge_icon(badge_name)
        if holder not in out:
            out[holder] = {"icons": [], "badges": []}
        if icon not in out[holder]["icons"]:
            out[holder]["icons"].append(icon)
        out[holder]["badges"].append(badge_name)
    return out


def decorate_username_with_badges(username, holder_badge_map):
    username = str(username or "")
    if username in holder_badge_map:
        icons = " ".join(holder_badge_map[username].get("icons", [])[:4])
        return f"{username} {icons}".strip()
    return username


def get_badge_holders_table(organization):
    payload = compute_badges_for_organization(organization)
    badges = payload.get("badges", [])
    if not badges:
        return pd.DataFrame()
    df = pd.DataFrame(badges)
    if "badge" in df.columns:
        df["icon"] = df["badge"].apply(get_badge_icon)
    if "holder" in df.columns and "icon" in df.columns:
        df["holder_display"] = df["holder"].astype(str) + " " + df["icon"].astype(str)
    return df


def compute_badge_summary_for_super_admin(organization):
    info = compute_badges_for_organization(organization)
    summary = info.get("summary", {}).copy()
    summary["organization"] = organization
    summary["generated_at"] = info.get("generated_at")
    summary["best_employee_score"] = info.get("best_employee", {}).get("score", 0)
    summary["best_admin_score"] = info.get("best_admin", {}).get("score", 0)
    return summary


def get_best_employees_across_organizations():
    conn = get_connection()
    orgs = _safe_read(conn, "SELECT name FROM organizations ORDER BY name")
    conn.close()

    if orgs.empty:
        return pd.DataFrame()

    rows = []
    for _, row in orgs.iterrows():
        org_name = str(row.get("name", "")).strip()
        if not org_name:
            continue
        data = compute_badges_for_organization(org_name)
        best = data.get("best_employee")
        if best:
            rows.append({
                "organization": org_name,
                "best_employee": best.get("username", ""),
                "branch": best.get("branch", ""),
                "score": best.get("score", 0),
            })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    return out.sort_values("score", ascending=False).reset_index(drop=True)
