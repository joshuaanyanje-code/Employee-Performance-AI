import pandas as pd
from datetime import datetime, timedelta
import json
import re

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


def _normalize_role_name(value):
    role = str(value or "").strip().lower()
    if role in {"super_admin", "superadmin"}:
        return "super_admin"
    if role in {"master", "master_admin", "owner", "overall"}:
        return "master"
    if role in {"admin", "manager"}:
        return "admin"
    return role


def _filter_scope_frame(df, branch=None, branch_columns=None):
    if df is None or getattr(df, "empty", True) or not branch:
        return df

    branch_value = str(branch or "").strip()
    columns = list(branch_columns or ["branch"])
    out = df.copy()
    matched = False
    for col in columns:
        if col in out.columns:
            out = out[out[col].fillna("").astype(str).str.strip() == branch_value].copy()
            matched = True
            break
    return out if matched else df


def _filter_user_scoped_frame(df, allowed_usernames, user_columns):
    if df is None or getattr(df, "empty", True):
        return df

    columns = [col for col in (user_columns or []) if col in df.columns]
    if not columns:
        return df

    if not allowed_usernames:
        return df.iloc[0:0].copy()

    out = df.copy()
    allowed = {str(name).strip() for name in allowed_usernames if str(name).strip()}
    for col in columns:
        out = out[out[col].fillna("").astype(str).str.strip().isin(allowed)]
    return out.copy()


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


def _text_mentions_employee(text, employee_name):
    employee_name = str(employee_name or "").strip()
    if not employee_name:
        return False
    try:
        pattern = rf"(?<!\w){re.escape(employee_name)}(?!\w)"
        return bool(re.search(pattern, str(text or ""), flags=re.IGNORECASE))
    except Exception:
        return employee_name.lower() in str(text or "").lower()



def _build_hr_ladder(risk_level):
    ladder = [
        "Normal Monitoring",
        "Coaching / Monitoring",
        "Written Warning / PIP",
        "Probation / Final Warning",
        "Termination Recommendation",
    ]
    current_map = {
        "Stable": 0,
        "Watchlist": 1,
        "Coaching Needed": 2,
        "Final Warning": 3,
        "Termination Review": 4,
    }
    current_idx = current_map.get(risk_level, 0)
    return ladder[current_idx], [
        {
            "stage": stage,
            "status": "current" if idx == current_idx else "done" if idx < current_idx else "pending",
        }
        for idx, stage in enumerate(ladder)
    ]



def _clamp_score(value, low=0.0, high=100.0):
    try:
        return round(max(low, min(high, float(value))), 1)
    except Exception:
        return round(float(low), 1)



def _coerce_date_boundary(value, end_of_day=False):
    if value is None or str(value).strip() == "":
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:
        ts = pd.NaT
    if pd.isna(ts):
        return None
    ts = ts.normalize()
    if end_of_day:
        ts = ts + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return ts.to_pydatetime()



def _filter_dataframe_by_date_range(df, possible_cols, start_date=None, end_date=None):
    if df is None or df.empty:
        return df

    start_dt = _coerce_date_boundary(start_date, end_of_day=False)
    end_dt = _coerce_date_boundary(end_date, end_of_day=True)
    if start_dt is None and end_dt is None:
        return df

    scoped = df.copy()
    target_col = None
    for col in possible_cols:
        if col in scoped.columns:
            scoped[col] = pd.to_datetime(scoped[col], errors="coerce")
            target_col = col
            break

    if target_col is None:
        return scoped

    mask = scoped[target_col].notna()
    if start_dt is not None:
        mask &= scoped[target_col] >= pd.Timestamp(start_dt)
    if end_dt is not None:
        mask &= scoped[target_col] <= pd.Timestamp(end_dt)
    return scoped[mask].copy()



def _build_employee_scorecard(
    recent_avg,
    decline_points,
    low_score_count,
    late_days,
    absence_signals,
    early_clockouts,
    warning_count,
    manager_pattern_count,
    leave_fairness_count,
    bad_seed_count,
    rating_out_pattern_count,
    negative_group_count,
):
    performance_score = _clamp_score(
        recent_avg - (decline_points * 1.8) - max(low_score_count - 1, 0) * 4
    )
    attendance_score = _clamp_score(
        100 - (absence_signals * 18) - (late_days * 6) - (early_clockouts * 5)
    )
    behavior_score = _clamp_score(
        100 - (warning_count * 12) - (bad_seed_count * 18) - (rating_out_pattern_count * 10) - (negative_group_count * 12)
    )
    fairness_score = _clamp_score(
        100 - (manager_pattern_count * 18) - (leave_fairness_count * 12)
    )

    overall_score = _clamp_score(
        (performance_score * 0.40) +
        (attendance_score * 0.25) +
        (behavior_score * 0.20) +
        (fairness_score * 0.15)
    )

    if overall_score >= 80:
        score_band = "Strong"
    elif overall_score >= 65:
        score_band = "Moderate"
    elif overall_score >= 50:
        score_band = "Needs Coaching"
    elif overall_score >= 35:
        score_band = "High Risk"
    else:
        score_band = "Critical"

    return {
        "overall": overall_score,
        "band": score_band,
        "performance": performance_score,
        "attendance": attendance_score,
        "behavior": behavior_score,
        "fairness": fairness_score,
    }



