import json
import pandas as pd
from datetime import datetime

try:
    from ..database.db import get_connection
except ImportError:
    from database.db import get_connection

try:
    from .powermap import detect_synchronized_groups
except ImportError:
    from Analytics.powermap import detect_synchronized_groups


def _filter_by_branch_scope(df, branch, branch_col="branch"):
    if df is None or getattr(df, "empty", True) or not branch:
        return df
    if branch_col not in df.columns:
        return df
    scope = str(branch or "").strip()
    return df[df[branch_col].fillna("").astype(str).str.strip() == scope].copy()


# =====================================================
# GROUP DEMOGRAPHICS & ANALYSIS
# =====================================================
def analyze_group_demographics(ratings_df, attendance_df=None, users_df=None, organization=None, branch=None):
    """
    Analyzes group demographics including gender distribution, group types, and risk levels.
    Stores findings in database for super admin viewing.
    """
    
    analysis = {
        "total_groups": 0,
        "groups_by_type": {},
        "gender_distribution": {
            "total_employees": 0,
            "male_count": 0,
            "female_count": 0,
            "other_count": 0,
        },
        "group_details": [],
        "high_risk_groups": [],
    }
    
    ratings_df = _filter_by_branch_scope(ratings_df, branch, "branch")
    attendance_df = _filter_by_branch_scope(attendance_df, branch, "branch")
    users_df = _filter_by_branch_scope(users_df, branch, "branch")

    if ratings_df is None or ratings_df.empty:
        return analysis
    
    # Get gender data from users_df
    gender_map = {}
    if users_df is not None and not users_df.empty and "gender" in users_df.columns:
        gender_map = dict(zip(users_df["username"], users_df["gender"]))
    
    # Get synchronized groups
    if attendance_df is not None and not attendance_df.empty:
        sync_insights, sync_groups = detect_synchronized_groups(attendance_df, ratings_df, users_df)
        
        analysis["total_groups"] = len(sync_groups)
        
        # Analyze each group
        for group_key, group_info in sync_groups.items():
            
            members = group_info.get("members", [])
            logic_tags = list(group_info.get("logic_tags", []))
            if group_info.get("relationship_type") == "dating" or "dating" in logic_tags:
                group_type = "dating"
            elif "conflict_pair" in logic_tags:
                group_type = "conflict_pair"
            elif "synchronized" in logic_tags:
                group_type = "synchronized"
            else:
                group_type = group_info.get("type", "unknown")
            
            # Count genders
            male = sum(1 for m in members if gender_map.get(m, "").lower() == "male")
            female = sum(1 for m in members if gender_map.get(m, "").lower() == "female")
            other = len(members) - male - female
            
            # Get group's average rating
            group_ratings = ratings_df[ratings_df["rated"].isin(members)]
            avg_rating = group_ratings["score"].mean() if not group_ratings.empty else 0
            
            # Determine risk level
            risk_level = "critical" if group_type in ["dating", "relationship"] else "warning" if group_type == "conflict_pair" else "info"

            member_1 = str(members[0]) if len(members) >= 1 else ""
            member_2 = str(members[1]) if len(members) >= 2 else ""
            gender_1 = str(gender_map.get(member_1, "unknown")) if member_1 else "unknown"
            gender_2 = str(gender_map.get(member_2, "unknown")) if member_2 else "unknown"
            
            # Store in list
            group_detail = {
                "group_id": group_key,
                "group_type": group_type,
                "logic_tags": logic_tags,
                "members": members,
                "pair_label": group_info.get("pair_label", " & ".join(members)),
                "member_1": member_1,
                "member_2": member_2,
                "gender_1": gender_1,
                "gender_2": gender_2,
                "total_members": len(members),
                "male_count": male,
                "female_count": female,
                "other_count": other,
                "avg_rating": avg_rating,
                "risk_level": risk_level,
                "description": group_info.get("description", "Group detected"),
                "clock_in_count": int(group_info.get("clock_in_count", 0) or 0),
                "clock_out_count": int(group_info.get("clock_out_count", 0) or 0),
                "leave_sync_count": int(group_info.get("leave_sync_count", 0) or 0),
                "low_ratings": int(group_info.get("low_ratings", 0) or 0),
                "avg_mutual_rating": float(group_info.get("avg_mutual_rating", 0) or 0),
            }
            
            analysis["group_details"].append(group_detail)
            
            # Track by type
            if group_type not in analysis["groups_by_type"]:
                analysis["groups_by_type"][group_type] = 0
            analysis["groups_by_type"][group_type] += 1
            
            # Add to high risk if critical
            if risk_level == "critical":
                analysis["high_risk_groups"].append(group_detail)
    
    # Calculate overall gender distribution
    if users_df is not None and not users_df.empty and "gender" in users_df.columns:
        analysis["gender_distribution"]["total_employees"] = len(users_df)
        analysis["gender_distribution"]["male_count"] = len(users_df[users_df["gender"].str.lower() == "male"])
        analysis["gender_distribution"]["female_count"] = len(users_df[users_df["gender"].str.lower() == "female"])
        analysis["gender_distribution"]["other_count"] = (
            analysis["gender_distribution"]["total_employees"] - 
            analysis["gender_distribution"]["male_count"] - 
            analysis["gender_distribution"]["female_count"]
        )
    
    # Save to database
    if organization:
        save_group_demographics_to_db(organization, branch, analysis)
    
    return analysis


