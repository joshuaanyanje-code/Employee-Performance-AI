import pandas as pd
from datetime import datetime

from database.db import cached_read_sql


DISPLAY_COLUMNS = [
    "Username",
    "Role",
    "Branch",
    "Late Records",
    "Chargeable Late Minutes",
    "Approved Late Minutes",
    "Chargeable Hours",
    "Pending Minutes to Next Fine",
    "Fine Amount",
]
_SCHEMA_READY_KEYS = set()


def _schema_cache_key(conn):
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row and len(row) >= 3 and row[2]:
            return str(row[2])
    except Exception:
        pass
    return f"conn:{id(conn)}"


def _safe_read(conn, query, params=None):
    try:
        normalized_params = tuple(params) if isinstance(params, (list, tuple)) else ((params,) if params is not None else ())
        query_text = str(query or "").strip()
        if query_text.lower().startswith("select") and not getattr(conn, "in_transaction", False):
            return cached_read_sql(query_text, normalized_params)
        if params is None:
            return pd.read_sql(query, conn)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()


def _parse_time(value, fallback="09:00"):
    txt = str(value or fallback).strip() or fallback
    try:
        return datetime.strptime(txt, "%H:%M").time()
    except Exception:
        return datetime.strptime(fallback, "%H:%M").time()


def empty_fine_table():
    return pd.DataFrame(columns=DISPLAY_COLUMNS)


def ensure_lateness_fine_tables(conn):
    schema_key = _schema_cache_key(conn)
    if schema_key in _SCHEMA_READY_KEYS:
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lateness_fine_settings(
                organization TEXT PRIMARY KEY,
                amount_per_hour REAL DEFAULT 0,
                currency TEXT DEFAULT 'KES',
                status TEXT DEFAULT 'approved',
                updated_by TEXT DEFAULT '',
                approved_by TEXT DEFAULT '',
                note TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                approved_at TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lateness_fine_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization TEXT,
                branch TEXT,
                requested_by TEXT,
                requested_amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'KES',
                reason TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                reviewed_by TEXT DEFAULT '',
                review_note TEXT DEFAULT '',
                created_at TEXT,
                reviewed_at TEXT
            )
            """
        )
        conn.commit()
        _SCHEMA_READY_KEYS.add(schema_key)
    except Exception:
        pass


def get_lateness_policy(conn, organization):
    ensure_lateness_fine_tables(conn)

    policy = {
        "amount_per_hour": 0.0,
        "currency": "KES",
        "status": "not_set",
        "updated_by": "",
        "approved_by": "",
        "note": "",
        "updated_at": "",
        "approved_at": "",
        "pending_request": None,
    }

    if not organization:
        return policy

    settings_df = _safe_read(
        conn,
        """
        SELECT amount_per_hour, currency, status, updated_by, approved_by, note, updated_at, approved_at
        FROM lateness_fine_settings
        WHERE organization=?
        LIMIT 1
        """,
        params=(organization,),
    )
    if not settings_df.empty:
        row = settings_df.iloc[0]
        policy.update(
            {
                "amount_per_hour": float(row.get("amount_per_hour", 0) or 0),
                "currency": str(row.get("currency", "KES") or "KES"),
                "status": str(row.get("status", "approved") or "approved"),
                "updated_by": str(row.get("updated_by", "") or ""),
                "approved_by": str(row.get("approved_by", "") or ""),
                "note": str(row.get("note", "") or ""),
                "updated_at": str(row.get("updated_at", "") or ""),
                "approved_at": str(row.get("approved_at", "") or ""),
            }
        )

    pending_df = _safe_read(
        conn,
        """
        SELECT id, branch, requested_by, requested_amount, currency, reason, status, created_at
        FROM lateness_fine_requests
        WHERE organization=? AND status='pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        params=(organization,),
    )
    if not pending_df.empty:
        policy["pending_request"] = pending_df.iloc[0].to_dict()

    return policy


