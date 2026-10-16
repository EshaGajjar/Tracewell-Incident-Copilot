"""Automatic ingestion of Google Cloud Run deployments and Cloud Logging errors."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import GCP_PROJECT, GCP_REGION, LOCAL_FALLBACK, LOG_LOOKBACK_MINUTES
from .store import append_operational_event, get_incident, list_operational_events

logger = logging.getLogger("tracewell.ingestion")


def _safe_text(value: Any, limit: int = 1500) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def _stable_key(*parts: Any) -> str:
    """Create a deterministic hash key from structured parts."""
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_event_timestamp(value: Any) -> str:
    """Ensure timestamp is in standard ISO format with UTC offset."""
    if not value:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).isoformat()


def get_effective_project(override: str | None = None) -> str | None:
    """Resolve the effective GCP project ID."""
    if override:
        return override
    if GCP_PROJECT:
        return GCP_PROJECT
    try:
        import google.auth

        _, project = google.auth.default()
        if project:
            return project
    except Exception:
        pass
    return None


def get_effective_region(override: str | None = None) -> str:
    return override or GCP_REGION or "us-central1"


# -------------------------------------------------------------------
# GCP Retrieval: Cloud Run Revisions and Deployment Changes
# -------------------------------------------------------------------


def fetch_cloud_run_deployments(
    service: str,
    project: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Retrieve Cloud Run service revisions, deployment metadata, and traffic allocations.

    Uses existing Application Default Credentials (ADC) and project/region configuration.
    Handles missing credentials, unavailable APIs, and network errors gracefully.
    """
    effective_project = get_effective_project(project)
    effective_region = get_effective_region(region)

    if not effective_project:
        return {
            "status": "error",
            "service": service,
            "error_type": "CONFIG_ERROR",
            "error": (
                "GCP project is not configured. Cloud Run deployment inspection "
                "requires a configured project ID."
            ),
        }

    try:
        import google.auth
        import google.auth.exceptions
        from google.api_core import exceptions as gcp_exceptions
        from google.cloud import run_v2
    except (ImportError, ModuleNotFoundError) as exc:
        return {
            "status": "error",
            "service": service,
            "error_type": "DEPENDENCY_MISSING",
            "error": f"Cloud Run SDK is not available: {type(exc).__name__}: {exc}",
        }

    try:
        client = run_v2.ServicesClient()
        service_path = (
            f"projects/{effective_project}/locations/{effective_region}/services/{service}"
        )
        svc = client.get_service(name=service_path)

        rev_client = run_v2.RevisionsClient()
        revisions: list[dict[str, Any]] = []

        for rev in rev_client.list_revisions(parent=service_path):
            rev_name = rev.name.split("/")[-1]
            created_at = (
                rev.create_time.isoformat() if rev.create_time else None
            )
            is_ready = bool(
                rev.conditions
                and any(
                    condition.type_ == "Ready"
                    and condition.state.name == "CONDITION_SUCCEEDED"
                    for condition in rev.conditions
                )
            )

            container_image = None
            if rev.containers:
                container_image = rev.containers[0].image

            revisions.append(
                {
                    "name": rev_name,
                    "full_name": rev.name,
                    "created_at": created_at,
                    "ready": is_ready,
                    "service": service,
                    "image": container_image,
                }
            )

        traffic: list[dict[str, Any]] = []
        for item in svc.traffic:
            traffic.append(
                {
                    "revision": item.revision,
                    "latest_revision": item.latest_revision,
                    "percent": item.percent,
                    "tag": item.tag,
                }
            )

        recent_revisions = sorted(
            revisions,
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )[:15]

        return {
            "status": "success",
            "service": service,
            "project": effective_project,
            "region": effective_region,
            "uri": getattr(svc, "uri", None),
            "traffic": traffic,
            "revisions": recent_revisions,
        }

    except google.auth.exceptions.DefaultCredentialsError as exc:
        msg = f"DefaultCredentialsError: Could not automatically determine credentials: {exc}"
        logger.warning("Cloud Run auth error: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "MISSING_CREDENTIALS",
            "error": msg,
        }
    except gcp_exceptions.PermissionDenied as exc:
        msg = f"PermissionDenied: Insufficient IAM permissions for Cloud Run service '{service}' in project '{effective_project}': {exc.message}"
        logger.warning("Cloud Run permission denied: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "PERMISSION_DENIED",
            "error": msg,
        }
    except gcp_exceptions.NotFound as exc:
        msg = f"NotFound: Cloud Run service '{service}' not found in location '{effective_region}' (project '{effective_project}'): {exc.message}"
        logger.warning("Cloud Run resource not found: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "NOT_FOUND",
            "error": msg,
        }
    except gcp_exceptions.GoogleAPICallError as exc:
        msg = f"Cloud Run API error ({type(exc).__name__}): {exc.message or str(exc)}"
        logger.warning("Cloud Run API error: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "API_ERROR",
            "error": msg,
        }
    except Exception as exc:
        msg = f"Cloud Run query failed: {type(exc).__name__}: {str(exc)}"
        logger.exception("Unexpected Cloud Run error: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "UNEXPECTED_ERROR",
            "error": msg,
        }