# =====================================================
# SAVE GROUP DEMOGRAPHICS TO DATABASE
# =====================================================
def save_group_demographics_to_db(organization, branch, demographics_data):
    """Saves group demographics findings to database for tracking."""
    
    scope_branch = str(branch or "").strip()
    if scope_branch.lower() in {"all", "all branches"}:
        scope_branch = ""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM group_demographics WHERE organization=? AND COALESCE(branch, '')=?",
        (organization, scope_branch),
    )
    
    for group in demographics_data.get("group_details", []):
        notes = json.dumps(
            {
                "pair_label": group.get("pair_label", ""),
                "member_1": group.get("member_1", ""),
                "member_2": group.get("member_2", ""),
                "gender_1": group.get("gender_1", "unknown"),
                "gender_2": group.get("gender_2", "unknown"),
                "logic_tags": group.get("logic_tags", []),
                "description": group.get("description", "Group detected"),
                "clock_in_count": int(group.get("clock_in_count", 0) or 0),
                "clock_out_count": int(group.get("clock_out_count", 0) or 0),
                "leave_sync_count": int(group.get("leave_sync_count", 0) or 0),
                "low_ratings": int(group.get("low_ratings", 0) or 0),
                "avg_mutual_rating": float(group.get("avg_mutual_rating", 0) or 0),
            },
            default=str,
        )
        
        cursor.execute("""
        INSERT INTO group_demographics(
            organization, branch, group_id, group_type, members, total_members,
            male_count, female_count, other_count, avg_rating, risk_level, detected_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            organization,
            scope_branch,
            group["group_id"],
            group["group_type"],
            ",".join(group["members"]),
            group["total_members"],
            group["male_count"],
            group["female_count"],
            group["other_count"],
            group["avg_rating"],
            group["risk_level"],
            datetime.now().isoformat(),
            notes,
        ))
    
    conn.commit()
    conn.close()


# =====================================================
# GET DEMOGRAPHIC STATISTICS
# =====================================================
def get_demographic_statistics(organization, branch=None):
    """Gets gender and group demographic statistics for dashboard."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {
        "gender_breakdown": {
            "male": 0,
            "female": 0,
            "other": 0,
            "total": 0,
        },
        "group_breakdown": {
            "synchronized": 0,
            "dating": 0,
            "conflict_pair": 0,
            "other": 0,
        },
        "high_risk": 0,
    }
    
    # Gender stats
    if branch:
        cursor.execute("""
        SELECT gender, COUNT(*) FROM users
        WHERE organization = ? AND branch = ? AND role NOT IN (?, ?)
        GROUP BY gender
        """, (organization, branch, "super_admin", "master"))
    else:
        cursor.execute("""
        SELECT gender, COUNT(*) FROM users
        WHERE organization = ? AND role NOT IN (?, ?)
        GROUP BY gender
        """, (organization, "super_admin", "master"))
    
    for gender, count in cursor.fetchall():
        gender_lower = (gender or "unknown").lower()
        if gender_lower == "male":
            stats["gender_breakdown"]["male"] = count
        elif gender_lower == "female":
            stats["gender_breakdown"]["female"] = count
        else:
            stats["gender_breakdown"]["other"] = count
    
    stats["gender_breakdown"]["total"] = sum(stats["gender_breakdown"].values())
    
    # Group type stats
    if branch:
        cursor.execute("""
        SELECT group_type, COUNT(*) FROM group_demographics
        WHERE organization = ? AND branch = ?
        GROUP BY group_type
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT group_type, COUNT(*) FROM group_demographics
        WHERE organization = ?
        GROUP BY group_type
        """, (organization,))
    
    for group_type, count in cursor.fetchall():
        if group_type in stats["group_breakdown"]:
            stats["group_breakdown"][group_type] = count
        else:
            stats["group_breakdown"]["other"] += count
    
    # High risk count
    if branch:
        cursor.execute("""
        SELECT COUNT(*) FROM group_demographics
        WHERE organization = ? AND branch = ? AND risk_level = 'critical'
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT COUNT(*) FROM group_demographics
        WHERE organization = ? AND risk_level = 'critical'
        """, (organization,))
    
    stats["high_risk"] = cursor.fetchone()[0]
    
    conn.close()
    
    return stats


