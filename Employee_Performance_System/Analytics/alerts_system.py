import pandas as pd
from datetime import datetime, timedelta

try:
    from ..database.db import get_connection
except ImportError:
    from database.db import get_connection


# =====================================================
# ALERT SYSTEM & NOTIFICATIONS
# =====================================================
def create_alert(organization, branch, alert_type, severity, subject, message, related_user=None, assigned_to=None):
    """Creates an alert for super admin review."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    INSERT INTO alerts(organization, branch, alert_type, severity, subject, message, related_user, assigned_to, status, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (organization, branch, alert_type, severity, subject, message, related_user, assigned_to, "open", datetime.now().isoformat()))
    
    conn.commit()
    alert_id = cursor.lastrowid
    conn.close()
    
    return alert_id


# =====================================================
# SEND SYSTEM MESSAGE
# =====================================================
def send_system_message(from_user, to_user, organization, branch, message_type, subject, body, priority="normal"):
    """Sends a system message (alert/notification) to a user."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    INSERT INTO system_messages(from_user, to_user, organization, branch, message_type, subject, body, priority, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (from_user, to_user, organization, branch, message_type, subject, body, priority, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()


# =====================================================
# GET UNREAD ALERTS (for dashboard)
# =====================================================
def get_unread_alerts(organization, branch=None, role="super_admin"):
    """Gets unread alerts for super admin dashboard."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if branch:
        cursor.execute("""
        SELECT * FROM alerts
        WHERE organization = ? AND branch = ? AND status = 'open'
        ORDER BY 
            CASE severity 
                WHEN 'critical' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END,
            created_at DESC
        LIMIT 20
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT * FROM alerts
        WHERE organization = ? AND status = 'open'
        ORDER BY 
            CASE severity 
                WHEN 'critical' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END,
            created_at DESC
        LIMIT 50
        """, (organization,))
    
    alerts = cursor.fetchall()
    cols = ["id", "organization", "branch", "alert_type", "severity", "subject", "message", "related_user", "assigned_to", "status", "created_at", "resolved_at"]
    
    result = [dict(zip(cols, a)) for a in alerts]
    conn.close()
    
    return result


# =====================================================
# MARK ALERT AS RESOLVED
# =====================================================
def resolve_alert(alert_id, notes=None):
    """Marks an alert as resolved."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    UPDATE alerts
    SET status = 'resolved', resolved_at = ?
    WHERE id = ?
    """, (datetime.now().isoformat(), alert_id))
    
    conn.commit()
    conn.close()


# =====================================================
# GET UNREAD MESSAGES (for user/admin)
# =====================================================
def get_unread_messages(to_user, organization, branch=None):
    """Gets unread messages for a user."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if branch:
        cursor.execute("""
        SELECT * FROM system_messages
        WHERE to_user = ? AND organization = ? AND branch = ? AND read_at IS NULL
        ORDER BY priority DESC, created_at DESC
        """, (to_user, organization, branch))
    else:
        cursor.execute("""
        SELECT * FROM system_messages
        WHERE to_user = ? AND organization = ? AND read_at IS NULL
        ORDER BY priority DESC, created_at DESC
        """, (to_user, organization))
    
    messages = cursor.fetchall()
    cols = ["id", "from_user", "to_user", "organization", "branch", "message_type", "subject", "body", "priority", "read_at", "created_at"]
    
    result = [dict(zip(cols, m)) for m in messages]
    conn.close()
    
    return result


# =====================================================
# MARK MESSAGE AS READ
# =====================================================
def mark_message_read(message_id):
    """Marks a message as read."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    UPDATE system_messages
    SET read_at = ?
    WHERE id = ?
    """, (datetime.now().isoformat(), message_id))
    
    conn.commit()
    conn.close()


# =====================================================
# AUTO-GENERATE ALERTS FROM INTELLIGENCE
# =====================================================
def generate_alerts_from_intelligence(organization, branch, intelligence_report):
    """Automatically creates alerts from intelligence report findings."""
    
    alert_count = 0
    
    # Create critical alerts
    for alert_text in intelligence_report.get("critical_alerts", []):
        
        severity = "critical" if "🚨" in alert_text or "🔥" in alert_text else "warning"
        alert_id = create_alert(
            organization=organization,
            branch=branch,
            alert_type="intelligence_finding",
            severity=severity,
            subject=alert_text[:60],
            message=alert_text,
            assigned_to="super_admin"
        )
        alert_count += 1
    
    # Send recommendations as high-priority messages
    for rec in intelligence_report.get("recommendations", [])[:5]:
        send_system_message(
            from_user="system",
            to_user="super_admin",
            organization=organization,
            branch=branch,
            message_type="recommendation",
            subject="Action Required",
            body=rec,
            priority="high" if "ACTION:" in rec else "normal"
        )
    
    return alert_count


