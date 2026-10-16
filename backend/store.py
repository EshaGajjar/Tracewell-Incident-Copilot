"""Persistence adapter: Firestore in cloud; durable SQLite for local fallback/tests."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
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

    connection.executescript(
        """
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

        CREATE TABLE IF NOT EXISTS operational_events (
            incident_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (incident_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS services (
            name TEXT PRIMARY KEY,
            service_type TEXT NOT NULL,
            environment TEXT NOT NULL,
            health_status TEXT,
            source TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS service_dependencies (
            id TEXT PRIMARY KEY,
            source_service TEXT NOT NULL,
            target_service TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            evidence TEXT NOT NULL,
            confidence TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_service_dependencies_unique
        ON service_dependencies (source_service, target_service, relationship_type);

        CREATE TABLE IF NOT EXISTS correlated_events (
            incident_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            classification TEXT NOT NULL,
            correlation_reason TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (incident_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS root_cause_analyses (
            incident_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS investigation_graphs (
            graph_id TEXT PRIMARY KEY,
            incident_id TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    # Safely migrate existing databases without deleting stored events.
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(operational_events)"
        ).fetchall()
    }

    if "dedupe_key" not in columns:
        connection.execute(
            "ALTER TABLE operational_events ADD COLUMN dedupe_key TEXT"
        )

    # Each non-null dedupe key can appear only once per incident.
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_operational_events_dedupe
        ON operational_events (incident_id, dedupe_key)
        WHERE dedupe_key IS NOT NULL
        """
    )

    connection.commit()
    return connection


def firestore_available() -> bool:
    return bool(GCP_PROJECT)


def get_db():
    global _db, _firestore_module

    if not GCP_PROJECT:
        if LOCAL_FALLBACK:
            return None
        raise RuntimeError(
            "GCP_PROJECT is required; set TRACEWELL_LOCAL_FALLBACK=true "
            "only for local development."
        )

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
            row = conn.execute(
                "SELECT payload FROM incidents WHERE id = ?",
                (incident_id,),
            ).fetchone()

            return json.loads(row["payload"]) if row else None

    snap = db.collection("incidents").document(incident_id).get()

    return {"id": snap.id, **(snap.to_dict() or {})} if snap.exists else None


def update_incident(
    incident_id: str,
    fields: dict[str, Any],
    *,
    create: bool = False,
) -> None:
    db = get_db()

    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute(
                "SELECT payload FROM incidents WHERE id = ?",
                (incident_id,),
            ).fetchone()

            if row is None and not create:
                raise KeyError(
                    f"Incident {incident_id!r} was not found in local store"
                )

            current = json.loads(row["payload"]) if row else {"id": incident_id}
            current.update(fields)

            created_at = str(
                current.get("created_at")
                or datetime.now(timezone.utc).isoformat()
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO incidents (id, payload, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    incident_id,
                    json.dumps(current, default=str),
                    created_at,
                ),
            )
            conn.commit()

        return

    ref = db.collection("incidents").document(incident_id)

    if create:
        ref.set(fields, merge=True)
    else:
        ref.update(fields)


def create_local_incident(
    incident_id: str,
    fields: dict[str, Any],
) -> None:
    item = {"id": incident_id, **fields}

    with _lock, _connect_local() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO incidents (id, payload, created_at)
            VALUES (?, ?, ?)
            """,
            (
                incident_id,
                json.dumps(item, default=str),
                str(item.get("created_at") or ""),
            ),
        )
        conn.commit()


def append_step(
    incident_id: str,
    *,
    step_type: str,
    tool: str,
    input_data: Any,
    output_data: Any,
) -> dict[str, Any]:
    db = get_db()

    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(step_index), -1) + 1 AS next_idx
                FROM steps
                WHERE incident_id = ?
                """,
                (incident_id,),
            ).fetchone()

            idx = int(row["next_idx"])

            step = {
                "index": idx,
                "type": step_type,
                "tool": tool,
                "input": input_data,
                "output": output_data,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

            conn.execute(
                """
                INSERT INTO steps (incident_id, step_index, payload)
                VALUES (?, ?, ?)
                """,
                (
                    incident_id,
                    idx,
                    json.dumps(step, default=str),
                ),
            )
            conn.commit()

            return step

    ref = (
        db.collection("incidents")
        .document(incident_id)
        .collection("steps")
        .document()
    )

    @_firestore_module.transactional
    def _write(transaction):
        incident_ref = db.collection("incidents").document(incident_id)
        snap = incident_ref.get(transaction=transaction)

        idx = (
            int((snap.to_dict() or {}).get("next_step_index", 0))
            if snap.exists
            else 0
        )

        transaction.set(
            ref,
            {
                "index": idx,
                "type": step_type,
                "tool": tool,
                "input": input_data,
                "output": output_data,
                "ts": _firestore_module.SERVER_TIMESTAMP,
            },
        )

        transaction.set(
            incident_ref,
            {"next_step_index": idx + 1},
            merge=True,
        )

        return idx

    idx = _write(db.transaction())

    return {
        "index": idx,
        "type": step_type,
        "tool": tool,
        "input": input_data,
        "output": output_data,
    }


def list_local_steps(incident_id: str) -> list[dict[str, Any]]:
    with _lock, _connect_local() as conn:
        rows = conn.execute(
            """
            SELECT payload
            FROM steps
            WHERE incident_id = ?
            ORDER BY step_index
            """,
            (incident_id,),
        ).fetchall()

        return [json.loads(row["payload"]) for row in rows]


def save_diagnosis(
    incident_id: str,
    diagnosis: dict[str, Any],
    pending_action: dict[str, Any] | None = None,
) -> None:
    fields: dict[str, Any] = {
        "diagnosis": diagnosis,
        "status": "awaiting_approval" if pending_action else "investigating",
        "updated_at": server_timestamp(),
    }

    if pending_action:
        fields["pending_action"] = pending_action

    update_incident(incident_id, fields)


def list_incidents() -> list[dict[str, Any]]:
    db = get_db()

    if db is None:
        with _lock, _connect_local() as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM incidents
                ORDER BY created_at DESC, rowid DESC
                """
            ).fetchall()

            items = [json.loads(row["payload"]) for row in rows]
    else:
        items = [
            {"id": snap.id, **(snap.to_dict() or {})}
            for snap in db.collection("incidents").stream()
        ]

    return sorted(
        items,
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )


def list_steps(incident_id: str) -> list[dict[str, Any]]:
    db = get_db()

    if db is None:
        return list_local_steps(incident_id)

    ref = db.collection("incidents").document(incident_id)

    return [
        {"id": snap.id, **(snap.to_dict() or {})}
        for snap in ref.collection("steps").order_by("index").stream()
    ]


def append_operational_event(
    incident_id: str,
    *,
    event_type: str,
    service: str,
    description: str,
    event_timestamp: str | None = None,
    evidence: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """Persist an operational event, optionally deduplicating by key."""

    if not get_incident(incident_id):
        raise KeyError(f"Incident {incident_id!r} was not found")

    timestamp = (
        event_timestamp
        if isinstance(event_timestamp, str) and event_timestamp
        else datetime.now(timezone.utc).isoformat()
    )

    if dedupe_key:
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"tracewell:{incident_id}:{dedupe_key}",
        ).hex
    else:
        event_id = uuid.uuid4().hex

    db = get_db()

    if db is None:
        with _lock, _connect_local() as conn:
            # Return the original event if this dedupe key already exists.
            if dedupe_key:
                existing = conn.execute(
                    """
                    SELECT payload
                    FROM operational_events
                    WHERE incident_id = ? AND dedupe_key = ?
                    """,
                    (incident_id, dedupe_key),
                ).fetchone()

                if existing:
                    return json.loads(existing["payload"])

            event: dict[str, Any] = {
                "id": event_id,
                "incident_id": incident_id,
                "event_type": event_type,
                "service": service,
                "description": description,
                "timestamp": timestamp,
                "evidence": evidence or {},
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }

            if dedupe_key:
                event["dedupe_key"] = dedupe_key

            try:
                conn.execute(
                    """
                    INSERT INTO operational_events
                        (incident_id, event_id, event_timestamp, dedupe_key, payload)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        incident_id,
                        event_id,
                        timestamp,
                        dedupe_key,
                        json.dumps(event, default=str),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                if dedupe_key:
                    existing = conn.execute(
                        """
                        SELECT payload
                        FROM operational_events
                        WHERE incident_id = ? AND dedupe_key = ?
                        """,
                        (incident_id, dedupe_key),
                    ).fetchone()
                    if existing:
                        return json.loads(existing["payload"])
                raise

            return event

    # Use the deterministic document ID in Firestore for deduplicated events.
    event_ref = (
        db.collection("incidents")
        .document(incident_id)
        .collection("operational_events")
        .document(event_id)
    )

    if dedupe_key:
        # A transaction prevents concurrent retries from creating duplicates.
        transaction = db.transaction()

        @_firestore_module.transactional
        def _write_event(transaction):
            existing = event_ref.get(transaction=transaction)

            if existing.exists:
                return {"id": existing.id, **(existing.to_dict() or {})}

            event = {
                "id": event_id,
                "incident_id": incident_id,
                "event_type": event_type,
                "service": service,
                "description": description,
                "timestamp": timestamp,
                "evidence": evidence or {},
                "dedupe_key": dedupe_key,
                "recorded_at": _firestore_module.SERVER_TIMESTAMP,
            }

            transaction.set(event_ref, event)
            return event

        return _write_event(transaction)

    event = {
    "id": event_id,
    "incident_id": incident_id,
    "event_type": event_type,
    "service": service,
    "description": description,
    "timestamp": timestamp,
    "evidence": evidence or {},
    "recorded_at": _firestore_module.SERVER_TIMESTAMP,
    }

    event_ref.set(event)

    # Return JSON-safe data. Firestore resolves SERVER_TIMESTAMP when
    # the document is written, but the sentinel itself is not JSON serializable.
    saved_event = event_ref.get()
    return {"id": saved_event.id, **(saved_event.to_dict() or {})}


def list_operational_events(incident_id: str) -> list[dict[str, Any]]:
    """List operational events in chronological order."""

    if get_incident(incident_id) is None:
        raise KeyError(f"Incident '{incident_id}' was not found")

    db = get_db()

    if db is None:
        with _lock, _connect_local() as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM operational_events
                WHERE incident_id = ?
                ORDER BY event_timestamp ASC, event_id ASC
                """,
                (incident_id,),
            ).fetchall()

            return [json.loads(row["payload"]) for row in rows]

    ref = (
        db.collection("incidents")
        .document(incident_id)
        .collection("operational_events")
    )

    events = [
        {"id": snap.id, **(snap.to_dict() or {})}
        for snap in ref.stream()
    ]

    return sorted(
        events,
        key=lambda event: str(event.get("timestamp", "")),
    )


# -------------------------------------------------------------------
# Service node persistence
# -------------------------------------------------------------------


def save_service_node(node: dict[str, Any]) -> dict[str, Any]:
    """Persist or update a service node in the store."""
    name = node.get("name")
    if not name:
        raise ValueError("Service node must have a 'name'")

    item: dict[str, Any] = {
        "name": str(name),
        "service_type": str(node.get("service_type") or "microservice"),
        "environment": str(node.get("environment") or "production"),
        "health_status": str(node.get("health_status") or "unknown"),
        "source": str(node.get("source") or "configured"),
        "description": node.get("description"),
        "metadata": node.get("metadata") or {},
        "updated_at": node.get("updated_at") or datetime.now(timezone.utc).isoformat(),
    }

    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            conn.execute(
                """
                INSERT INTO services (
                    name, service_type, environment, health_status, source, payload, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    service_type = excluded.service_type,
                    environment = excluded.environment,
                    health_status = excluded.health_status,
                    source = excluded.source,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    item["name"],
                    item["service_type"],
                    item["environment"],
                    item["health_status"],
                    item["source"],
                    json.dumps(item, default=str),
                    item["updated_at"],
                ),
            )
            conn.commit()
            return item

    ref = db.collection("services").document(name)
    ref.set(item, merge=True)
    return item


def get_service_node(name: str) -> dict[str, Any] | None:
    """Retrieve a service node by name."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute(
                "SELECT payload FROM services WHERE name = ?",
                (name,),
            ).fetchone()
            return json.loads(row["payload"]) if row else None

    snap = db.collection("services").document(name).get()
    return {"name": snap.id, **(snap.to_dict() or {})} if snap.exists else None


def list_service_nodes(environment: str | None = None) -> list[dict[str, Any]]:
    """List all registered service nodes, optionally filtered by environment."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            if environment and environment != "all":
                rows = conn.execute(
                    "SELECT payload FROM services WHERE environment = ? ORDER BY name ASC",
                    (environment,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload FROM services ORDER BY name ASC"
                ).fetchall()
            return [json.loads(row["payload"]) for row in rows]

    ref = db.collection("services")
    if environment and environment != "all":
        ref = ref.where("environment", "==", environment)
    items = [{"name": snap.id, **(snap.to_dict() or {})} for snap in ref.stream()]
    return sorted(items, key=lambda x: str(x.get("name", "")))


def delete_service_node(name: str) -> bool:
    """Delete a service node by name."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            cur = conn.execute(
                "DELETE FROM services WHERE name = ?",
                (name,),
            )
            conn.commit()
            return cur.rowcount > 0

    ref = db.collection("services").document(name)
    if ref.get().exists:
        ref.delete()
        return True
    return False


# -------------------------------------------------------------------
# Service dependency persistence
# -------------------------------------------------------------------


def save_dependency_relationship(dependency: dict[str, Any]) -> dict[str, Any]:
    """Persist or update a directional service dependency relationship."""
    source = dependency.get("source") or dependency.get("source_service")
    target = dependency.get("target") or dependency.get("target_service")
    rel_type = dependency.get("relationship_type") or "sync_http"

    if not source or not target:
        raise ValueError("Dependency relationship must specify both 'source' and 'target'")

    dep_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"tracewell:dep:{source}:{target}:{rel_type}",
    ).hex

    created_at = (
        dependency.get("created_at")
        if isinstance(dependency.get("created_at"), str) and dependency.get("created_at")
        else datetime.now(timezone.utc).isoformat()
    )

    item: dict[str, Any] = {
        "id": dep_id,
        "source": str(source),
        "source_service": str(source),
        "target": str(target),
        "target_service": str(target),
        "relationship_type": str(rel_type),
        "evidence": str(dependency.get("evidence") or "Configured architecture dependency"),
        "confidence": str(dependency.get("confidence") or "high"),
        "verification_status": str(dependency.get("verification_status") or "configured"),
        "environment": str(dependency.get("environment") or "production"),
        "metadata": dependency.get("metadata") or {},
        "created_at": created_at,
    }

    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            conn.execute(
                """
                INSERT INTO service_dependencies (
                    id, source_service, target_service, relationship_type,
                    evidence, confidence, verification_status, payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_service, target_service, relationship_type) DO UPDATE SET
                    evidence = excluded.evidence,
                    confidence = excluded.confidence,
                    verification_status = excluded.verification_status,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    dep_id,
                    item["source"],
                    item["target"],
                    item["relationship_type"],
                    item["evidence"],
                    item["confidence"],
                    item["verification_status"],
                    json.dumps(item, default=str),
                    created_at,
                ),
            )
            conn.commit()
            return item

    ref = db.collection("service_dependencies").document(dep_id)
    ref.set(item, merge=True)
    return item


def get_dependency_relationship(
    source: str,
    target: str,
    relationship_type: str = "sync_http",
) -> dict[str, Any] | None:
    """Retrieve a single dependency relationship."""
    dep_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"tracewell:dep:{source}:{target}:{relationship_type}",
    ).hex

    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute(
                "SELECT payload FROM service_dependencies WHERE id = ?",
                (dep_id,),
            ).fetchone()
            return json.loads(row["payload"]) if row else None

    snap = db.collection("service_dependencies").document(dep_id).get()
    return {"id": snap.id, **(snap.to_dict() or {})} if snap.exists else None


def list_dependency_relationships(
    source: str | None = None,
    target: str | None = None,
    environment: str | None = None,
) -> list[dict[str, Any]]:
    """List service dependencies with optional filters."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            query = "SELECT payload FROM service_dependencies WHERE 1=1"
            params: list[Any] = []
            if source:
                query += " AND source_service = ?"
                params.append(source)
            if target:
                query += " AND target_service = ?"
                params.append(target)
            query += " ORDER BY source_service ASC, target_service ASC"
            rows = conn.execute(query, tuple(params)).fetchall()
            items = [json.loads(row["payload"]) for row in rows]
            if environment and environment != "all":
                items = [i for i in items if i.get("environment") == environment]
            return items

    ref = db.collection("service_dependencies")
    if source:
        ref = ref.where("source", "==", source)
    if target:
        ref = ref.where("target", "==", target)
    items = [{"id": snap.id, **(snap.to_dict() or {})} for snap in ref.stream()]
    if environment and environment != "all":
        items = [i for i in items if i.get("environment") == environment]
    return sorted(
        items,
        key=lambda x: (str(x.get("source", "")), str(x.get("target", ""))),
    )


def delete_dependency_relationship(
    source: str,
    target: str,
    relationship_type: str = "sync_http",
) -> bool:
    """Delete a dependency relationship."""
    dep_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"tracewell:dep:{source}:{target}:{relationship_type}",
    ).hex

    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            cur = conn.execute(
                "DELETE FROM service_dependencies WHERE id = ?",
                (dep_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    ref = db.collection("service_dependencies").document(dep_id)
    if ref.get().exists:
        ref.delete()
        return True
    return False


# -------------------------------------------------------------------
# Correlated events persistence
# -------------------------------------------------------------------


def save_correlated_event(incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Persist a correlated event record associated with an incident."""
    event_id = event.get("event_id")
    if not event_id:
        raise ValueError("Correlated event must have an 'event_id'")

    classification = str(event.get("classification") or "unrelated")
    reason = str(event.get("correlation_reason") or "")
    created_at = (
        event.get("created_at")
        if isinstance(event.get("created_at"), str) and event.get("created_at")
        else datetime.now(timezone.utc).isoformat()
    )

    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            conn.execute(
                """
                INSERT INTO correlated_events (
                    incident_id, event_id, classification, correlation_reason, payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id, event_id) DO UPDATE SET
                    classification = excluded.classification,
                    correlation_reason = excluded.correlation_reason,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    incident_id,
                    event_id,
                    classification,
                    reason,
                    json.dumps(event, default=str),
                    created_at,
                ),
            )
            conn.commit()
            return event

    ref = (
        db.collection("incidents")
        .document(incident_id)
        .collection("correlated_events")
        .document(event_id)
    )
    ref.set(event, merge=True)
    return event


def save_correlated_events_batch(
    incident_id: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist a batch of correlated events, preventing duplicates."""
    saved: list[dict[str, Any]] = []
    for ev in events:
        saved.append(save_correlated_event(incident_id, ev))
    return saved


def list_correlated_events(
    incident_id: str,
    classification: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve correlated events for an incident, optionally filtered by classification."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            query = "SELECT payload FROM correlated_events WHERE incident_id = ?"
            params: list[Any] = [incident_id]
            if classification and classification != "all":
                query += " AND classification = ?"
                params.append(classification)
            query += " ORDER BY created_at ASC, event_id ASC"
            rows = conn.execute(query, tuple(params)).fetchall()
            return [json.loads(row["payload"]) for row in rows]

    ref = (
        db.collection("incidents")
        .document(incident_id)
        .collection("correlated_events")
    )
    if classification and classification != "all":
        ref = ref.where("classification", "==", classification)
    items = [{"id": snap.id, **(snap.to_dict() or {})} for snap in ref.stream()]
    return sorted(items, key=lambda x: str(x.get("timestamp", "")))


def list_all_operational_events(
    service: str | None = None,
    limit: int = 300,
) -> list[dict[str, Any]]:
    """Retrieve operational events across incidents or for a service."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            query = "SELECT payload FROM operational_events ORDER BY event_timestamp DESC LIMIT ?"
            rows = conn.execute(query, (limit,)).fetchall()
            events = [json.loads(row["payload"]) for row in rows]
            if service:
                events = [e for e in events if e.get("service") == service]
            return events

    events: list[dict[str, Any]] = []
    try:
        ref = db.collection_group("operational_events")
        if service:
            ref = ref.where("service", "==", service)
        for snap in ref.limit(limit).stream():
            events.append({"id": snap.id, **(snap.to_dict() or {})})
    except Exception as exc:
        logger.warning("Firestore collection_group query failed: %s", exc)

    return sorted(events, key=lambda x: str(x.get("timestamp", "")), reverse=True)


def save_root_cause_analysis(
    incident_id: str,
    analysis: dict[str, Any],
) -> None:
    """Save or update root-cause analysis for an incident (idempotent, no duplicates)."""
    ts = server_timestamp()
    analysis_copy = dict(analysis)
    analysis_copy["updated_at"] = ts

    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            conn.execute(
                """
                INSERT INTO root_cause_analyses (incident_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (incident_id, json.dumps(analysis_copy, default=str), ts),
            )
            conn.commit()
    else:
        db.collection("incidents").document(incident_id).collection("analysis").document("root_cause").set(analysis_copy)
        update_incident(incident_id, {"root_cause_analysis": analysis_copy, "updated_at": ts})


def get_root_cause_analysis(incident_id: str) -> dict[str, Any] | None:
    """Retrieve the persisted root-cause analysis for an incident, if available."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute(
                "SELECT payload FROM root_cause_analyses WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row:
                return json.loads(row["payload"])
            return None

    snap = (
        db.collection("incidents")
        .document(incident_id)
        .collection("analysis")
        .document("root_cause")
        .get()
    )
    if snap.exists:
        return snap.to_dict()

    inc = get_incident(incident_id)
    if inc and "root_cause_analysis" in inc:
        return inc["root_cause_analysis"]
    return None


def save_investigation_graph(
    incident_id: str,
    graph: dict[str, Any],
) -> dict[str, Any]:
    """Persist or update an investigation graph for an incident (idempotent, duplicate-free)."""
    ts = server_timestamp()
    graph_copy = dict(graph)
    graph_id = graph_copy.get("graph_id") or f"cgraph_{incident_id}"
    graph_copy["graph_id"] = graph_id
    graph_copy["incident_id"] = incident_id
    graph_copy["updated_at"] = ts
    if "created_at" not in graph_copy:
        graph_copy["created_at"] = ts

    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            conn.execute(
                """
                INSERT INTO investigation_graphs (graph_id, incident_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    graph_id = excluded.graph_id,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (graph_id, incident_id, json.dumps(graph_copy, default=str), ts),
            )
            conn.commit()
    else:
        db.collection("incidents").document(incident_id).collection("graphs").document("causal_graph").set(graph_copy)
        update_incident(incident_id, {"investigation_graph_id": graph_id, "updated_at": ts})

    return graph_copy


def get_investigation_graph(incident_id: str) -> dict[str, Any] | None:
    """Retrieve the saved investigation graph for an incident, if available."""
    db = get_db()
    if db is None:
        with _lock, _connect_local() as conn:
            row = conn.execute(
                "SELECT payload FROM investigation_graphs WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row:
                return json.loads(row["payload"])
            return None

    snap = (
        db.collection("incidents")
        .document(incident_id)
        .collection("graphs")
        .document("causal_graph")
        .get()
    )
    if snap.exists:
        return snap.to_dict()

    return None