# -------------------------------------------------------------------
# GCP Retrieval: Cloud Logging Errors and Exceptions
# -------------------------------------------------------------------


def fetch_cloud_logging_errors(
    service: str,
    minutes: int = LOG_LOOKBACK_MINUTES,
    since: datetime | None = None,
    project: str | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve relevant Cloud Logging error entries and exceptions for a Cloud Run service.

    Queries error/exception logs strictly for the given service.
    Extracts timestamps, service names, revision identifiers, severity, and payload evidence.
    """
    effective_project = get_effective_project(project)

    if not effective_project:
        return {
            "status": "error",
            "service": service,
            "error_type": "CONFIG_ERROR",
            "error": (
                "GCP project is not configured. Cloud Logging query "
                "requires a configured project ID."
            ),
        }

    try:
        import google.auth
        import google.auth.exceptions
        from google.api_core import exceptions as gcp_exceptions
        from google.cloud import logging as cloud_logging
    except (ImportError, ModuleNotFoundError) as exc:
        return {
            "status": "error",
            "service": service,
            "error_type": "DEPENDENCY_MISSING",
            "error": f"Cloud Logging SDK is not available: {type(exc).__name__}: {exc}",
        }

    try:
        client = cloud_logging.Client(project=effective_project)

        if not since:
            since = datetime.now(timezone.utc) - timedelta(
                minutes=max(1, min(minutes, 360))
            )
        elif since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        # Strictly restrict query to the targeted service.
        # Captures severity >= ERROR as well as exception / traceback payloads.
        filter_ = (
            'resource.type="cloud_run_revision" '
            f'AND resource.labels.service_name="{service}" '
            f'AND timestamp>="{since.isoformat()}" '
            'AND (severity>=ERROR OR textPayload:"ERROR" '
            'OR textPayload:"Exception" OR textPayload:"Traceback" '
            'OR jsonPayload.message:"ERROR" OR jsonPayload.message:"Exception")'
        )

        entries: list[dict[str, Any]] = []

        for entry in client.list_entries(
            filter_=filter_,
            order_by=cloud_logging.DESCENDING,
            page_size=50,
        ):
            # Verify resource service label matches the requested service.
            entry_service = (entry.resource.labels or {}).get("service_name") if entry.resource else None
            if entry_service and entry_service != service:
                continue

            payload = entry.payload
            log_timestamp = (
                entry.timestamp.isoformat() if entry.timestamp else None
            )
            log_revision = (
                (entry.resource.labels or {}).get("revision_name")
                if entry.resource
                else None
            )
            log_payload = _safe_text(payload, 1500)
            insert_id = getattr(entry, "insert_id", None)
            severity = str(entry.severity or "ERROR")

            log_item = {
                "timestamp": log_timestamp,
                "severity": severity,
                "payload": log_payload,
                "revision": log_revision,
                "insert_id": insert_id,
                "service": service,
            }

            # If an incident ID is provided, note whether the log payload directly references it.
            if incident_id and (incident_id in log_payload or (getattr(entry, "labels", None) and incident_id in str(entry.labels))):
                log_item["incident_match"] = True

            entries.append(log_item)

            if len(entries) >= 50:
                break

        return {
            "status": "success",
            "service": service,
            "project": effective_project,
            "count": len(entries),
            "entries": entries,
            "query": filter_,
        }

    except google.auth.exceptions.DefaultCredentialsError as exc:
        msg = f"DefaultCredentialsError: Could not automatically determine credentials: {exc}"
        logger.warning("Cloud Logging auth error: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "MISSING_CREDENTIALS",
            "error": msg,
        }
    except gcp_exceptions.PermissionDenied as exc:
        msg = f"PermissionDenied: Insufficient IAM permissions for Cloud Logging in project '{effective_project}': {exc.message}"
        logger.warning("Cloud Logging permission denied: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "PERMISSION_DENIED",
            "error": msg,
        }
    except gcp_exceptions.NotFound as exc:
        msg = f"NotFound: Cloud Logging resource not found in project '{effective_project}': {exc.message}"
        logger.warning("Cloud Logging not found: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "NOT_FOUND",
            "error": msg,
        }
    except gcp_exceptions.GoogleAPICallError as exc:
        msg = f"Cloud Logging API error ({type(exc).__name__}): {exc.message or str(exc)}"
        logger.warning("Cloud Logging API error: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "API_ERROR",
            "error": msg,
        }
    except Exception as exc:
        msg = f"Cloud Logging query failed: {type(exc).__name__}: {str(exc)}"
        logger.exception("Unexpected Cloud Logging error: %s", msg)
        return {
            "status": "error",
            "service": service,
            "error_type": "UNEXPECTED_ERROR",
            "error": msg,
        }


# -------------------------------------------------------------------
# Incident Association and Operational Event Persistence
# -------------------------------------------------------------------


def associate_and_persist_operational_events(
    incident_id: str,
    *,
    deployments_data: dict[str, Any] | None = None,
    errors_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Associate retrieved GCP operational events with the incident and persist them.

    ASSOCIATION RULES:
    1. Incident Service Matching: Events MUST match the incident's service name.
       Events from different services are rejected.
    2. Revision Identifier Matching: Cloud Run revisions and traffic events must belong
       to the service under investigation. Error logs must originate from that service
       and record their revision identifier when present.
    3. Identifiers & Correlation: Uses revision identifiers, service names, and insert_id/hashes.
       Does NOT associate events based only on timestamp proximity.
    4. Deduplication: Saves events with deterministic, stable dedupe keys. Repeated ingestion
       returns the existing persisted events without creating duplicates.
    """
    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} was not found")

    incident_service = incident.get("service")
    if not incident_service:
        return {
            "status": "error",
            "incident_id": incident_id,
            "message": "Incident does not have an associated service name; cannot correlate events.",
            "events_persisted": 0,
        }

    ingested_events: list[dict[str, Any]] = []

    # 1. Ingest deployment changes
    if deployments_data and deployments_data.get("status") == "success":
        dep_service = deployments_data.get("service")
        # Strict service name verification:
        if dep_service == incident_service:
            revisions = deployments_data.get("revisions") or []
            traffic = deployments_data.get("traffic") or []

            for revision in revisions:
                rev_name = revision.get("name")
                if not rev_name:
                    continue

                dedupe_key = f"deployment:{incident_service}:{rev_name}"
                created_at = normalize_event_timestamp(revision.get("created_at"))

                event = append_operational_event(
                    incident_id=incident_id,
                    event_type="deployment",
                    service=incident_service,
                    description=(
                        f"Cloud Run revision {rev_name} discovered for service {incident_service}; "
                        f"ready={revision.get('ready', False)}."
                    ),
                    event_timestamp=created_at,
                    evidence={
                        "revision": revision,
                        "traffic": traffic,
                        "source": "Cloud Run API",
                        "incident_id": incident_id,
                        "service": incident_service,
                    },
                    dedupe_key=dedupe_key,
                )
                ingested_events.append(event)

            for traffic_item in traffic:
                if traffic_item.get("percent"):
                    traffic_rev = (
                        traffic_item.get("revision")
                        or (
                            "latest"
                            if traffic_item.get("latest_revision")
                            else "unspecified"
                        )
                    )
                    percent = traffic_item.get("percent")
                    tag = traffic_item.get("tag")

                    dedupe_key = (
                        f"traffic:{incident_service}:"
                        + _stable_key(traffic_rev, percent, tag)
                    )

                    event = append_operational_event(
                        incident_id=incident_id,
                        event_type="traffic_allocation",
                        service=incident_service,
                        description=(
                            f"Cloud Run traffic allocation: {percent}% routed to "
                            f"revision {traffic_rev} on service {incident_service}."
                        ),
                        event_timestamp=normalize_event_timestamp(
                            incident.get("created_at")
                        ),
                        evidence={
                            "traffic": traffic_item,
                            "source": "Cloud Run API",
                            "incident_id": incident_id,
                            "service": incident_service,
                        },
                        dedupe_key=dedupe_key,
                    )
                    ingested_events.append(event)

    # 2. Ingest application errors and exceptions
    if errors_data and errors_data.get("status") == "success":
        err_service = errors_data.get("service")
        # Strict service name verification:
        if err_service == incident_service:
            for entry in errors_data.get("entries") or []:
                log_timestamp = normalize_event_timestamp(entry.get("timestamp"))
                severity = entry.get("severity") or "ERROR"
                log_revision = entry.get("revision")
                log_payload = entry.get("payload") or ""
                insert_id = entry.get("insert_id")

                # Stable deduplication key
                if insert_id:
                    dedupe_key = f"error_log:{incident_service}:{insert_id}"
                else:
                    dedupe_key = (
                        f"error_log:{incident_service}:"
                        + _stable_key(
                            log_timestamp,
                            severity,
                            log_revision,
                            log_payload[:400],
                        )
                    )

                revision_suffix = f" for revision {log_revision}" if log_revision else ""
                description = (
                    f"Cloud Logging {severity} entry{revision_suffix}: {log_payload[:700]}"
                )

                event = append_operational_event(
                    incident_id=incident_id,
                    event_type="error_log",
                    service=incident_service,
                    description=description,
                    event_timestamp=log_timestamp,
                    evidence={
                        "log": entry,
                        "source": "Cloud Logging",
                        "query": errors_data.get("query"),
                        "insert_id": insert_id,
                        "revision": log_revision,
                        "severity": severity,
                        "service": incident_service,
                        "incident_id": incident_id,
                    },
                    dedupe_key=dedupe_key,
                )
                ingested_events.append(event)

    return {
        "status": "success",
        "incident_id": incident_id,
        "service": incident_service,
        "events_count": len(ingested_events),
        "events": ingested_events,
    }


