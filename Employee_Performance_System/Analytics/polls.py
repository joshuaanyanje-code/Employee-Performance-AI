import pandas as pd
from datetime import datetime

from database.db import cached_read_sql


ALLOWED_POLL_CHOICES = ["Yes", "No", "Custom"]
_SCHEMA_READY_KEYS = set()


def _schema_cache_key(conn):
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row and len(row) >= 3 and row[2]:
            return str(row[2])
    except Exception:
        pass
    return f"conn:{id(conn)}"


def safe_read(conn, query, params=None):
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


def ensure_poll_tables(conn):
    schema_key = _schema_cache_key(conn)
    if schema_key in _SCHEMA_READY_KEYS:
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS polls(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization TEXT NOT NULL,
                branch TEXT DEFAULT '',
                question TEXT NOT NULL,
                allow_custom INTEGER DEFAULT 1,
                anonymous INTEGER DEFAULT 1,
                status TEXT DEFAULT 'open',
                created_by TEXT NOT NULL,
                creator_role TEXT DEFAULT '',
                created_at TEXT,
                closed_at TEXT DEFAULT '',
                expires_at TEXT DEFAULT '',
                archived INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_responses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                organization TEXT NOT NULL,
                branch TEXT DEFAULT '',
                responder TEXT NOT NULL,
                responder_role TEXT DEFAULT '',
                response_choice TEXT DEFAULT '',
                custom_answer TEXT DEFAULT '',
                created_at TEXT,
                UNIQUE(poll_id, responder)
            )
            """
        )
        try:
            conn.execute("ALTER TABLE polls ADD COLUMN expires_at TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE polls ADD COLUMN archived INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_polls_org_branch_status ON polls(organization, branch, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_poll_responses_poll_org ON poll_responses(poll_id, organization)")
        conn.commit()
        _SCHEMA_READY_KEYS.add(schema_key)
    except Exception:
        pass


def _normalize_role(role):
    return str(role or "").strip().lower()


def _parse_datetime_value(value):
    text = str(value or "").strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed
        except Exception:
            continue

    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _normalize_expiry_text(expires_at):
    if isinstance(expires_at, datetime):
        return expires_at.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    parsed = _parse_datetime_value(expires_at)
    if parsed is None:
        return ""
    return parsed.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def create_poll(
    conn,
    organization,
    question,
    created_by,
    creator_role,
    branch="",
    anonymous=True,
    allow_custom=True,
    expires_at="",
):
    ensure_poll_tables(conn)

    org = str(organization or "").strip()
    poll_question = str(question or "").strip()
    creator = str(created_by or "").strip()
    role = _normalize_role(creator_role)
    target_branch = str(branch or "").strip()
    expiry_text = _normalize_expiry_text(expires_at)

    if not org:
        return False, "Organization is required.", None
    if not poll_question:
        return False, "Poll question is required.", None
    if not creator:
        return False, "Creator identity is required.", None

    if role not in {"superadmin", "super_admin", "admin", "manager"}:
        return False, "Only admins and super admins can create polls.", None

    if expiry_text:
        parsed_expiry = _parse_datetime_value(expiry_text)
        if parsed_expiry is not None and parsed_expiry <= datetime.now():
            return False, "Expiry must be in the future.", None

    # Admin-created polls stay anonymous to branch viewers.
    if role in {"admin", "manager"}:
        anonymous = True

    if target_branch.lower() in {"all", "all branches", "organization", "overall"}:
        target_branch = ""

    duplicate_df = safe_read(
        conn,
        """
        SELECT id
        FROM polls
        WHERE organization=?
          AND COALESCE(branch, '')=?
          AND LOWER(TRIM(question))=LOWER(TRIM(?))
          AND created_by=?
          AND datetime(created_at) >= datetime('now', '-2 minutes')
        ORDER BY id DESC
        LIMIT 1
        """,
        params=(org, target_branch, poll_question, creator),
    )
    if not duplicate_df.empty:
        return False, "Duplicate poll submit blocked. That question was just created.", None

    try:
        cursor = conn.execute(
            """
            INSERT INTO polls(
                organization, branch, question, allow_custom, anonymous,
                status, created_by, creator_role, created_at, closed_at, expires_at, archived
            )
            VALUES (?,?,?,?,?,'open',?,?,datetime('now'),'','',0)
            """,
            (
                org,
                target_branch,
                poll_question,
                int(bool(allow_custom)),
                int(bool(anonymous)),
                creator,
                role,
            ),
        )
        if expiry_text:
            conn.execute(
                "UPDATE polls SET expires_at=? WHERE id=?",
                (expiry_text, int(cursor.lastrowid)),
            )
        conn.commit()
        return True, "Poll created successfully.", int(cursor.lastrowid)
    except Exception as exc:
        return False, f"Could not create poll: {exc}", None


def create_poll_batch(
    conn,
    organization,
    questions,
    created_by,
    creator_role,
    branch="",
    anonymous=True,
    allow_custom=True,
    expires_at="",
):
    if isinstance(questions, str):
        parsed_questions = [
            str(line).strip().lstrip("-• ").strip()
            for line in str(questions).splitlines()
        ]
    else:
        parsed_questions = [str(item).strip() for item in (questions or [])]

    question_items = [item for item in parsed_questions if item]
    if not question_items:
        return False, "Type at least one poll question.", []

    created_ids = []
    for question_text in question_items:
        ok, message, poll_id = create_poll(
            conn,
            organization,
            question_text,
            created_by,
            creator_role,
            branch=branch,
            anonymous=anonymous,
            allow_custom=allow_custom,
            expires_at=expires_at,
        )
        if not ok:
            return False, message, created_ids
        created_ids.append(int(poll_id))

    if len(created_ids) == 1:
        return True, "Poll created successfully.", created_ids
    return True, f"{len(created_ids)} poll questions created successfully.", created_ids


def set_poll_status(conn, poll_id, status):
    ensure_poll_tables(conn)
    new_status = str(status or "open").strip().lower()
    if new_status not in {"open", "closed", "archived"}:
        return False, "Invalid poll status."

    try:
        if new_status == "closed":
            conn.execute(
                "UPDATE polls SET status='closed', archived=0, closed_at=datetime('now') WHERE id=?",
                (int(poll_id),),
            )
        elif new_status == "archived":
            conn.execute(
                "UPDATE polls SET status='archived', archived=1, closed_at=datetime('now') WHERE id=?",
                (int(poll_id),),
            )
        else:
            conn.execute(
                "UPDATE polls SET status='open', archived=0, closed_at='' WHERE id=?",
                (int(poll_id),),
            )
        conn.commit()
        return True, f"Poll {new_status}."
    except Exception as exc:
        return False, f"Could not update poll status: {exc}"


def get_visible_polls(conn, organization, viewer_branch="", viewer_role="employee", include_closed=True):
    ensure_poll_tables(conn)

    org = str(organization or "").strip()
    branch = str(viewer_branch or "").strip()
    role = _normalize_role(viewer_role)

    query = [
        """
        SELECT
            p.id,
            p.organization,
            COALESCE(p.branch, '') AS branch,
            p.question,
            COALESCE(p.allow_custom, 1) AS allow_custom,
            COALESCE(p.anonymous, 1) AS anonymous,
            COALESCE(p.status, 'open') AS status,
            p.created_by,
            p.creator_role,
            p.created_at,
            p.closed_at,
            COALESCE(p.expires_at, '') AS expires_at,
            COALESCE(p.archived, 0) AS archived,
            CASE
                WHEN COALESCE(p.expires_at, '') <> '' AND datetime(p.expires_at) < datetime('now') THEN 1
                ELSE 0
            END AS is_expired,
            COUNT(r.id) AS total_responses
        FROM polls p
        LEFT JOIN poll_responses r ON r.poll_id = p.id
        WHERE p.organization=?
        """
    ]
    params = [org]

    if role not in {"superadmin", "super_admin", "master", "master_admin"}:
        query.append("AND (COALESCE(p.branch, '')='' OR p.branch=?)")
        params.append(branch)

    if not include_closed:
        query.append(
            "AND lower(COALESCE(p.status, 'open'))='open' "
            "AND COALESCE(p.archived, 0)=0 "
            "AND (COALESCE(p.expires_at, '')='' OR datetime(p.expires_at) >= datetime('now'))"
        )

    query.append(
        """
        GROUP BY p.id, p.organization, p.branch, p.question, p.allow_custom, p.anonymous,
                 p.status, p.created_by, p.creator_role, p.created_at, p.closed_at, p.expires_at, p.archived
        ORDER BY CASE
                    WHEN COALESCE(p.archived, 0)=1 THEN 2
                    WHEN lower(COALESCE(p.status, 'open'))='open' THEN 0
                    ELSE 1
                 END,
                 p.id ASC
        """
    )

    return safe_read(conn, "\n".join(query), params=tuple(params))


def get_user_poll_response(conn, poll_id, responder):
    ensure_poll_tables(conn)
    responder_name = str(responder or "").strip()
    if not responder_name:
        return None

    df = safe_read(
        conn,
        """
        SELECT poll_id, responder, responder_role, response_choice, custom_answer, created_at
        FROM poll_responses
        WHERE poll_id=? AND responder=?
        LIMIT 1
        """,
        params=(int(poll_id), responder_name),
    )
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def submit_poll_response(
    conn,
    poll_id,
    organization,
    responder,
    responder_role,
    responder_branch="",
    answer_choice="",
    custom_answer="",
):
    ensure_poll_tables(conn)

    org = str(organization or "").strip()
    responder_name = str(responder or "").strip()
    role = _normalize_role(responder_role)
    branch = str(responder_branch or "").strip()
    custom_text = str(custom_answer or "").strip()
    choice = str(answer_choice or "").strip().title()

    poll_df = safe_read(
        conn,
        "SELECT * FROM polls WHERE id=? AND organization=? LIMIT 1",
        params=(int(poll_id), org),
    )
    if poll_df.empty:
        return False, "Poll not found."

    poll = poll_df.iloc[0]
    if str(poll.get("status", "open") or "open").strip().lower() != "open":
        return False, "This poll is closed."

    target_branch = str(poll.get("branch", "") or "").strip()
    if target_branch and target_branch != branch:
        return False, "This poll is not available for your branch."

    expires_at = _parse_datetime_value(poll.get("expires_at", ""))
    if expires_at is not None and expires_at < datetime.now():
        return False, "This poll has expired."

    existing = get_user_poll_response(conn, int(poll_id), responder_name)
    if existing:
        return False, "You already answered this question. Previous answers are locked."

    if custom_text:
        choice = "Custom"
    if choice not in ALLOWED_POLL_CHOICES:
        return False, "Choose Yes, No, or enter a custom answer."
    if choice == "Custom" and not custom_text:
        return False, "Type the custom answer you want to submit."
    if choice != "Custom":
        custom_text = ""

    if not responder_name:
        return False, "Responder name is required."

    try:
        conn.execute(
            """
            INSERT INTO poll_responses(
                poll_id, organization, branch, responder, responder_role,
                response_choice, custom_answer, created_at
            )
            VALUES (?,?,?,?,?,?,?,datetime('now'))
            """,
            (
                int(poll_id),
                org,
                branch,
                responder_name,
                role,
                choice,
                custom_text,
            ),
        )
        conn.commit()
        return True, "Poll response saved."
    except Exception as exc:
        if "unique" in str(exc).lower():
            return False, "You already answered this question. Previous answers are locked."
        return False, f"Could not save response: {exc}"


def get_poll_results(conn, poll_id, can_view_identities=False):
    ensure_poll_tables(conn)

    poll_df = safe_read(conn, "SELECT * FROM polls WHERE id=? LIMIT 1", params=(int(poll_id),))
    if poll_df.empty:
        return {
            "poll": {},
            "total": 0,
            "yes_count": 0,
            "no_count": 0,
            "custom_count": 0,
            "custom_answers": pd.DataFrame(),
            "summary_breakdown": pd.DataFrame(columns=["Response Type", "Count"]),
            "detailed_breakdown": pd.DataFrame(columns=["Answer", "Count"]),
            "named_responses": pd.DataFrame(),
        }

    poll = poll_df.iloc[0].to_dict()
    responses = safe_read(
        conn,
        """
        SELECT responder, responder_role, response_choice, custom_answer, branch, created_at
        FROM poll_responses
        WHERE poll_id=?
        ORDER BY id DESC
        """,
        params=(int(poll_id),),
    )

    if responses.empty:
        return {
            "poll": poll,
            "total": 0,
            "yes_count": 0,
            "no_count": 0,
            "custom_count": 0,
            "custom_answers": pd.DataFrame(columns=["Custom Answer", "Count"]),
            "summary_breakdown": pd.DataFrame(
                {"Response Type": ["Yes", "No", "Custom"], "Count": [0, 0, 0]}
            ),
            "detailed_breakdown": pd.DataFrame(columns=["Answer", "Count"]),
            "named_responses": pd.DataFrame(columns=["Responder", "Role", "Answer", "Branch", "Submitted At"]),
        }

    work = responses.copy()
    work["response_choice"] = work["response_choice"].fillna("").astype(str).str.title().str.strip()
    work["custom_answer"] = work["custom_answer"].fillna("").astype(str).str.strip()
    work["final_answer"] = work.apply(
        lambda row: row["custom_answer"] if str(row["custom_answer"]).strip() else row["response_choice"],
        axis=1,
    )
    work["answer_bucket"] = work["final_answer"].apply(
        lambda value: value if str(value).strip().title() in {"Yes", "No"} else "Custom"
    )

    yes_count = int((work["answer_bucket"] == "Yes").sum())
    no_count = int((work["answer_bucket"] == "No").sum())
    custom_count = int((work["answer_bucket"] == "Custom").sum())

    summary_breakdown = pd.DataFrame(
        {
            "Response Type": ["Yes", "No", "Custom"],
            "Count": [yes_count, no_count, custom_count],
        }
    )

    detailed_breakdown = (
        work.assign(display_answer=work["final_answer"].replace("", "(Blank answer)"))
        .groupby("display_answer")
        .size()
        .reset_index(name="Count")
        .rename(columns={"display_answer": "Answer"})
        .sort_values(["Count", "Answer"], ascending=[False, True])
        .reset_index(drop=True)
    )

    custom_answers = detailed_breakdown[
        ~detailed_breakdown["Answer"].astype(str).str.title().isin(["Yes", "No"])
    ].rename(columns={"Answer": "Custom Answer"}).reset_index(drop=True)

    named_responses = pd.DataFrame(columns=["Responder", "Role", "Answer", "Branch", "Submitted At"])
    try:
        anonymous_flag = int(poll.get("anonymous", 1))
    except Exception:
        anonymous_flag = 1

    if can_view_identities and anonymous_flag == 0:
        named_responses = work.copy()
        named_responses["Answer"] = named_responses["final_answer"]
        named_responses = named_responses.rename(
            columns={
                "responder": "Responder",
                "responder_role": "Role",
                "branch": "Branch",
                "created_at": "Submitted At",
            }
        )[["Responder", "Role", "Answer", "Branch", "Submitted At"]]

    return {
        "poll": poll,
        "total": int(len(work)),
        "yes_count": yes_count,
        "no_count": no_count,
        "custom_count": custom_count,
        "custom_answers": custom_answers,
        "summary_breakdown": summary_breakdown,
        "detailed_breakdown": detailed_breakdown,
        "named_responses": named_responses,
    }