def compute_lateness_fines(conn, organization, branch=None, username=None, start_date=None, end_date=None, reset_monthly=True):
    ensure_lateness_fine_tables(conn)

    if not organization:
        return empty_fine_table()

    settings_df = _safe_read(conn, "SELECT work_start, late_minutes FROM settings WHERE id=1")
    if settings_df.empty:
        global_start = _parse_time("09:00", "09:00")
        late_grace = 15
    else:
        settings_row = settings_df.iloc[0]
        global_start = _parse_time(settings_row.get("work_start", "09:00"), "09:00")
        try:
            late_grace = int(settings_row.get("late_minutes", 15) or 15)
        except Exception:
            late_grace = 15

    policy = get_lateness_policy(conn, organization)
    amount_per_hour = float(policy.get("amount_per_hour", 0) or 0)

    user_query = """
        SELECT username, role, branch, status
        FROM users
        WHERE organization=? AND lower(coalesce(role, '')) = 'employee'
    """
    user_params = [organization]
    if branch:
        user_query += " AND branch=?"
        user_params.append(branch)
    if username:
        user_query += " AND username=?"
        user_params.append(username)
    user_query += " ORDER BY role, username"

    users_df = _safe_read(conn, user_query, params=tuple(user_params))
    if users_df.empty:
        if username:
            return pd.DataFrame(
                [{
                    "Username": username,
                    "Role": "Unknown",
                    "Branch": branch or "",
                    "Late Records": 0,
                    "Chargeable Late Minutes": 0,
                    "Approved Late Minutes": 0,
                    "Chargeable Hours": 0,
                    "Pending Minutes to Next Fine": 60,
                    "Fine Amount": 0.0,
                }]
            )
        return empty_fine_table()

    attendance_query = """
        SELECT username, branch, date, clock_in, status
        FROM attendance
        WHERE organization=?
    """
    attendance_params = [organization]
    if username:
        attendance_query += " AND username=?"
        attendance_params.append(username)
    attendance_query += " ORDER BY date DESC, id DESC"
    attendance_df = _safe_read(conn, attendance_query, params=tuple(attendance_params))

    start_bound = pd.to_datetime(start_date, errors="coerce") if start_date is not None else pd.NaT
    end_bound = pd.to_datetime(end_date, errors="coerce") if end_date is not None else pd.NaT
    if reset_monthly and pd.isna(start_bound) and pd.isna(end_bound):
        start_bound = pd.Timestamp.now().to_period("M").to_timestamp()

    if not attendance_df.empty:
        attendance_df = attendance_df.copy()
        attendance_df["date"] = pd.to_datetime(attendance_df.get("date"), errors="coerce")
        if not pd.isna(start_bound):
            attendance_df = attendance_df[attendance_df["date"] >= start_bound.normalize()]
        if not pd.isna(end_bound):
            end_limit = end_bound.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            attendance_df = attendance_df[attendance_df["date"] <= end_limit]

    schedules_query = """
        SELECT username, day, work_start, work_end, off_day
        FROM schedules
        WHERE organization=?
    """
    schedule_params = [organization]
    if branch:
        schedules_query += " AND branch=?"
        schedule_params.append(branch)
    if username:
        schedules_query += " AND username=?"
        schedule_params.append(username)
    schedules_df = _safe_read(conn, schedules_query, params=tuple(schedule_params))

    approvals_query = """
        SELECT username, approved_for_date, status
        FROM lateness_approvals
        WHERE organization=?
    """
    approval_params = [organization]
    if username:
        approvals_query += " AND username=?"
        approval_params.append(username)
    approvals_df = _safe_read(conn, approvals_query, params=tuple(approval_params))

    schedule_lookup = {}
    if not schedules_df.empty:
        for _, row in schedules_df.iterrows():
            uname = str(row.get("username", "") or "").strip()
            day_name = str(row.get("day", "") or "").strip()
            if not uname or not day_name:
                continue
            schedule_lookup[(uname, day_name)] = {
                "work_start": _parse_time(row.get("work_start", "09:00"), "09:00"),
                "off_day": int(row.get("off_day", 0) or 0),
            }

    approved_keys = set()
    if not approvals_df.empty:
        approved_rows = approvals_df[
            approvals_df["status"].astype(str).str.lower().isin(["approved", "used"])
        ].copy()
        approved_keys = {
            (str(row.get("username", "") or "").strip(), str(row.get("approved_for_date", "") or "").strip())
            for _, row in approved_rows.iterrows()
        }

    summary = {}
    for _, row in users_df.iterrows():
        uname = str(row.get("username", "") or "").strip()
        if not uname:
            continue
        summary[uname] = {
            "Username": uname,
            "Role": str(row.get("role", "") or "").title() or "Unknown",
            "Branch": str(row.get("branch", "") or ""),
            "Late Records": 0,
            "Chargeable Late Minutes": 0,
            "Approved Late Minutes": 0,
            "Chargeable Hours": 0,
            "Pending Minutes to Next Fine": 60,
            "Fine Amount": 0.0,
        }

    if attendance_df.empty:
        return pd.DataFrame(summary.values())[DISPLAY_COLUMNS]

    attendance_df = attendance_df.copy()
    attendance_df["date"] = pd.to_datetime(attendance_df.get("date"), errors="coerce")
    attendance_df["clock_in"] = pd.to_datetime(attendance_df.get("clock_in"), errors="coerce")

    for _, row in attendance_df.iterrows():
        uname = str(row.get("username", "") or "").strip()
        if not uname or uname not in summary:
            continue

        row_date = row.get("date")
        clock_in = row.get("clock_in")
        if pd.isna(clock_in):
            continue
        if pd.isna(row_date):
            row_date = clock_in
        if pd.isna(row_date):
            continue

        day_name = row_date.strftime("%A")
        schedule = schedule_lookup.get((uname, day_name), {})
        if int(schedule.get("off_day", 0) or 0) == 1:
            continue

        work_start = schedule.get("work_start", global_start)
        try:
            start_dt = datetime.combine(row_date.date(), work_start)
        except Exception:
            continue

        delay_minutes = int(max((clock_in.to_pydatetime() - start_dt).total_seconds() // 60, 0))
        if delay_minutes <= late_grace:
            continue

        chargeable_delay = int(max(delay_minutes - late_grace, 0))
        summary[uname]["Late Records"] += 1
        approval_key = (uname, row_date.strftime("%Y-%m-%d"))
        if approval_key in approved_keys:
            summary[uname]["Approved Late Minutes"] += chargeable_delay
        else:
            summary[uname]["Chargeable Late Minutes"] += chargeable_delay

    rows = []
    for item in summary.values():
        chargeable_minutes = int(item.get("Chargeable Late Minutes", 0) or 0)
        chargeable_hours = chargeable_minutes // 60
        pending_minutes = 60 if chargeable_minutes % 60 == 0 else 60 - (chargeable_minutes % 60)
        if chargeable_minutes == 0:
            pending_minutes = 60
        item["Chargeable Hours"] = int(chargeable_hours)
        item["Pending Minutes to Next Fine"] = int(pending_minutes)
        item["Fine Amount"] = round(chargeable_hours * amount_per_hour, 2)
        rows.append(item)

    fines_df = pd.DataFrame(rows)
    if fines_df.empty:
        return empty_fine_table()

    fines_df = fines_df.sort_values(
        ["Fine Amount", "Chargeable Late Minutes", "Username"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return fines_df[DISPLAY_COLUMNS]


def compute_lateness_fine_history(conn, organization, branch=None, username=None, months=6):
    ensure_lateness_fine_tables(conn)

    if not organization:
        return pd.DataFrame(columns=["Month"] + DISPLAY_COLUMNS)

    history_rows = []
    current_period = pd.Timestamp.now().to_period("M")

    for offset in range(max(int(months or 0), 1) - 1, -1, -1):
        month_period = current_period - offset
        month_start = month_period.to_timestamp()
        month_end = (month_start + pd.offsets.MonthEnd(0)).normalize()
        month_df = compute_lateness_fines(
            conn,
            organization,
            branch=branch,
            username=username,
            start_date=month_start,
            end_date=month_end,
            reset_monthly=False,
        )

        if month_df.empty and username:
            history_rows.append(
                {
                    "Month": month_period.strftime("%Y-%m"),
                    "Username": username,
                    "Role": "",
                    "Branch": branch or "",
                    "Late Records": 0,
                    "Chargeable Late Minutes": 0,
                    "Approved Late Minutes": 0,
                    "Chargeable Hours": 0,
                    "Pending Minutes to Next Fine": 60,
                    "Fine Amount": 0.0,
                }
            )
            continue

        for _, row in month_df.iterrows():
            item = row.to_dict()
            item["Month"] = month_period.strftime("%Y-%m")
            history_rows.append(item)

    if not history_rows:
        return pd.DataFrame(columns=["Month"] + DISPLAY_COLUMNS)

    history_df = pd.DataFrame(history_rows)
    history_df = history_df.sort_values(["Month", "Fine Amount", "Username"], ascending=[False, False, True]).reset_index(drop=True)
    return history_df[["Month"] + DISPLAY_COLUMNS]
