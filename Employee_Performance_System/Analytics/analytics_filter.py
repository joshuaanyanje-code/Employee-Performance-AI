"""
Analytics Filtering System

Ensures that super_admin and master_admin are excluded from all analytics views.
Also enables role-specific filtering so admins cannot see super admin, employees cannot see admins.
"""

try:
    from ..database.db import get_connection
except ImportError:
    from database.db import get_connection


# =====================================================
# ROLE-BASED EXCLUSION RULES
# =====================================================
# master  → sees EVERYBODY (no exclusions — boss level)
# super_admin → sees admins + employees, NOT master/super_admin
# admin   → sees employees only, NOT master/super_admin
# employee → sees only fellow employees
ROLE_ALIASES = {
    "master": {"master", "master_admin", "owner", "overall"},
    "super_admin": {"super_admin", "superadmin"},
    "admin": {"admin", "manager"},
    "employee": {"employee"},
    "kiosk": {"kiosk"},
}

ROLE_EXCLUSIONS = {
    "master": [],                                  # BOSS — full visibility
    "super_admin": ["super_admin", "master"],   # no peer/boss data
    "admin": ["super_admin", "master"],         # no super_admin/master data
    "employee": ["super_admin", "master", "admin"],
    "kiosk": ["super_admin", "master", "admin"],
}


def normalize_role_name(value):
    role = str(value or "").strip().lower()
    for canonical, aliases in ROLE_ALIASES.items():
        if role in aliases:
            return canonical
    return role


def expand_exclusion_roles(roles):
    expanded = set()
    for role in roles or []:
        canonical = normalize_role_name(role)
        expanded.update(ROLE_ALIASES.get(canonical, {canonical}))
    return expanded


def get_exclusion_roles(viewer_role):
    """
    Returns list of roles that should be excluded from analytics for the given viewer role.

    master      → [] — sees EVERYTHING (boss, full access)
    super_admin → excludes master and super_admin from data
    admin       → excludes master and super_admin from data
    employee    → excludes master, super_admin, admin from data
    """
    canonical_role = normalize_role_name(viewer_role)
    return ROLE_EXCLUSIONS.get(canonical_role, [])


# =====================================================
# MASTER ADMIN — FULL BYPASS (no filtering)
# =====================================================
def filter_for_master(df):
    """
    Master admin is the boss and sees ALL data with zero restrictions.
    This is an explicit passthrough — no rows removed.
    """
    return df


# =====================================================
# FILTER RATINGS (EXCLUDE ROLES)
# =====================================================
def filter_ratings_by_role(ratings_df, viewer_role=None):
    """
    Filters ratings DataFrame to exclude users based on viewing role.
    Master admin bypasses all filtering — sees full dataset.
    """
    
    if ratings_df is None or ratings_df.empty:
        return ratings_df
    
    # Master admin — no filtering, sees everything
    if normalize_role_name(viewer_role) == "master":
        return ratings_df
    
    exclude_roles = list(expand_exclusion_roles(get_exclusion_roles(viewer_role or "admin")))
    if not exclude_roles:
        return ratings_df.copy()
    
    try:
        from ..database.db import get_connection
    except Exception:
        from database.db import get_connection
    
    conn = get_connection()
    cursor = conn.cursor()
    
    placeholders = ",".join(["?" for _ in exclude_roles])
    cursor.execute(
        f"SELECT username FROM users WHERE LOWER(TRIM(COALESCE(role, ''))) IN ({placeholders})",
        tuple(exclude_roles),
    )
    
    excluded_users = {str(u[0]).strip() for u in cursor.fetchall() if u and u[0] is not None}
    conn.close()
    
    filtered_df = ratings_df[
        ~ratings_df["rater"].astype(str).str.strip().isin(excluded_users)
        & ~ratings_df["rated"].astype(str).str.strip().isin(excluded_users)
    ].copy()
    
    return filtered_df


# =====================================================
# FILTER USERS (EXCLUDE ROLES)
# =====================================================
def filter_users_by_role(users_df, viewer_role=None):
    """
    Filters users DataFrame to exclude users based on viewing role.
    Master admin bypasses all filtering — sees full user list.
    """
    
    if users_df is None or users_df.empty:
        return users_df
    
    if normalize_role_name(viewer_role) == "master":
        return users_df
    
    exclude_roles = {normalize_role_name(r) for r in expand_exclusion_roles(get_exclusion_roles(viewer_role or "admin"))}
    role_norm = users_df["role"].astype(str).map(normalize_role_name)
    filtered_df = users_df[~role_norm.isin(exclude_roles)].copy()
    
    if "exclude_from_analytics" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["exclude_from_analytics"].fillna(0).astype(int) != 1].copy()
    
    return filtered_df


# =====================================================
# FILTER ATTENDANCE (EXCLUDE ROLES)
# =====================================================
def filter_attendance_by_role(attendance_df, viewer_role=None):
    """
    Filters attendance DataFrame to exclude users based on viewing role.
    Master admin bypasses all filtering — sees full attendance data.
    """
    
    if attendance_df is None or attendance_df.empty:
        return attendance_df
    
    # Master admin — no filtering
    if viewer_role == "master":
        return attendance_df
    
    # Get excluded users
    try:
        from ..database.db import get_connection
    except:
        from database.db import get_connection
    
    exclude_roles = get_exclusion_roles(viewer_role or "admin")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    placeholders = ",".join(["?" for _ in exclude_roles])
    cursor.execute(f"""
    SELECT username FROM users WHERE role IN ({placeholders})
    """, exclude_roles)
    
    excluded_users = set([u[0] for u in cursor.fetchall()])
    conn.close()
    
    # Filter out excluded users
    filtered_df = attendance_df[~attendance_df["username"].isin(excluded_users)].copy()
    
    return filtered_df


