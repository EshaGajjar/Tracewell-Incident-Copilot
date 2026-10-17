"""Script to safely reset all local incident data in Tracewell Incident Copilot.

This script:
1. Verifies that the environment is strictly local SQLite development (never Firestore/production).
2. Creates a timestamped backup of tracewell_local.sqlite3.
3. Completely deletes all records from incident-owned tables:
   - incidents
   - steps
   - operational_events
   - correlated_events
   - root_cause_analyses
   - investigation_graphs
4. Preserves non-incident tables:
   - services (registered service topology)
   - service_dependencies (service dependency topology)
5. Vacuums the SQLite database to reclaim space.
6. Reports exactly what was deleted and what was preserved.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import GCP_PROJECT
from backend.store import _connect_local, _local_db_path, firestore_available

logger = logging.getLogger("tracewell.reset_incidents")

INCIDENT_TABLES = [
    "correlated_events",
    "investigation_graphs",
    "root_cause_analyses",
    "steps",
    "operational_events",
    "incidents",
]

PRESERVED_TABLES = [
    "services",
    "service_dependencies",
]


def reset_local_incident_data() -> dict[str, Any]:
    """Safely delete all local incidents and associated investigation records."""
    # 1. Safety check: never run against cloud Firestore or configured GCP project
    if GCP_PROJECT or firestore_available():
        raise RuntimeError(
            "ABORTED: Refusing to reset data. Active persistence mode indicates Cloud Firestore is configured. "
            "Data reset is strictly restricted to local SQLite development mode."
        )

    db_path = _local_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Local SQLite database not found at {db_path}")

    # 2. Create timestamped backup of local SQLite database
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"{db_path.name}.backup_{timestamp_str}"
    shutil.copy2(db_path, backup_path)
    logger.info("Created local SQLite backup at: %s", backup_path)

    # 3. Read pre-deletion counts
    pre_counts: dict[str, int] = {}
    with _connect_local() as conn:
        for tbl in INCIDENT_TABLES + PRESERVED_TABLES:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
                pre_counts[tbl] = row[0] if row else 0
            except sqlite3.OperationalError:
                pre_counts[tbl] = 0

    # 4. Clean incident-owned tables within a transaction
    with _connect_local() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for tbl in INCIDENT_TABLES:
            conn.execute(f"DELETE FROM {tbl}")
        conn.commit()
        conn.execute("VACUUM")

    # 5. Read post-deletion counts
    post_counts: dict[str, int] = {}
    with _connect_local() as conn:
        for tbl in INCIDENT_TABLES + PRESERVED_TABLES:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
                post_counts[tbl] = row[0] if row else 0
            except sqlite3.OperationalError:
                post_counts[tbl] = 0

    deleted_counts = {
        tbl: pre_counts.get(tbl, 0) - post_counts.get(tbl, 0)
        for tbl in INCIDENT_TABLES
    }

    return {
        "status": "success",
        "backup_path": str(backup_path),
        "db_path": str(db_path),
        "pre_counts": pre_counts,
        "post_counts": post_counts,
        "deleted_counts": deleted_counts,
        "preserved_counts": {tbl: post_counts[tbl] for tbl in PRESERVED_TABLES},
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("=" * 60)
    print("RESETTING LOCAL INCIDENT DATA (Tracewell Incident Copilot)")
    print("=" * 60)

    res = reset_local_incident_data()

    print(f"\nBackup created at: {res['backup_path']}")
    print(f"Target SQLite database: {res['db_path']}\n")

    print("Deleted Incident-Related Records:")
    for tbl, count in res["deleted_counts"].items():
        print(f"  - {tbl:<24} : {count:>5} records deleted (now {res['post_counts'][tbl]})")

    print("\nPreserved System Data:")
    for tbl, count in res["preserved_counts"].items():
        print(f"  - {tbl:<24} : {count:>5} records preserved")

    print("\nLocal incident data reset complete.")
