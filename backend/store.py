"""Persistence adapter: Firestore in cloud; durable SQLite for local fallback/tests."""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from .config import GCP_PROJECT, LOCAL_FALLBACK

_lock = RLock()
_db = None
_firestore_module = None
_DEFAULT_LOCAL_DB = Path(__file__).resolve().with_name("tracewell_local.sqlite3")


def _local_db_path() -> Path:
    configured = os.getenv("TRACEWELL_LOCAL_DB")
    path = Path(configured).expanduser() if configured else _DEFAULT_LOCAL_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect_local() -> sqlite3.Connection:
    connection = sqlite3.connect(_local_db_path(), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=15000")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS steps (
            incident_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (incident_id, step_index)
        );
    """)
    connection.commit()
    return connection


def firestore_available() -> bool:
    return bool(GCP_PROJECT)


def get_db():
    global _db, _firestore_module
    if not GCP_PROJECT:
        if LOCAL_FALLBACK:
            return None
        raise RuntimeError("GCP_PROJECT is required; set TRACEWELL_LOCAL_FALLBACK=true only for local development.")
    if _db is None:
        from google.cloud import firestore
        _firestore_module = firestore
        _db = firestore.Client(project=GCP_PROJECT)
    return _db


def server_timestamp():
    if get_db() is None:
        return datetime.now(timezone.utc).isoformat()
    return _firestore_module.SERVER_TIMESTAMP


def get_incident(incident_id: str) -> dict[str, Any] | None:
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute("SELECT payload FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            return json.loads(row["payload"]) if row else None
    snap = db.collection("incidents").document(incident_id).get()
    return ({"id": snap.id, **(snap.to_dict() or {})} if snap.exists else None)


def update_incident(incident_id: str, fields: dict[str, Any], *, create: bool = False) -> None:
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute("SELECT payload FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if row is None and not create:
                raise KeyError(f"Incident {incident_id!r} was not found in local store")
            current = json.loads(row["payload"]) if row else {"id": incident_id}
            current.update(fields)
            created_at = str(current.get("created_at") or datetime.now(timezone.utc).isoformat())
            conn.execute("INSERT OR REPLACE INTO incidents (id, payload, created_at) VALUES (?, ?, ?)",
                         (incident_id, json.dumps(current, default=str), created_at))
            conn.commit()
        return
    ref = db.collection("incidents").document(incident_id)
    if create:
        ref.set(fields, merge=True)
    else:
        ref.update(fields)


def create_local_incident(incident_id: str, fields: dict[str, Any]) -> None:
    item = {"id": incident_id, **fields}
    with _lock, _connect_local() as conn:
        conn.execute("INSERT OR REPLACE INTO incidents (id, payload, created_at) VALUES (?, ?, ?)",
                     (incident_id, json.dumps(item, default=str), str(item.get("created_at") or "")))
        conn.commit()


def append_step(incident_id: str, *, step_type: str, tool: str, input_data: Any, output_data: Any) -> dict[str, Any]:
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute("SELECT COALESCE(MAX(step_index), -1) + 1 AS next_idx FROM steps WHERE incident_id = ?",
                               (incident_id,)).fetchone()
            idx = int(row["next_idx"])
            step = {"index": idx, "type": step_type, "tool": tool, "input": input_data,
                    "output": output_data, "ts": datetime.now(timezone.utc).isoformat()}
            conn.execute("INSERT INTO steps (incident_id, step_index, payload) VALUES (?, ?, ?)",
                         (incident_id, idx, json.dumps(step, default=str)))
            conn.commit()
            return step
    ref = db.collection("incidents").document(incident_id).collection("steps").document()
    @_firestore_module.transactional
    def _write(transaction):
        incident_ref = db.collection("incidents").document(incident_id)
        snap = incident_ref.get(transaction=transaction)
        idx = int((snap.to_dict() or {}).get("next_step_index", 0)) if snap.exists else 0
        transaction.set(ref, {"index": idx, "type": step_type, "tool": tool, "input": input_data,
                              "output": output_data, "ts": _firestore_module.SERVER_TIMESTAMP})
        transaction.set(incident_ref, {"next_step_index": idx + 1}, merge=True)
        return idx
    idx = _write(db.transaction())
    return {"index": idx, "type": step_type, "tool": tool, "input": input_data, "output": output_data}


def list_local_steps(incident_id: str) -> list[dict[str, Any]]:
    with _lock, _connect_local() as conn:
        rows = conn.execute("SELECT payload FROM steps WHERE incident_id = ? ORDER BY step_index", (incident_id,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]


def save_diagnosis(incident_id: str, diagnosis: dict[str, Any], pending_action: dict[str, Any] | None = None) -> None:
    fields: dict[str, Any] = {"diagnosis": diagnosis, "status": "awaiting_approval" if pending_action else "investigating",
                              "updated_at": server_timestamp()}
    if pending_action:
        fields["pending_action"] = pending_action
    update_incident(incident_id, fields)


def list_incidents() -> list[dict[str, Any]]:
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            rows = conn.execute("SELECT payload FROM incidents ORDER BY created_at DESC, rowid DESC").fetchall()
            items = [json.loads(row["payload"]) for row in rows]
    else:
        items = [{"id": snap.id, **(snap.to_dict() or {})} for snap in db.collection("incidents").stream()]
    return sorted(items, key=lambda item: str(item.get("created_at", "")), reverse=True)


def list_steps(incident_id: str) -> list[dict[str, Any]]:
    db = get_db()
    if db is None:
        return list_local_steps(incident_id)
    ref = db.collection("incidents").document(incident_id)
    return [{"id": snap.id, **(snap.to_dict() or {})} for snap in ref.collection("steps").order_by("index").stream()]