# =====================================================
# FILTER MESSAGES (EXCLUDE ROLES)
# =====================================================
def filter_messages_by_role(messages_df, viewer_role=None):
    """
    Filters messages to exclude conversations involving super_admin/master.
    Master admin bypasses all filtering — sees all messages.
    """
    
    if messages_df is None or messages_df.empty:
        return messages_df
    
    # Master admin — no filtering
    if viewer_role == "master":
        return messages_df
    
    # Get excluded users
    try:
        from ..database.db import get_connection
    except:
        from database.db import get_connection
    
    exclude_roles = get_exclusion_roles(viewer_role or "admin")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    placeholders = ",".join(["?" for _ in exclude_roles])
    cursor.execute(f"""
    SELECT username FROM users WHERE role IN ({placeholders})
    """, exclude_roles)
    
    excluded_users = set([u[0] for u in cursor.fetchall()])
    conn.close()
    
    # Filter messages not involving excluded users
    filtered_df = messages_df[
        ~messages_df["from_user"].isin(excluded_users) &
        ~messages_df["to_user"].isin(excluded_users)
    ].copy()
    
    return filtered_df


# =====================================================
# APPLY FILTERS TO ENTIRE DATASET
# =====================================================
def apply_role_filters_to_analytics(ratings_df, attendance_df=None, users_df=None, messages_df=None, viewer_role=None):
    """
    Applies all necessary role-based filters to analytics data.
    Master admin receives unfiltered data — full system access.

    Returns tuple: (filtered_ratings, filtered_attendance, filtered_users, filtered_messages)
    """
    
    # Master admin — return everything unfiltered
    if viewer_role == "master":
        return ratings_df, attendance_df, users_df, messages_df
    
    filtered_ratings    = filter_ratings_by_role(ratings_df, viewer_role)
    filtered_attendance = filter_attendance_by_role(attendance_df, viewer_role)
    filtered_users      = filter_users_by_role(users_df, viewer_role)
    filtered_messages   = filter_messages_by_role(messages_df, viewer_role)
    
    return filtered_ratings, filtered_attendance, filtered_users, filtered_messages


# =====================================================
# VERIFY DATA INTEGRITY (No Secret Users in Reports)
# =====================================================
def verify_no_secret_users_in_report(report_dict):
    """
    Verifies that report doesn't contain super_admin or master_admin references.
    Returns True if clean, False if found.
    """
    
    forbidden_users = ["super_admin", "master"]
    report_str = str(report_dict)
    
    for user in forbidden_users:
        if user in report_str:
            return False
    
    return True


# =====================================================
# GET VIEWER ROLE EXCLUSION LIST
# =====================================================
def get_viewer_exclusion_summary(viewer_role):
    """Returns human-readable summary of what a viewer can/cannot see."""
    
    exclude_roles = get_exclusion_roles(viewer_role)
    
    summary = {
        "viewer_role": viewer_role,
        "can_view": "Everyone" if viewer_role == "master" else "All active employees excluding higher roles",
        "cannot_view": ", ".join(exclude_roles) if exclude_roles else "None (full access)",
        "explanation": {
            "master":      "BOSS — sees everything: master, super_admin, admins, employees",
            "super_admin": "Sees admins and employees; NOT master or other super_admins",
            "admin":       "Sees employees only; NOT super_admin or master",
            "employee":    "Sees only fellow employees",
        }
    }
    
    return summary


# =====================================================
# SANITIZE ANALYTICS OUTPUT
# =====================================================
def sanitize_analytics_output(report_or_list, forbidden_roles=None, viewer_role=None):
    """
    Removes all references to forbidden roles from report/output.
    Used as final safety check before sending to frontend.
    Master admin is exempt — receives raw unredacted data.
    """
    
    # Master admin sees everything — no sanitization
    if viewer_role == "master":
        return report_or_list
    
    if forbidden_roles is None:
        forbidden_roles = ["super_admin", "master"]
    """
    Removes all references to forbidden roles from report/output.
    Used as final safety check before sending to frontend.
    """
    
    if isinstance(report_or_list, dict):
        sanitized = {}
        for key, value in report_or_list.items():
            if isinstance(value, str):
                # Remove forbidden user references
                for role in forbidden_roles:
                    value = value.replace(role, "[HIDDEN]")
                sanitized[key] = value
            elif isinstance(value, (list, dict)):
                sanitized[key] = sanitize_analytics_output(value, forbidden_roles)
            else:
                sanitized[key] = value
        return sanitized
    
    elif isinstance(report_or_list, list):
        sanitized = []
        for item in report_or_list:
            if isinstance(item, str):
                for role in forbidden_roles:
                    item = item.replace(role, "[HIDDEN]")
                sanitized.append(item)
            elif isinstance(item, (list, dict)):
                sanitized.append(sanitize_analytics_output(item, forbidden_roles))
            else:
                sanitized.append(item)
        return sanitized
    
    else:
        return report_or_list
