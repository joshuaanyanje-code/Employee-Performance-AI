"""
Mirror SQLite team_ai.db to MongoDB (Atlas) so cloud data survives restarts and redeploys.

The app keeps using SQLite + pandas.read_sql; after each commit we push a full snapshot to Mongo.
On startup, if Mongo already has business data, we pull it into the local SQLite file.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except Exception:  # pragma: no cover
    MongoClient = None  # type: ignore
    PyMongoError = Exception  # type: ignore

# Insert parents before dependents (poll_responses after polls, users after organizations, etc.)
_TABLE_INSERT_ORDER = [
    "organizations",
    "branches",
    "organization_holiday_profiles",
    "client_feedback_settings",
    "payment_config",
    "settings",
    "analytics_filters",
    "topics",
    "users",
    "branch_working_hours",
    "organization_holidays",
    "ratings",
    "attendance",
    "early_clockout_approvals",
    "lateness_approvals",
    "lateness_fine_settings",
    "lateness_fine_requests",
    "schedules",
    "leaves",
    "warnings",
    "messages",
    "polls",
    "poll_responses",
    "payments",
    "kiosks",
    "client_feedback",
    "audit_logs",
    "admin_action_requests",
    "staff_transfers",
    "user_sessions",
    "alerts",
    "group_demographics",
    "analytics_reports",
    "system_messages",
    "super_admin_insights",
]

_mongo_client_lock = threading.Lock()
_mongo_client = None
_initial_sync_lock = threading.Lock()
_initial_sync_done = False
def is_mongo_configured() -> bool:
    uri = str(os.getenv("MONGO_URI", "") or "").strip()
    return bool(uri) and MongoClient is not None


def _mongo_db_name() -> str:
    name = str(os.getenv("MONGO_DB_NAME", "") or "").strip()
    return name or "Employee-Performance-System"


def get_mongo_db():
    """Shared MongoDB handle (thread-safe lazy init)."""
    global _mongo_client
    if not is_mongo_configured():
        raise RuntimeError("MongoDB is not configured (set MONGO_URI and install pymongo).")
    uri = str(os.getenv("MONGO_URI", "") or "").strip()
    with _mongo_client_lock:
        if _mongo_client is None:
            _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        return _mongo_client[_mongo_db_name()]


def _list_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _ordered_tables(tables: list[str]) -> tuple[list[str], list[str]]:
    """Return (insert_order, delete_order)."""
    tset = set(tables)
    insert: list[str] = []
    for name in _TABLE_INSERT_ORDER:
        if name in tset:
            insert.append(name)
    for name in sorted(tables):
        if name not in insert:
            insert.append(name)
    return insert, list(reversed(insert))


def _row_to_bson_value(value):
    if isinstance(value, bytes):
        return value
    return value


def _repair_sqlite_autoincrement(conn: sqlite3.Connection) -> None:
    """After bulk reload, align sqlite_sequence with MAX(id) to avoid PK collisions."""
    try:
        seq_rows = conn.execute("SELECT name FROM sqlite_sequence").fetchall()
    except sqlite3.OperationalError:
        return
    for (tbl,) in seq_rows:
        try:
            mx = conn.execute(f'SELECT MAX(id) FROM "{tbl}"').fetchone()[0]
            if mx is None:
                mx = 0
            conn.execute(
                "UPDATE sqlite_sequence SET seq=? WHERE name=?",
                (int(mx), tbl),
            )
        except Exception:
            pass


def _sanitize_doc(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        out[str(k)] = _row_to_bson_value(v)
    return out


def push_sqlite_to_mongo(db_path: str) -> None:
    """Replace Mongo collections with a snapshot of the SQLite file."""
    if not is_mongo_configured():
        return
    db = get_mongo_db()
    raw = sqlite3.connect(db_path, timeout=60)
    try:
        tables = _list_sqlite_tables(raw)
        for name in tables:
            cur = raw.execute(f'SELECT * FROM "{name}"')
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            coll = db[name]
            coll.delete_many({})
            if not rows:
                continue
            docs = []
            for row in rows:
                doc = _sanitize_doc(dict(zip(cols, row)))
                docs.append(doc)
            if len(docs) <= 500:
                coll.insert_many(docs)
            else:
                for i in range(0, len(docs), 500):
                    coll.insert_many(docs[i : i + 500])
        db["_team_ai_sync"].update_one(
            {"_id": "meta"},
            {
                "$set": {
                    "updated_at": datetime.now(timezone.utc),
                    "source": "sqlite_mirror",
                }
            },
            upsert=True,
        )
    finally:
        raw.close()


def pull_mongo_to_sqlite(db_path: str) -> None:
    """Overwrite SQLite table data from Mongo (schema must already exist)."""
    if not is_mongo_configured():
        return
    db = get_mongo_db()
    raw = sqlite3.connect(db_path, timeout=60)
    try:
        raw.execute("PRAGMA foreign_keys = OFF")
        tables = _list_sqlite_tables(raw)
        insert_order, delete_order = _ordered_tables(tables)
        for name in delete_order:
            raw.execute(f'DELETE FROM "{name}"')

        for name in insert_order:
            coll = db[name]
            docs = list(coll.find())
            if not docs:
                continue
            info = raw.execute(f'PRAGMA table_info("{name}")').fetchall()
            col_names = [str(r[1]) for r in info]
            if not col_names:
                continue
            placeholders = ",".join("?" * len(col_names))
            cols_sql = ",".join(f'"{c}"' for c in col_names)
            insert_sql = f'INSERT INTO "{name}" ({cols_sql}) VALUES ({placeholders})'
            batch = []
            for doc in docs:
                d = dict(doc)
                d.pop("_id", None)
                batch.append(tuple(d.get(c) for c in col_names))
            raw.executemany(insert_sql, batch)

        _repair_sqlite_autoincrement(raw)
        raw.execute("PRAGMA foreign_keys = ON")
        raw.commit()
    except Exception:
        try:
            raw.rollback()
        except Exception:
            pass
        raise
    finally:
        raw.close()


def should_pull_from_mongo(db) -> bool:
    if str(os.getenv("TEAM_AI_SKIP_MONGO_PULL", "") or "").strip() == "1":
        return False
    try:
        orgs = db["organizations"].count_documents({})
        users = db["users"].count_documents({})
        return orgs > 0 or users > 1
    except Exception:
        return False


def mongo_initial_sync_after_schema(db_path: str) -> None:
    """Once per process: either hydrate SQLite from Mongo or upload SQLite to Mongo."""
    global _initial_sync_done
    if not is_mongo_configured():
        return
    with _initial_sync_lock:
        if _initial_sync_done:
            return
        try:
            db = get_mongo_db()
            db.client.admin.command("ping")
        except Exception as exc:
            logger.warning(
                "team_ai_mongo: cannot reach MongoDB (check MONGO_URI in Streamlit Secrets and Atlas Network Access): %s",
                exc,
            )
            return
        try:
            if should_pull_from_mongo(db):
                pull_mongo_to_sqlite(db_path)
                logger.info("team_ai_mongo: loaded SQLite from MongoDB snapshot.")
            else:
                push_sqlite_to_mongo(db_path)
                logger.info("team_ai_mongo: uploaded SQLite snapshot to MongoDB.")
        except (PyMongoError, OSError, sqlite3.Error) as exc:
            logger.warning("team_ai_mongo: initial sync failed: %s", exc)
            return
        _initial_sync_done = True


def maybe_push_sqlite_to_mongo(db_path: str) -> None:
    """Full push after SQLite commit (best-effort)."""
    if not is_mongo_configured():
        return
    try:
        push_sqlite_to_mongo(db_path)
    except Exception as exc:
        logger.warning("team_ai_mongo: push after commit failed: %s", exc)


def reset_mongo_sync_state() -> None:
    """Allow initial sync to run again (e.g. after full DB reset)."""
    global _initial_sync_done
    with _initial_sync_lock:
        _initial_sync_done = False


def clear_mongodb_mirror() -> None:
    """Drop mirrored app collections (used with full DB reset)."""
    if not is_mongo_configured():
        return
    db = get_mongo_db()
    try:
        names = db.list_collection_names()
    except Exception:
        return
    for name in names:
        if name.startswith("system."):
            continue
        try:
            db.drop_collection(name)
        except Exception:
            pass