# =====================================================
# ALERT STATISTICS FOR DASHBOARD
# =====================================================
def get_alert_statistics(organization, branch=None):
    """Gets alert statistics for dashboard display."""
    
    conn = get_connection()
    cursor = conn.cursor()
    
    if branch:
        cursor.execute("""
        SELECT severity, COUNT(*) as count FROM alerts
        WHERE organization = ? AND branch = ? AND status = 'open'
        GROUP BY severity
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT severity, COUNT(*) as count FROM alerts
        WHERE organization = ? AND status = 'open'
        GROUP BY severity
        """, (organization,))
    
    results = cursor.fetchall()
    
    stats = {
        "critical": 0,
        "warning": 0,
        "info": 0,
        "total": 0,
    }
    
    for severity, count in results:
        stats[severity] = count
        stats["total"] += count
    
    # Get message statistics
    if branch:
        cursor.execute("""
        SELECT COUNT(*) FROM system_messages
        WHERE organization = ? AND branch = ? AND read_at IS NULL
        """, (organization, branch))
    else:
        cursor.execute("""
        SELECT COUNT(*) FROM system_messages
        WHERE organization = ? AND read_at IS NULL
        """, (organization,))
    
    unread_messages = cursor.fetchone()[0]
    stats["unread_messages"] = unread_messages
    
    conn.close()
    
    return stats


# =====================================================
# SEND BULK ALERTS FOR FAVORITISM CASES
# =====================================================
def create_favoritism_alerts(organization, branch, favoritism_findings):
    """Creates alerts for each favoritism case."""
    
    for finding in favoritism_findings:
        
        alert_id = create_alert(
            organization=organization,
            branch=branch,
            alert_type="favoritism_detection",
            severity="warning",
            subject=f"Favoritism detected: {finding[:50]}",
            message=finding,
            assigned_to="super_admin"
        )
        
        # Also send message to admin if applicable
        send_system_message(
            from_user="system",
            to_user="admin",
            organization=organization,
            branch=branch,
            message_type="alert",
            subject="Favoritism Alert",
            body=finding,
            priority="high"
        )


# =====================================================
# SEND ALERTS FOR IMPORTANT DISCOVERIES
# =====================================================
def create_group_alerts(organization, branch, group_analysis):
    """Creates alerts for detected groups (relationships, cliques, etc)."""
    
    for group_key, group_info in group_analysis.get("groups", {}).items():
        
        group_type = group_info.get("type", "unknown")
        severity = "critical" if group_type == "dating" or group_type == "relationship" else "warning"
        
        members = ", ".join(group_info.get("members", []))
        message = f"{group_type.upper()}: {members} - {group_info.get('description', 'Group detected')}"
        
        alert_id = create_alert(
            organization=organization,
            branch=branch,
            alert_type=f"group_{group_type}",
            severity=severity,
            subject=message[:60],
            message=message,
            assigned_to="super_admin"
        )


# =====================================================
# RETENTION ALERTS (EMPLOYEES AT RISK)
# =====================================================
def create_retention_alerts(organization, branch, at_risk_employees):
    """Creates alerts for employees at risk of leaving."""
    
    for employee in at_risk_employees:
        
        create_alert(
            organization=organization,
            branch=branch,
            alert_type="retention_risk",
            severity="critical",
            subject=f"Retention Alert: {employee}",
            message=f"{employee} showing signs of withdrawal/family issues. Recommend immediate retention tactics.",
            related_user=employee,
            assigned_to="super_admin"
        )
        
        send_system_message(
            from_user="system",
            to_user="admin",
            organization=organization,
            branch=branch,
            message_type="retention_action",
            subject=f"Employee {employee} Requires Support",
            body=f"Recommend offering leave, flex time, or temporary relief duties. Check in with employee.",
            priority="high"
        )