# -------------------------------------------------------------------
# End-to-End Incident Ingestion Orchestrator
# -------------------------------------------------------------------


def ingest_operational_events_for_incident(
    incident_id: str,
    lookback_minutes: int | None = None,
) -> dict[str, Any]:
    """Execute automatic ingestion of GCP deployments and application errors for an incident.

    Preserves local SQLite fallback if GCP is not configured.
    Returns clear, meaningful status and error details rather than silently pretending
    that GCP data was retrieved.
    """
    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} was not found")

    service = incident.get("service")
    if not service:
        return {
            "status": "error",
            "incident_id": incident_id,
            "message": "Incident does not specify a service name; cannot ingest GCP events.",
            "events_persisted": 0,
        }

    effective_project = get_effective_project()

    # Local fallback preservation: if no GCP project is configured, remain in local mode
    if not effective_project:
        return {
            "status": "skipped",
            "mode": "local_fallback",
            "incident_id": incident_id,
            "service": service,
            "message": (
                "GCP project is not configured. Preserving local mode; "
                "live Cloud Run and Cloud Logging were not queried."
            ),
            "events_persisted": 0,
        }

    minutes = lookback_minutes or LOG_LOOKBACK_MINUTES

    # Retrieve deployments and errors from GCP
    deployments_data = fetch_cloud_run_deployments(service, project=effective_project)
    errors_data = fetch_cloud_logging_errors(
        service,
        minutes=minutes,
        project=effective_project,
        incident_id=incident_id,
    )

    errors_reported: dict[str, str] = {}
    if deployments_data.get("status") == "error":
        errors_reported["deployments"] = deployments_data.get("error", "Unknown deployment error")
    if errors_data.get("status") == "error":
        errors_reported["logging"] = errors_data.get("error", "Unknown logging error")

    # If both APIs failed, return meaningful error summary without pretending success
    if (
        deployments_data.get("status") == "error"
        and errors_data.get("status") == "error"
    ):
        return {
            "status": "error",
            "incident_id": incident_id,
            "service": service,
            "project": effective_project,
            "errors": errors_reported,
            "message": "Failed to retrieve both Cloud Run deployments and Cloud Logging entries from GCP.",
            "events_persisted": 0,
        }

    # Associate and persist any successfully retrieved events
    persist_result = associate_and_persist_operational_events(
        incident_id,
        deployments_data=deployments_data,
        errors_data=errors_data,
    )

    return {
        "status": "success",
        "incident_id": incident_id,
        "service": service,
        "project": effective_project,
        "events_persisted": persist_result.get("events_count", 0),
        "deployments_retrieved": len(deployments_data.get("revisions", [])),
        "errors_retrieved": len(errors_data.get("entries", [])),
        "api_errors": errors_reported if errors_reported else None,
    }