# =====================================================
# GET GROUP DETAILS FOR SUPER ADMIN VIEW
# =====================================================
def get_group_details_for_super_admin(organization, branch=None, risk_level_filter=None):
    """Retrieves group details for super admin dashboard."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if branch:
        if risk_level_filter:
            cursor.execute("""
            SELECT * FROM group_demographics
            WHERE organization = ? AND branch = ? AND risk_level = ?
            ORDER BY detected_at DESC
            """, (organization, branch, risk_level_filter))
        else:
            cursor.execute("""
            SELECT * FROM group_demographics
            WHERE organization = ? AND branch = ?
            ORDER BY risk_level DESC, detected_at DESC
            """, (organization, branch))
    else:
        if risk_level_filter:
            cursor.execute("""
            SELECT * FROM group_demographics
            WHERE organization = ? AND risk_level = ?
            ORDER BY detected_at DESC
            """, (organization, risk_level_filter))
        else:
            cursor.execute("""
            SELECT * FROM group_demographics
            WHERE organization = ?
            ORDER BY risk_level DESC, detected_at DESC
            """, (organization,))
    
    groups = cursor.fetchall()
    cols = ["id", "organization", "branch", "group_id", "group_type", "members", "total_members", "male_count", "female_count", "other_count", "avg_rating", "risk_level", "detected_at", "notes"]
    
    result = []
    for g in groups:
        group_dict = dict(zip(cols, g))
        group_dict["members"] = group_dict["members"].split(",") if group_dict["members"] else []
        result.append(group_dict)
    
    conn.close()
    
    return result


# =====================================================
# CROSS-GENDER DATING DETECTION SUMMARY
# =====================================================
def get_cross_gender_relationships(organization, branch=None):
    """Gets summary of cross-gender relationships detected."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if branch:
        cursor.execute("""
        SELECT * FROM group_demographics
        WHERE organization = ? AND branch = ? AND group_type IN ('dating', 'relationship')
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT * FROM group_demographics
        WHERE organization = ? AND group_type IN ('dating', 'relationship')
        """, (organization,))
    
    relationships = cursor.fetchall()
    cols = ["id", "organization", "branch", "group_id", "group_type", "members", "total_members", "male_count", "female_count", "other_count", "avg_rating", "risk_level", "detected_at", "notes"]
    
    result = []
    for r in relationships:
        rel_dict = dict(zip(cols, r))
        rel_dict["members"] = rel_dict["members"].split(",") if rel_dict["members"] else []
        result.append(rel_dict)
    
    conn.close()
    
    return result


# =====================================================
# GENERATE GENDER DIVERSITY REPORT
# =====================================================
def generate_gender_diversity_report(organization):
    """Generates gender diversity statistics for organization."""
    
    stats = get_demographic_statistics(organization)
    
    report = {
        "organization": organization,
        "generated_at": datetime.now().isoformat(),
        "gender_breakdown": stats["gender_breakdown"],
        "group_breakdown": stats["group_breakdown"],
        "high_risk_groups": stats["high_risk"],
        "gender_percentages": {
            "male_pct": round((stats["gender_breakdown"]["male"] / max(stats["gender_breakdown"]["total"], 1)) * 100, 1),
            "female_pct": round((stats["gender_breakdown"]["female"] / max(stats["gender_breakdown"]["total"], 1)) * 100, 1),
            "other_pct": round((stats["gender_breakdown"]["other"] / max(stats["gender_breakdown"]["total"], 1)) * 100, 1),
        },
        "group_percentages": {
            group_type: round((count / max(sum(stats["group_breakdown"].values()), 1)) * 100, 1)
            for group_type, count in stats["group_breakdown"].items()
        }
    }
    
    return report