def _classify_employee_risk(
    recent_avg,
    decline_points,
    low_score_count,
    late_days,
    absence_signals,
    early_clockouts,
    warning_count,
    signal_count,
    critical_logic_count,
):
    signal_score = 0
    if recent_avg <= 55:
        signal_score += 2
    if recent_avg <= 45:
        signal_score += 2
    signal_score += min(int(max(decline_points, 0) // 4), 3)
    signal_score += min(int(late_days // 2), 2)
    signal_score += min(int(absence_signals), 2)
    signal_score += min(int(early_clockouts // 2), 2)
    signal_score += min(int(warning_count), 2)
    signal_score += min(int(signal_count), 4)
    signal_score += int(critical_logic_count)

    if recent_avg <= 42 or signal_score >= 10 or (decline_points >= 10 and critical_logic_count >= 2):
        return "Termination Review", 4
    if recent_avg <= 50 or signal_score >= 8 or critical_logic_count >= 2:
        return "Final Warning", 3
    if recent_avg <= 55 or signal_score >= 5 or decline_points >= 5:
        return "Coaching Needed", 2
    if signal_score >= 2 or decline_points > 0 or low_score_count >= 2:
        return "Watchlist", 1
    return "Stable", 0



def _build_employee_risk_overview(
    ratings_df,
    attendance_df=None,
    warnings_df=None,
    users_df=None,
    leaves_df=None,
    intelligence=None,
    group_analysis=None,
):
    empty_summary = {
        "total_tracked": 0,
        "below_55": 0,
        "needs_attention": 0,
        "watchlist": 0,
        "coaching_needed": 0,
        "final_warning": 0,
        "termination_review": 0,
    }

    if ratings_df is None or ratings_df.empty:
        return {"summary": empty_summary, "cases": []}

    intelligence = intelligence if isinstance(intelligence, dict) else {}
    group_analysis = group_analysis if isinstance(group_analysis, dict) else {}

    ratings_work = ratings_df.copy()
    ratings_work["rated"] = ratings_work["rated"].astype(str).str.strip()
    if "rater" in ratings_work.columns:
        ratings_work["rater"] = ratings_work["rater"].astype(str).str.strip()
    ratings_work["score"] = pd.to_numeric(ratings_work.get("score"), errors="coerce")
    if "created_at" in ratings_work.columns:
        ratings_work["created_at"] = pd.to_datetime(ratings_work["created_at"], errors="coerce")
    ratings_work = ratings_work.dropna(subset=["rated", "score"])
    ratings_work = ratings_work[ratings_work["rated"] != ""]

    if ratings_work.empty:
        return {"summary": empty_summary, "cases": []}

    attendance_work = pd.DataFrame()
    if attendance_df is not None and not attendance_df.empty and "username" in attendance_df.columns:
        attendance_work = attendance_df.copy()
        attendance_work["username"] = attendance_work["username"].astype(str).str.strip()
        for col in ["date", "clock_in", "clock_out"]:
            if col in attendance_work.columns:
                attendance_work[col] = pd.to_datetime(attendance_work[col], errors="coerce")

    warnings_work = pd.DataFrame()
    if warnings_df is not None and not warnings_df.empty and "username" in warnings_df.columns:
        warnings_work = warnings_df.copy()
        warnings_work["username"] = warnings_work["username"].astype(str).str.strip()
        if "created_at" in warnings_work.columns:
            warnings_work["created_at"] = pd.to_datetime(warnings_work["created_at"], errors="coerce")

    users_work = pd.DataFrame()
    role_lookup = {}
    if users_df is not None and not users_df.empty and "username" in users_df.columns:
        users_work = users_df.copy()
        users_work["username"] = users_work["username"].astype(str).str.strip()
        if "role" in users_work.columns:
            role_lookup = dict(
                zip(
                    users_work["username"].astype(str),
                    users_work["role"].fillna("").astype(str).str.lower(),
                )
            )

    leaves_work = pd.DataFrame()
    if leaves_df is not None and not leaves_df.empty and "username" in leaves_df.columns:
        leaves_work = leaves_df.copy()
        leaves_work["username"] = leaves_work["username"].astype(str).str.strip()
        for col in ["start_date", "end_date", "reviewed_at"]:
            if col in leaves_work.columns:
                leaves_work[col] = pd.to_datetime(leaves_work[col], errors="coerce")

    now = datetime.now()
    recent_cutoff = now - timedelta(days=30)
    baseline_cutoff = now - timedelta(days=120)
    cases = []

    for employee in sorted(ratings_work["rated"].unique()):
        emp_scores = ratings_work[ratings_work["rated"] == employee].copy()
        if emp_scores.empty:
            continue

        if "created_at" in emp_scores.columns:
            emp_scores = emp_scores.sort_values("created_at")

        overall_avg = _safe_float(emp_scores["score"].mean())
        latest_score = _safe_float(emp_scores["score"].iloc[-1]) if not emp_scores.empty else overall_avg

        if "created_at" in emp_scores.columns and emp_scores["created_at"].notna().any():
            recent_scores = emp_scores[emp_scores["created_at"] >= recent_cutoff]
            baseline_scores = emp_scores[
                (emp_scores["created_at"] >= baseline_cutoff) & (emp_scores["created_at"] < recent_cutoff)
            ]
        else:
            recent_scores = emp_scores.tail(min(len(emp_scores), 10))
            baseline_scores = emp_scores.head(max(len(emp_scores) // 2, 1))

        recent_avg = _safe_float(recent_scores["score"].mean()) if not recent_scores.empty else overall_avg
        baseline_avg = _safe_float(baseline_scores["score"].mean()) if not baseline_scores.empty else overall_avg
        decline_points = round(max(baseline_avg - recent_avg, 0.0), 2)
        low_score_count = int((emp_scores["score"] < 50).sum())

        branch_name = ""
        role_name = ""
        if not users_work.empty:
            user_match = users_work[users_work["username"] == employee]
            if not user_match.empty:
                branch_name = str(user_match.iloc[0].get("branch", "") or "")
                role_name = str(user_match.iloc[0].get("role", "") or "")
        if not branch_name and "branch" in emp_scores.columns and not emp_scores["branch"].dropna().empty:
            branch_name = str(emp_scores["branch"].dropna().iloc[-1] or "")

        late_days = 0
        absence_signals = 0
        early_clockouts = 0
        warning_count = 0
        leave_requests = 0
        leave_rejections = 0

        score_history = pd.DataFrame()
        attendance_history = pd.DataFrame()
        warning_history = pd.DataFrame()
        leave_history = pd.DataFrame()

        if "created_at" in emp_scores.columns:
            score_hist_work = emp_scores.dropna(subset=["created_at"]).copy()
            if not score_hist_work.empty:
                score_hist_work["period"] = score_hist_work["created_at"].dt.to_period("M").astype(str)
                score_history = score_hist_work.groupby("period", as_index=False).agg(
                    avg_score=("score", "mean"),
                    ratings=("score", "count"),
                    low_scores=("score", lambda s: int((s < 50).sum())),
                )

        if not attendance_work.empty:
            emp_att = attendance_work[attendance_work["username"] == employee].copy()
            if not emp_att.empty:
                date_col = "date" if "date" in emp_att.columns else "clock_in" if "clock_in" in emp_att.columns else None
                if date_col:
                    recent_att = emp_att[emp_att[date_col] >= recent_cutoff] if emp_att[date_col].notna().any() else emp_att
                    if "status" in recent_att.columns:
                        recent_status = recent_att["status"].astype(str).str.upper()
                        late_days = int((recent_status == "LATE").sum())
                        absence_signals = int(recent_status.isin(["ABSENT", "NO SHOW", "NO-SHOW", "MISS"]).sum())
                    if "clock_in" in recent_att.columns:
                        late_days = max(late_days, int((recent_att["clock_in"].dt.hour > 9).fillna(False).sum()))
                        absence_signals += int(recent_att["clock_in"].isna().sum())
                    if "clock_out" in recent_att.columns:
                        early_clockouts = int((recent_att["clock_out"].dt.hour < 18).fillna(False).sum())

                    att_hist = emp_att.dropna(subset=[date_col]).copy()
                    if not att_hist.empty:
                        att_hist["period"] = att_hist[date_col].dt.to_period("M").astype(str)
                        if "status" in att_hist.columns:
                            att_status = att_hist["status"].astype(str).str.upper()
                            att_hist["late_flag"] = att_status.eq("LATE").astype(int)
                            att_hist["absence_flag"] = att_status.isin(["ABSENT", "NO SHOW", "NO-SHOW", "MISS"]).astype(int)
                        else:
                            att_hist["late_flag"] = (att_hist["clock_in"].dt.hour > 9).fillna(False).astype(int)
                            att_hist["absence_flag"] = 0
                        if "clock_out" in att_hist.columns:
                            att_hist["early_flag"] = (att_hist["clock_out"].dt.hour < 18).fillna(False).astype(int)
                        else:
                            att_hist["early_flag"] = 0
                        attendance_history = att_hist.groupby("period", as_index=False).agg(
                            late_days=("late_flag", "sum"),
                            absence_signals=("absence_flag", "sum"),
                            early_clockouts=("early_flag", "sum"),
                        )

        if not warnings_work.empty:
            emp_warn = warnings_work[warnings_work["username"] == employee].copy()
            if not emp_warn.empty:
                if "created_at" in emp_warn.columns and emp_warn["created_at"].notna().any():
                    recent_warn = emp_warn[emp_warn["created_at"] >= baseline_cutoff]
                    warn_hist = emp_warn.dropna(subset=["created_at"]).copy()
                    if not warn_hist.empty:
                        warn_hist["period"] = warn_hist["created_at"].dt.to_period("M").astype(str)
                        warning_history = warn_hist.groupby("period", as_index=False).size().rename(columns={"size": "warnings"})
                else:
                    recent_warn = emp_warn
                warning_count = int(len(recent_warn))

        if not leaves_work.empty:
            emp_leave = leaves_work[leaves_work["username"] == employee].copy()
            if not emp_leave.empty:
                leave_requests = int(len(emp_leave))
                if "status" in emp_leave.columns:
                    leave_status = emp_leave["status"].fillna("").astype(str).str.lower()
                    leave_rejections = int(leave_status.isin(["rejected", "denied"]).sum())
                leave_date_col = "reviewed_at" if "reviewed_at" in emp_leave.columns else "start_date" if "start_date" in emp_leave.columns else None
                if leave_date_col:
                    leave_hist = emp_leave.dropna(subset=[leave_date_col]).copy()
                    if not leave_hist.empty:
                        leave_hist["period"] = leave_hist[leave_date_col].dt.to_period("M").astype(str)
                        if "status" in leave_hist.columns:
                            leave_hist["rejected_flag"] = leave_hist["status"].fillna("").astype(str).str.lower().isin(["rejected", "denied"]).astype(int)
                        else:
                            leave_hist["rejected_flag"] = 0
                        leave_history = leave_hist.groupby("period", as_index=False).agg(
                            leave_requests=("username", "count"),
                            leave_rejections=("rejected_flag", "sum"),
                        )

        manager_pattern_notes = set()
        leave_fairness_notes = set()
        bad_seed_notes = set()
        negative_group_notes = set()
        rating_out_notes = set()

        if "rater" in emp_scores.columns and role_lookup:
            emp_scores["rater_role"] = emp_scores["rater"].map(role_lookup).fillna("")
            manager_scores = emp_scores[emp_scores["rater_role"].isin(["admin", "manager"])]["score"]
            peer_scores = emp_scores[~emp_scores["rater_role"].isin(["admin", "manager"])]["score"]
            if len(manager_scores) >= 2 and int((manager_scores < 50).sum()) >= 2:
                manager_pattern_notes.add("Managers/admins repeatedly rated this employee below 50%.")
            if len(manager_scores) >= 2 and not peer_scores.empty and (float(peer_scores.mean()) - float(manager_scores.mean())) >= 12:
                manager_pattern_notes.add("Manager rating pattern is materially harsher than peer ratings and should be reviewed.")

        given_df = pd.DataFrame()
        if "rater" in ratings_work.columns:
            given_df = ratings_work[ratings_work["rater"] == employee].copy()
        if not given_df.empty:
            very_low_given = int((given_df["score"] < 45).sum())
            extreme_high_given = int((given_df["score"] > 85).sum())
            target_summary = given_df.groupby("rated")["score"].mean() if "rated" in given_df.columns else pd.Series(dtype=float)
            if very_low_given >= 3:
                rating_out_notes.add(f"This employee gave {very_low_given} very low ratings to others.")
            if len(target_summary[target_summary < 45]) >= 2:
                rating_out_notes.add("This employee consistently rates some coworkers very low.")
            if extreme_high_given >= 3 and len(target_summary[target_summary > 85]) <= 2:
                rating_out_notes.add("This employee gives unusually high scores to a small circle of coworkers.")
            if very_low_given >= 3 and extreme_high_given >= 1:
                bad_seed_notes.add("Bad-seed pattern detected in how this employee rates others (very low to many, selectively high to a few).")

        if leave_rejections >= 2:
            leave_fairness_notes.add(f"{leave_rejections} leave request(s) were rejected/denied and may need a fairness review.")
        elif leave_requests >= 3:
            leave_fairness_notes.add(f"{leave_requests} leave cases have been logged for this employee and should be reviewed for workload or fairness issues.")

        text_sources = {
            "favoritism_analysis": intelligence.get("favoritism_analysis", []),
            "power_abuse_analysis": intelligence.get("power_abuse_analysis", []),
            "peer_gangup_analysis": intelligence.get("peer_gangup_analysis", []),
            "isolation_analysis": intelligence.get("isolation_analysis", []),
            "critical_alerts": intelligence.get("critical_alerts", []),
            "recommendations": intelligence.get("recommendations", []),
        }
        for _, entries in text_sources.items():
            for entry in entries or []:
                entry_text = str(entry or "").strip()
                if not entry_text or not _text_mentions_employee(entry_text, employee):
                    continue
                upper = entry_text.upper()
                if any(token in upper for token in ["ADMIN BIAS", "FAVORITISM", "POWER ABUSE", "RETALIATION", "DISCIPLINE TARGETING"]):
                    manager_pattern_notes.add(entry_text)
                if any(token in upper for token in ["LEAVE PRESSURE", "FAVOR PROTECTION", "BOUNDARY RISK"]):
                    leave_fairness_notes.add(entry_text)
                if any(token in upper for token in ["BAD SEED", "TOXIC"]):
                    bad_seed_notes.add(entry_text)
                if any(token in upper for token in ["PEER GANG-UP", "PEER TARGETING", "CLIQUE", "GROUP", "ISOLATION", "CONFLICT"]):
                    negative_group_notes.add(entry_text)

        for group in group_analysis.get("group_details", []) or []:
            members = []
            raw_members = group.get("members", [])
            if isinstance(raw_members, list):
                members.extend([str(m).strip() for m in raw_members if str(m).strip()])
            elif isinstance(raw_members, str):
                members.extend([m.strip() for m in raw_members.split(",") if m.strip()])
            for key in ["member_1", "member_2"]:
                if str(group.get(key, "") or "").strip():
                    members.append(str(group.get(key)).strip())
            if employee not in set(members):
                continue
            gtype = str(group.get("group_type", "") or "").lower()
            group_risk = str(group.get("risk_level", "") or "").lower()
            description = str(group.get("description", "Group involvement detected") or "Group involvement detected")
            if gtype in ["conflict_pair", "synchronized"] or group_risk in ["warning", "high", "critical"]:
                negative_group_notes.add(description)

        manager_pattern_count = len(manager_pattern_notes)
        leave_fairness_count = len(leave_fairness_notes)
        bad_seed_count = len(bad_seed_notes)
        rating_out_pattern_count = len(rating_out_notes)
        negative_group_count = len(negative_group_notes)

        logic_breakdown = {
            "Low genuine ratings": int(low_score_count + (1 if recent_avg <= 55 else 0)),
            "Absenteeism / lateness / early exits": int(late_days + absence_signals + early_clockouts),
            "Manager rating pattern": int(manager_pattern_count),
            "Leave fairness / favoritism": int(leave_fairness_count),
            "How person rates others": int(rating_out_pattern_count),
            "Bad seed / toxic influence": int(bad_seed_count),
            "Negative groups / clique signals": int(negative_group_count),
        }
        active_logic_count = len([v for v in logic_breakdown.values() if int(v) > 0])
        critical_logic_count = len([
            key for key in [
                "Manager rating pattern",
                "Leave fairness / favoritism",
                "Bad seed / toxic influence",
                "Negative groups / clique signals",
            ]
            if int(logic_breakdown.get(key, 0)) > 0
        ])

        scorecard = _build_employee_scorecard(
            recent_avg,
            decline_points,
            low_score_count,
            late_days,
            absence_signals,
            early_clockouts,
            warning_count,
            manager_pattern_count,
            leave_fairness_count,
            bad_seed_count,
            rating_out_pattern_count,
            negative_group_count,
        )

        risk_level, risk_rank = _classify_employee_risk(
            recent_avg,
            decline_points,
            low_score_count,
            late_days,
            absence_signals,
            early_clockouts,
            warning_count,
            active_logic_count,
            critical_logic_count,
        )
        hr_stage, hr_ladder = _build_hr_ladder(risk_level)

        reasons = []
        if recent_avg <= 55:
            reasons.append(f"Low genuine rating average: {recent_avg:.1f}% in the recent review window.")
        if decline_points > 0:
            reasons.append(f"Performance dropped by {decline_points:.1f} points from baseline ({baseline_avg:.1f} -> {recent_avg:.1f}).")
        if late_days or absence_signals or early_clockouts:
            reasons.append(
                f"Attendance signals -> absent/missed: {absence_signals}, late: {late_days}, early clock-outs: {early_clockouts}."
            )
        if warning_count:
            reasons.append(f"{warning_count} warning record(s) logged in the last 120 days.")
        reasons.append(
            f"Employee score card is {scorecard.get('overall', 0):.1f}/100 ({scorecard.get('band', 'Needs Review')})."
        )
        if manager_pattern_notes:
            reasons.extend(list(manager_pattern_notes)[:2])
        if leave_fairness_notes:
            reasons.extend(list(leave_fairness_notes)[:2])
        if bad_seed_notes:
            reasons.extend(list(bad_seed_notes)[:2])
        if rating_out_notes:
            reasons.extend(list(rating_out_notes)[:2])
        if negative_group_notes:
            reasons.extend(list(negative_group_notes)[:2])
        if not reasons:
            reasons.append("No strong decline signal found, but the employee remains under normal monitoring.")

        deduped_reasons = []
        seen_reason_norms = set()
        for reason in reasons:
            cleaned = str(reason).strip()
            if not cleaned:
                continue
            norm = cleaned.lower()
            if norm not in seen_reason_norms:
                deduped_reasons.append(cleaned)
                seen_reason_norms.add(norm)

        history_frames = [frame for frame in [score_history, attendance_history, warning_history, leave_history] if not frame.empty]
        history_df = pd.DataFrame()
        for frame in history_frames:
            history_df = frame.copy() if history_df.empty else history_df.merge(frame, on="period", how="outer")

        history_rows = []
        if not history_df.empty:
            history_df = history_df.fillna(0).sort_values("period").tail(6)
            for _, row in history_df.iterrows():
                history_rows.append({
                    "Period": str(row.get("period", "")),
                    "Avg Score": round(_safe_float(row.get("avg_score", 0)), 1),
                    "Ratings": int(row.get("ratings", 0) or 0),
                    "Low Scores": int(row.get("low_scores", 0) or 0),
                    "Late Days": int(row.get("late_days", 0) or 0),
                    "Absence Signals": int(row.get("absence_signals", 0) or 0),
                    "Early Clock-Outs": int(row.get("early_clockouts", 0) or 0),
                    "Warnings": int(row.get("warnings", 0) or 0),
                    "Leave Requests": int(row.get("leave_requests", 0) or 0),
                    "Leave Rejections": int(row.get("leave_rejections", 0) or 0),
                })

        timeline_notes = [
            f"Baseline performance: {baseline_avg:.1f}",
            f"Most recent performance window: {recent_avg:.1f}",
            f"Employee score card: {scorecard.get('overall', 0):.1f}/100 ({scorecard.get('band', 'Needs Review')})",
            f"Combined logic signals counted: {active_logic_count}",
            f"Current HR ladder stage: {hr_stage}",
        ]
        if warning_count or late_days or absence_signals or early_clockouts:
            timeline_notes.append(
                f"Discipline signals -> absent/missed: {absence_signals}, late: {late_days}, early outs: {early_clockouts}, warnings: {warning_count}."
            )
        if manager_pattern_count or leave_fairness_count or bad_seed_count or rating_out_pattern_count or negative_group_count:
            timeline_notes.append(
                "Expanded logic -> manager pattern: "
                f"{manager_pattern_count}, leave fairness: {leave_fairness_count}, bad seed: {bad_seed_count}, "
                f"rates others pattern: {rating_out_pattern_count}, negative groups: {negative_group_count}."
            )

        cases.append({
            "employee": employee,
            "branch": branch_name or "-",
            "role": role_name or "employee",
            "risk_level": risk_level,
            "risk_rank": risk_rank,
            "hr_stage": hr_stage,
            "hr_ladder": hr_ladder,
            "overall_avg": round(overall_avg, 1),
            "recent_avg": round(recent_avg, 1),
            "baseline_avg": round(baseline_avg, 1),
            "decline_points": round(decline_points, 1),
            "latest_score": round(latest_score, 1),
            "low_score_count": low_score_count,
            "late_days": int(late_days),
            "absence_signals": int(absence_signals),
            "early_clockouts": int(early_clockouts),
            "warning_count": int(warning_count),
            "leave_requests": int(leave_requests),
            "leave_rejections": int(leave_rejections),
            "signal_count": int(active_logic_count),
            "logic_breakdown": logic_breakdown,
            "scorecard": scorecard,
            "scorecard_total": float(scorecard.get("overall", 0)),
            "primary_reason": deduped_reasons[0],
            "reasons": deduped_reasons[:8],
            "timeline": history_rows,
            "timeline_notes": timeline_notes,
        })

    cases = sorted(
        cases,
        key=lambda item: (
            item.get("risk_rank", 0),
            item.get("signal_count", 0),
            item.get("warning_count", 0),
            item.get("absence_signals", 0),
            item.get("late_days", 0),
            item.get("decline_points", 0),
        ),
        reverse=True,
    )

    flagged_cases = [case for case in cases if case.get("risk_rank", 0) > 0]
    below_55_cases = [
        case for case in flagged_cases
        if float(case.get("recent_avg", 0) or 0) <= 55 or float(case.get("overall_avg", 0) or 0) <= 55
    ]
    return {
        "summary": {
            "total_tracked": len(cases),
            "below_55": len(below_55_cases),
            "needs_attention": len(flagged_cases),
            "watchlist": len([c for c in flagged_cases if c.get("risk_level") == "Watchlist"]),
            "coaching_needed": len([c for c in flagged_cases if c.get("risk_level") == "Coaching Needed"]),
            "final_warning": len([c for c in flagged_cases if c.get("risk_level") == "Final Warning"]),
            "termination_review": len([c for c in flagged_cases if c.get("risk_level") == "Termination Review"]),
        },
        "cases": flagged_cases[:12],
    }



def _build_workforce_scorecards(
    ratings_df,
    attendance_df=None,
    warnings_df=None,
    users_df=None,
    leaves_df=None,
    intelligence=None,
    group_analysis=None,
    analysis_start=None,
    analysis_end=None,
):
    intelligence = intelligence if isinstance(intelligence, dict) else {}
    group_analysis = group_analysis if isinstance(group_analysis, dict) else {}

    users_work = users_df.copy() if users_df is not None and not users_df.empty else pd.DataFrame(columns=["username", "role", "branch", "created_at"])
    if not users_work.empty and "username" in users_work.columns:
        users_work["username"] = users_work["username"].astype(str).str.strip()
        if "created_at" in users_work.columns:
            users_work["created_at"] = pd.to_datetime(users_work["created_at"], errors="coerce")

    ratings_work = ratings_df.copy() if ratings_df is not None and not ratings_df.empty else pd.DataFrame(columns=["rater", "rated", "score", "created_at", "branch"])
    if not ratings_work.empty:
        for col in ["rater", "rated"]:
            if col in ratings_work.columns:
                ratings_work[col] = ratings_work[col].astype(str).str.strip()
        ratings_work["score"] = pd.to_numeric(ratings_work.get("score"), errors="coerce")
        if "created_at" in ratings_work.columns:
            ratings_work["created_at"] = pd.to_datetime(ratings_work["created_at"], errors="coerce")

    attendance_work = attendance_df.copy() if attendance_df is not None and not attendance_df.empty else pd.DataFrame(columns=["username", "date", "clock_in", "clock_out", "status"])
    if not attendance_work.empty:
        attendance_work["username"] = attendance_work["username"].astype(str).str.strip()
        for col in ["date", "clock_in", "clock_out"]:
            if col in attendance_work.columns:
                attendance_work[col] = pd.to_datetime(attendance_work[col], errors="coerce")

    warnings_work = warnings_df.copy() if warnings_df is not None and not warnings_df.empty else pd.DataFrame(columns=["username", "created_at"])
    if not warnings_work.empty:
        warnings_work["username"] = warnings_work["username"].astype(str).str.strip()
        if "created_at" in warnings_work.columns:
            warnings_work["created_at"] = pd.to_datetime(warnings_work["created_at"], errors="coerce")

    leaves_work = leaves_df.copy() if leaves_df is not None and not leaves_df.empty else pd.DataFrame(columns=["username", "status", "reviewed_at", "start_date"])
    if not leaves_work.empty:
        leaves_work["username"] = leaves_work["username"].astype(str).str.strip()
        for col in ["reviewed_at", "start_date", "end_date"]:
            if col in leaves_work.columns:
                leaves_work[col] = pd.to_datetime(leaves_work[col], errors="coerce")

    people = set()
    if not users_work.empty and "username" in users_work.columns:
        people.update(users_work["username"].dropna().astype(str).tolist())
    if not ratings_work.empty:
        if "rated" in ratings_work.columns:
            people.update(ratings_work["rated"].dropna().astype(str).tolist())
        if "rater" in ratings_work.columns:
            people.update(ratings_work["rater"].dropna().astype(str).tolist())
    if not attendance_work.empty and "username" in attendance_work.columns:
        people.update(attendance_work["username"].dropna().astype(str).tolist())

    people = sorted([p for p in people if str(p).strip()])
    if not people:
        return {"summary": {}, "rows": [], "monthly_summary": [], "date_scope": {}}

    role_lookup = {}
    branch_lookup = {}
    if not users_work.empty:
        if "role" in users_work.columns:
            role_lookup = dict(zip(users_work["username"].astype(str), users_work["role"].fillna("").astype(str).str.lower()))
        if "branch" in users_work.columns:
            branch_lookup = dict(zip(users_work["username"].astype(str), users_work["branch"].fillna("").astype(str)))

    group_counts = {person: 0 for person in people}
    for group in group_analysis.get("group_details", []) or []:
        members = []
        raw_members = group.get("members", [])
        if isinstance(raw_members, list):
            members.extend([str(m).strip() for m in raw_members if str(m).strip()])
        elif isinstance(raw_members, str):
            members.extend([m.strip() for m in raw_members.split(",") if m.strip()])
        for key in ["member_1", "member_2"]:
            value = str(group.get(key, "") or "").strip()
            if value:
                members.append(value)
        if not members:
            continue
        gtype = str(group.get("group_type", "") or "").lower()
        risk_level = str(group.get("risk_level", "") or "").lower()
        if gtype in ["conflict_pair", "synchronized", "dating"] or risk_level in ["warning", "high", "critical"]:
            for member in set(members):
                if member in group_counts:
                    group_counts[member] += 1

    text_counts = {
        person: {"manager": 0, "fairness": 0, "bad_seed": 0, "negativity": 0}
        for person in people
    }
    text_sources = {
        "favoritism_analysis": intelligence.get("favoritism_analysis", []),
        "power_abuse_analysis": intelligence.get("power_abuse_analysis", []),
        "peer_gangup_analysis": intelligence.get("peer_gangup_analysis", []),
        "isolation_analysis": intelligence.get("isolation_analysis", []),
        "critical_alerts": intelligence.get("critical_alerts", []),
        "recommendations": intelligence.get("recommendations", []),
    }
    for _, entries in text_sources.items():
        for entry in entries or []:
            entry_text = str(entry or "").strip()
            if not entry_text:
                continue
            upper = entry_text.upper()
            for person in people:
                if not _text_mentions_employee(entry_text, person):
                    continue
                if any(token in upper for token in ["ADMIN BIAS", "FAVORITISM", "POWER ABUSE", "RETALIATION", "DISCIPLINE TARGETING"]):
                    text_counts[person]["manager"] += 1
                if any(token in upper for token in ["LEAVE PRESSURE", "FAVOR PROTECTION", "BOUNDARY RISK"]):
                    text_counts[person]["fairness"] += 1
                if any(token in upper for token in ["BAD SEED", "TOXIC"]):
                    text_counts[person]["bad_seed"] += 1
                if any(token in upper for token in ["PEER GANG-UP", "PEER TARGETING", "CLIQUE", "GROUP", "ISOLATION", "CONFLICT"]):
                    text_counts[person]["negativity"] += 1

    range_start = _coerce_date_boundary(analysis_start, end_of_day=False)
    range_end = _coerce_date_boundary(analysis_end, end_of_day=True) or datetime.now()
    selected_days = max((range_end.date() - range_start.date()).days + 1, 1) if range_start else 90

    def _window_mask(df, date_col, start=None, end=None):
        if df is None or df.empty or date_col not in df.columns:
            return pd.DataFrame()
        work = df[df[date_col].notna()].copy()
        if work.empty:
            return work
        if start is not None:
            work = work[work[date_col] >= pd.Timestamp(start)]
        if end is not None:
            work = work[work[date_col] <= pd.Timestamp(end)]
        return work

    def _score_person_for_period(person, start=None, end=None, days=None):
        end = end or range_end
        if start is None and days is not None:
            start = end - timedelta(days=int(max(days, 1)))
        if start is None:
            start = end - timedelta(days=90)
        previous_start = start - (end - start)

        received_all = ratings_work[ratings_work["rated"] == person].copy() if not ratings_work.empty and "rated" in ratings_work.columns else pd.DataFrame()
        received_recent = _window_mask(received_all, "created_at", start, end) if not received_all.empty and "created_at" in received_all.columns else received_all.copy()
        received_previous = _window_mask(received_all, "created_at", previous_start, start - timedelta(seconds=1)) if not received_all.empty and "created_at" in received_all.columns else pd.DataFrame()

        if not received_recent.empty:
            recent_avg = _safe_float(received_recent["score"].mean(), 100.0)
        elif not received_all.empty:
            recent_avg = _safe_float(received_all["score"].mean(), 100.0)
        else:
            recent_avg = 100.0

        if not received_previous.empty:
            baseline_avg = _safe_float(received_previous["score"].mean(), recent_avg)
        elif not received_all.empty:
            baseline_avg = _safe_float(received_all["score"].mean(), recent_avg)
        else:
            baseline_avg = recent_avg

        decline_points = round(max(baseline_avg - recent_avg, 0.0), 2)
        low_score_count = int((received_recent["score"] < 50).sum()) if not received_recent.empty else 0

        late_days = 0
        absence_signals = 0
        early_clockouts = 0
        if not attendance_work.empty:
            emp_att = attendance_work[attendance_work["username"] == person].copy()
            date_col = "date" if "date" in emp_att.columns else "clock_in" if "clock_in" in emp_att.columns else None
            recent_att = _window_mask(emp_att, date_col, start, end) if date_col else emp_att
            if not recent_att.empty:
                if "status" in recent_att.columns:
                    recent_status = recent_att["status"].astype(str).str.upper()
                    late_days = int((recent_status == "LATE").sum())
                    absence_signals = int(recent_status.isin(["ABSENT", "NO SHOW", "NO-SHOW", "MISS"]).sum())
                if "clock_in" in recent_att.columns:
                    late_days = max(late_days, int((recent_att["clock_in"].dt.hour > 9).fillna(False).sum()))
                    absence_signals += int(recent_att["clock_in"].isna().sum())
                if "clock_out" in recent_att.columns:
                    early_clockouts = int((recent_att["clock_out"].dt.hour < 18).fillna(False).sum())

        warning_count = 0
        if not warnings_work.empty:
            emp_warn = warnings_work[warnings_work["username"] == person].copy()
            recent_warn = _window_mask(emp_warn, "created_at", start, end) if "created_at" in emp_warn.columns else emp_warn
            warning_count = int(len(recent_warn))

        leave_requests = 0
        leave_rejections = 0
        if not leaves_work.empty:
            emp_leave = leaves_work[leaves_work["username"] == person].copy()
            leave_date_col = "reviewed_at" if "reviewed_at" in emp_leave.columns else "start_date" if "start_date" in emp_leave.columns else None
            recent_leave = _window_mask(emp_leave, leave_date_col, start, end) if leave_date_col else emp_leave
            leave_requests = int(len(recent_leave))
            if not recent_leave.empty and "status" in recent_leave.columns:
                leave_rejections = int(recent_leave["status"].fillna("").astype(str).str.lower().isin(["rejected", "denied"]).sum())

        manager_pattern_count = int(text_counts.get(person, {}).get("manager", 0))
        if not received_recent.empty and "rater" in received_recent.columns:
            manager_work = received_recent.copy()
            manager_work["rater_role"] = manager_work["rater"].map(role_lookup).fillna("")
            manager_scores = manager_work[manager_work["rater_role"].isin(["admin", "manager"])]["score"]
            peer_scores = manager_work[~manager_work["rater_role"].isin(["admin", "manager"])]["score"]
            if len(manager_scores) >= 2 and int((manager_scores < 50).sum()) >= 2:
                manager_pattern_count += 1
            if len(manager_scores) >= 2 and not peer_scores.empty and (float(peer_scores.mean()) - float(manager_scores.mean())) >= 12:
                manager_pattern_count += 1

        leave_fairness_count = int(text_counts.get(person, {}).get("fairness", 0))
        if leave_rejections >= 1:
            leave_fairness_count += 1

        rating_out_pattern_count = 0
        bad_seed_count = int(text_counts.get(person, {}).get("bad_seed", 0))
        if not ratings_work.empty and "rater" in ratings_work.columns:
            given_all = ratings_work[ratings_work["rater"] == person].copy()
            given_recent = _window_mask(given_all, "created_at", start, end) if not given_all.empty and "created_at" in given_all.columns else given_all
            if not given_recent.empty:
                very_low_given = int((given_recent["score"] < 45).sum())
                extreme_high_given = int((given_recent["score"] > 85).sum())
                target_summary = given_recent.groupby("rated")["score"].mean() if "rated" in given_recent.columns else pd.Series(dtype=float)
                if very_low_given >= 3:
                    rating_out_pattern_count += 1
                if len(target_summary[target_summary < 45]) >= 2:
                    rating_out_pattern_count += 1
                if extreme_high_given >= 3 and len(target_summary[target_summary > 85]) <= 2:
                    rating_out_pattern_count += 1
                if very_low_given >= 3 and extreme_high_given >= 1:
                    bad_seed_count += 1

        negative_group_count = int(group_counts.get(person, 0)) + int(text_counts.get(person, {}).get("negativity", 0))

        scorecard = _build_employee_scorecard(
            recent_avg,
            decline_points,
            low_score_count,
            late_days,
            absence_signals,
            early_clockouts,
            warning_count,
            manager_pattern_count,
            leave_fairness_count,
            bad_seed_count,
            rating_out_pattern_count,
            negative_group_count,
        )
        return {
            "scorecard": scorecard,
            "recent_avg": round(recent_avg, 1),
            "baseline_avg": round(baseline_avg, 1),
        }

    rows = []
    for person in people:
        selected = _score_person_for_period(person, start=range_start, end=range_end, days=selected_days)
        previous_selected = _score_person_for_period(
            person,
            start=(range_start - timedelta(days=selected_days)) if range_start else (range_end - timedelta(days=selected_days * 2)),
            end=(range_start - timedelta(seconds=1)) if range_start else (range_end - timedelta(days=selected_days)),
        )
        weekly = _score_person_for_period(person, days=7, end=range_end)
        monthly = _score_person_for_period(person, days=30, end=range_end)
        two_month = _score_person_for_period(person, days=60, end=range_end)
        three_month = _score_person_for_period(person, days=90, end=range_end)

        role_name = str(role_lookup.get(person, "employee") or "employee").title()
        branch_name = str(branch_lookup.get(person, "-") or "-")
        selected_score = float(selected["scorecard"].get("overall", 100.0))
        current_score = float(monthly["scorecard"].get("overall", 100.0))
        range_delta = round(selected_score - float(previous_selected["scorecard"].get("overall", 100.0)), 1)
        trend_delta = round(current_score - float(three_month["scorecard"].get("overall", 100.0)), 1)
        if trend_delta >= 2:
            trend_label = "Improving"
        elif trend_delta <= -2:
            trend_label = "Declining"
        else:
            trend_label = "Stable"

        rows.append({
            "Name": person,
            "Role": role_name,
            "Branch": branch_name,
            "Selected Range": round(selected_score, 1),
            "Weekly": round(float(weekly["scorecard"].get("overall", 100.0)), 1),
            "Monthly": round(current_score, 1),
            "2 Months": round(float(two_month["scorecard"].get("overall", 100.0)), 1),
            "3 Months": round(float(three_month["scorecard"].get("overall", 100.0)), 1),
            "Current Band": str(selected["scorecard"].get("band", monthly["scorecard"].get("band", "Strong"))),
            "Trend": trend_label,
            "Range Δ vs Previous": range_delta,
            "Delta 30d vs 90d": trend_delta,
            "Current Avg Rating": selected.get("recent_avg", 100.0),
        })

    rows = sorted(rows, key=lambda row: (row.get("Selected Range", 100.0), row.get("Monthly", 100.0), row.get("Weekly", 100.0)))

    workforce_df = pd.DataFrame(rows)
    employee_df = workforce_df[workforce_df["Role"].str.lower() == "employee"] if not workforce_df.empty else pd.DataFrame()
    manager_df = workforce_df[workforce_df["Role"].str.lower().isin(["admin", "manager"])] if not workforce_df.empty else pd.DataFrame()

    avg_selected = round(float(workforce_df["Selected Range"].mean()), 1) if not workforce_df.empty else 100.0
    avg_employees = round(float(employee_df["Selected Range"].mean()), 1) if not employee_df.empty else avg_selected
    avg_managers = round(float(manager_df["Selected Range"].mean()), 1) if not manager_df.empty else avg_selected
    below_60 = int((workforce_df["Selected Range"] < 60).sum()) if not workforce_df.empty else 0

    scope_label = (
        f"{range_start.strftime('%Y-%m-%d')} to {range_end.strftime('%Y-%m-%d')}"
        if range_start else f"up to {range_end.strftime('%Y-%m-%d')}"
    )
    monthly_summary = [
        f"Average workforce score for {scope_label} is {avg_selected:.1f}/100.",
        f"Employees average {avg_employees:.1f}/100 while managers average {avg_managers:.1f}/100.",
        f"{below_60} user(s) are below 60/100 and may require super admin review.",
    ]
    if not workforce_df.empty:
        best_improver = workforce_df.sort_values("Range Δ vs Previous", ascending=False).iloc[0]
        biggest_drop = workforce_df.sort_values("Range Δ vs Previous", ascending=True).iloc[0]
        monthly_summary.append(
            f"Best improvement in selected range: {best_improver['Name']} ({best_improver['Range Δ vs Previous']:+.1f} points vs previous range)."
        )
        monthly_summary.append(
            f"Biggest decline in selected range: {biggest_drop['Name']} ({biggest_drop['Range Δ vs Previous']:+.1f} points vs previous range)."
        )

    return {
        "summary": {
            "average_monthly": avg_selected,
            "employees_average": avg_employees,
            "managers_average": avg_managers,
            "below_60": below_60,
            "total_people": int(len(workforce_df)),
        },
        "rows": rows,
        "monthly_summary": monthly_summary,
        "date_scope": {
            "start": range_start.strftime('%Y-%m-%d') if range_start else "All available",
            "end": range_end.strftime('%Y-%m-%d'),
            "days": selected_days,
        },
    }


def _build_adaptive_recommendations(
    ratings_df,
    attendance_df,
    users_df,
    payments_df,
    monthly_trends,
    business_intelligence,
    branch_action_plan,
    base_recommendations,
):
    recs = []

    ratings_count = int(len(ratings_df)) if ratings_df is not None and not ratings_df.empty else 0
    attendance_count = int(len(attendance_df)) if attendance_df is not None and not attendance_df.empty else 0
    users_count = int(len(users_df)) if users_df is not None and not users_df.empty else 0
    payments_count = int(len(payments_df)) if payments_df is not None and not payments_df.empty else 0

    month_depth = 0
    if monthly_trends and monthly_trends.get("monthly_rows"):
        month_depth = len(monthly_trends.get("monthly_rows", []))

    drift_signals = []
    drift_score = 0

    # Detect behavior drift from recent window vs baseline window.
    if ratings_df is not None and not ratings_df.empty and "created_at" in ratings_df.columns and "score" in ratings_df.columns:
        ratings_w = ratings_df.copy()
        ratings_w["created_at"] = pd.to_datetime(ratings_w["created_at"], errors="coerce")
        ratings_w["score"] = pd.to_numeric(ratings_w["score"], errors="coerce")
        ratings_w = ratings_w.dropna(subset=["created_at", "score"])
        if not ratings_w.empty:
            now = datetime.now()
            recent_cutoff = now - timedelta(days=30)
            baseline_cutoff = now - timedelta(days=120)
            recent = ratings_w[ratings_w["created_at"] >= recent_cutoff]
            baseline = ratings_w[(ratings_w["created_at"] >= baseline_cutoff) & (ratings_w["created_at"] < recent_cutoff)]

            if not recent.empty and not baseline.empty:
                recent_avg = float(recent["score"].mean())
                baseline_avg = float(baseline["score"].mean())
                delta = recent_avg - baseline_avg
                if abs(delta) >= 3:
                    drift_score += 1
                    trend_word = "up" if delta > 0 else "down"
                    drift_signals.append(
                        f"Recent rating behavior shifted {trend_word} by {abs(delta):.1f} points versus prior baseline."
                    )

                # Dynamic volatility threshold (higher volatility implies instability).
                baseline_std = float(baseline["score"].std()) if len(baseline) > 1 else 0.0
                recent_std = float(recent["score"].std()) if len(recent) > 1 else 0.0
                if baseline_std > 0 and recent_std > (baseline_std * 1.35):
                    drift_score += 1
                    drift_signals.append("Score volatility increased materially in the recent period.")

    if attendance_df is not None and not attendance_df.empty and "status" in attendance_df.columns:
        att_w = attendance_df.copy()
        date_col = "date" if "date" in att_w.columns else "clock_in" if "clock_in" in att_w.columns else None
        if date_col:
            att_w[date_col] = pd.to_datetime(att_w[date_col], errors="coerce")
            att_w = att_w.dropna(subset=[date_col])
            if not att_w.empty:
                now = datetime.now()
                recent_cutoff = now - timedelta(days=30)
                baseline_cutoff = now - timedelta(days=120)
                recent_att = att_w[att_w[date_col] >= recent_cutoff]
                baseline_att = att_w[(att_w[date_col] >= baseline_cutoff) & (att_w[date_col] < recent_cutoff)]

                def _late_rate(df):
                    if df is None or df.empty:
                        return 0.0
                    s = df["status"].astype(str).str.upper()
                    return float((s == "LATE").sum()) / max(len(df), 1)

                if not recent_att.empty and not baseline_att.empty:
                    rr = _late_rate(recent_att)
                    br = _late_rate(baseline_att)
                    if (rr - br) >= 0.08:
                        drift_score += 1
                        drift_signals.append("Late attendance pattern increased compared with previous baseline.")

    if ratings_count < 40 or month_depth < 2:
        maturity_stage = "early"
    elif ratings_count < 160 or month_depth < 4:
        maturity_stage = "growing"
    else:
        maturity_stage = "mature"

    recs.append(
        f"DATA STAGE: {maturity_stage.upper()} (ratings={ratings_count}, months={month_depth}, attendance={attendance_count}, payments={payments_count})."
    )

    if drift_signals:
        recs.append(f"DRIFT STATUS: CHANGING ({len(drift_signals)} signal(s) detected).")
        recs.extend([f"DRIFT: {s}" for s in drift_signals[:3]])
    else:
        recs.append("DRIFT STATUS: STABLE (no major recent behavior shifts detected).")

    if maturity_stage == "early":
        recs.extend([
            "ACTION: Prioritize data capture consistency for 30 days before heavy strategic changes.",
            "ACTION: Ensure each branch submits enough ratings weekly (target: at least 10 per branch per week).",
            "GUIDE: Use recommendations as directional signals while evidence is still limited.",
        ])
    elif maturity_stage == "growing":
        recs.extend([
            "ACTION: Start branch-level A/B operating interventions and compare results month-over-month.",
            "ACTION: Track manager impact per branch using trend lines, not single snapshots.",
            "GUIDE: Escalate only repeated issues that persist across at least 2 periods.",
        ])
    else:
        recs.extend([
            "ACTION: Apply predictive planning for staffing, training, and branch expansion from established trends.",
            "ACTION: Standardize the best branch playbook and enforce KPI-linked manager accountability.",
            "GUIDE: Use rolling 90-day performance windows for board-level decisions.",
        ])

    health_status = str(business_intelligence.get("business_health_status", "")).lower()
    payment_trend = str(business_intelligence.get("payment_trend", {}).get("trend_direction", "Stable")).lower()
    trend_notes = monthly_trends.get("trend_summary", []) if isinstance(monthly_trends, dict) else []

    if health_status == "needs attention":
        recs.append("ACTION: Freeze expansion and focus on recovery in weak branches until health stabilizes.")
    elif health_status == "strong":
        recs.append("KEEP: Business health is strong; scale high-performing routines with controlled governance.")

    if payment_trend == "declining":
        recs.append("ACTION: Trigger revenue recovery plan: collections follow-up, branch accountability, and weekly cash tracking.")
    elif payment_trend == "growing":
        recs.append("KEEP: Collection trend is positive; reinvest in retention and quality improvement to sustain growth.")

    if trend_notes:
        recs.extend([f"TREND: {note}" for note in trend_notes[:3]])

    if drift_score >= 2:
        recs.append("ACTION: Move to weekly governance cadence until drift stabilizes (rapid check-ins, corrective actions, and owner tracking).")
    elif drift_score == 1:
        recs.append("GUIDE: Increase monitoring frequency for affected KPIs and validate if the shift persists next cycle.")

    target_branch = branch_action_plan.get("target_branch") if isinstance(branch_action_plan, dict) else None
    branch_actions = branch_action_plan.get("actions", []) if isinstance(branch_action_plan, dict) else []
    if target_branch and branch_actions:
        recs.append(f"PRIORITY BRANCH: {target_branch} requires focused intervention.")
        recs.extend([f"BRANCH ACTION: {a}" for a in branch_actions[:2]])

    if users_count > 0 and attendance_count == 0:
        recs.append("ACTION: Attendance data is missing; enforce check-in usage before relying on attendance-driven intelligence.")

    for base_rec in (base_recommendations or []):
        if isinstance(base_rec, str) and base_rec.strip():
            recs.append(base_rec.strip())

    deduped = []
    seen = set()
    for item in recs:
        norm = item.lower().strip()
        if norm and norm not in seen:
            deduped.append(item)
            seen.add(norm)

    return deduped[:20], {
        "stage": maturity_stage,
        "ratings_count": ratings_count,
        "attendance_count": attendance_count,
        "payments_count": payments_count,
        "users_count": users_count,
        "history_months": month_depth,
        "drift_score": drift_score,
        "drift_signals": drift_signals[:5],
    }


# =====================================================
# SUPER ADMIN DASHBOARD - INTELLIGENT VIEW
# =====================================================
def get_super_admin_dashboard(organization, branch=None, super_admin_user=None, start_date=None, end_date=None):
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
        "employee_risk_overview": {},
        "workforce_scorecards": {},
        "date_range": {
            "start": str(start_date) if start_date else "All available",
            "end": str(end_date) if end_date else datetime.now().strftime("%Y-%m-%d"),
        },
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
    if branch:
        cursor.execute("""
        SELECT * FROM leaves
        WHERE organization = ? AND branch = ?
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT * FROM leaves
        WHERE organization = ?
        """, (organization,))
    
    leaves_data = cursor.fetchall()
    leaves_cols = ["id", "username", "organization", "branch", "start_date", "end_date", "reason", "status", "approved_by", "admin_note", "reviewed_at"]
    leaves_df = pd.DataFrame(leaves_data, columns=leaves_cols) if leaves_data else pd.DataFrame()

    # =========================
    # GET WARNINGS
    # =========================
    if branch:
        cursor.execute("""
        SELECT * FROM warnings
        WHERE organization = ? AND branch = ?
        ORDER BY created_at DESC
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT * FROM warnings
        WHERE organization = ?
        ORDER BY created_at DESC
        """, (organization,))

    warnings_data = cursor.fetchall()
    warnings_cols = ["id", "username", "organization", "branch", "type", "message", "created_at"]
    warnings_df = pd.DataFrame(warnings_data, columns=warnings_cols) if warnings_data else pd.DataFrame()

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

    ratings_full_df = ratings_df.copy() if not ratings_df.empty else pd.DataFrame(columns=ratings_cols)
    attendance_full_df = attendance_df.copy() if not attendance_df.empty else pd.DataFrame(columns=attendance_cols)
    warnings_full_df = warnings_df.copy() if not warnings_df.empty else pd.DataFrame(columns=warnings_cols)
    leaves_full_df = leaves_df.copy() if not leaves_df.empty else pd.DataFrame(columns=leaves_cols)

    ratings_df = _filter_dataframe_by_date_range(ratings_full_df, ["created_at"], start_date, end_date)
    attendance_df = _filter_dataframe_by_date_range(attendance_full_df, ["date", "clock_in"], start_date, end_date)
    warnings_df = _filter_dataframe_by_date_range(warnings_full_df, ["created_at"], start_date, end_date)
    leaves_df = _filter_dataframe_by_date_range(leaves_full_df, ["reviewed_at", "start_date", "end_date"], start_date, end_date)
    payments_df = _filter_dataframe_by_date_range(payments_df, ["created_at"], start_date, end_date)
    messages_df = _filter_dataframe_by_date_range(messages_df, ["created_at"], start_date, end_date)

    if users_df is not None and not users_df.empty and "role" in users_df.columns:
        users_df = users_df.copy()
        users_df["role_norm"] = users_df["role"].map(_normalize_role_name)
        users_df = users_df[~users_df["role_norm"].isin({"super_admin", "master"})].copy()
    if branch:
        users_df = _filter_scope_frame(users_df, branch, ["branch"])
        branches_df = _filter_scope_frame(branches_df, branch, ["name", "branch"])

    allowed_usernames = set()
    if users_df is not None and not users_df.empty and "username" in users_df.columns:
        allowed_usernames = {
            str(name).strip()
            for name in users_df["username"].dropna().astype(str).tolist()
            if str(name).strip()
        }

    ratings_full_df = _filter_scope_frame(ratings_full_df, branch, ["branch"])
    ratings_df = _filter_scope_frame(ratings_df, branch, ["branch"])
    attendance_full_df = _filter_scope_frame(attendance_full_df, branch, ["branch"])
    attendance_df = _filter_scope_frame(attendance_df, branch, ["branch"])
    warnings_full_df = _filter_scope_frame(warnings_full_df, branch, ["branch"])
    warnings_df = _filter_scope_frame(warnings_df, branch, ["branch"])
    leaves_full_df = _filter_scope_frame(leaves_full_df, branch, ["branch"])
    leaves_df = _filter_scope_frame(leaves_df, branch, ["branch"])
    messages_df = _filter_scope_frame(messages_df, branch, ["branch"])

    ratings_full_df = _filter_user_scoped_frame(ratings_full_df, allowed_usernames, ["rater", "rated"])
    ratings_df = _filter_user_scoped_frame(ratings_df, allowed_usernames, ["rater", "rated"])
    attendance_full_df = _filter_user_scoped_frame(attendance_full_df, allowed_usernames, ["username"])
    attendance_df = _filter_user_scoped_frame(attendance_df, allowed_usernames, ["username"])
    warnings_full_df = _filter_user_scoped_frame(warnings_full_df, allowed_usernames, ["username"])
    warnings_df = _filter_user_scoped_frame(warnings_df, allowed_usernames, ["username"])
    leaves_full_df = _filter_user_scoped_frame(leaves_full_df, allowed_usernames, ["username"])
    leaves_df = _filter_user_scoped_frame(leaves_df, allowed_usernames, ["username"])
    messages_df = _filter_user_scoped_frame(messages_df, allowed_usernames, ["from_user", "to_user"])
    
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
        warnings_df if not warnings_df.empty else None,
    )
    
    # =========================
    # EXECUTIVE SUMMARY
    # =========================
    admin_count = 0
    if users_df is not None and not users_df.empty:
        if "role_norm" in users_df.columns:
            admin_count = int((users_df["role_norm"] == "admin").sum())
        elif "role" in users_df.columns:
            admin_count = int(users_df["role"].astype(str).str.lower().isin(["admin", "manager"]).sum())

    dashboard["executive_summary"] = {
        "summary_points": intelligence.get("executive_summary", []),
        "total_employees": len(users_df),
        "admins_count": admin_count,
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
    dashboard["employee_risk_overview"] = _build_employee_risk_overview(
        ratings_df,
        attendance_df if not attendance_df.empty else None,
        warnings_df if not warnings_df.empty else None,
        users_df if not users_df.empty else None,
        leaves_df if not leaves_df.empty else None,
        intelligence,
        group_data,
    )
    dashboard["workforce_scorecards"] = _build_workforce_scorecards(
        ratings_full_df,
        attendance_full_df if not attendance_full_df.empty else None,
        warnings_full_df if not warnings_full_df.empty else None,
        users_df if not users_df.empty else None,
        leaves_full_df if not leaves_full_df.empty else None,
        intelligence,
        group_data,
        analysis_start=start_date,
        analysis_end=end_date,
    )
    
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

    adaptive_recs, rec_context = _build_adaptive_recommendations(
        ratings_df,
        attendance_df if not attendance_df.empty else None,
        users_df if not users_df.empty else None,
        payments_df if not payments_df.empty else None,
        dashboard["monthly_trends"],
        dashboard["business_intelligence"],
        dashboard["branch_action_plan"],
        intelligence.get("recommendations", [])[:15],
    )
    dashboard["recommendations"] = adaptive_recs
    dashboard["recommendation_context"] = rec_context
    dashboard["favoritism_analysis"] = intelligence.get("favoritism_analysis", [])
    dashboard["isolation_analysis"] = intelligence.get("isolation_analysis", [])
    dashboard["power_abuse_analysis"] = intelligence.get("power_abuse_analysis", [])
    dashboard["peer_gangup_analysis"] = intelligence.get("peer_gangup_analysis", [])
    
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
