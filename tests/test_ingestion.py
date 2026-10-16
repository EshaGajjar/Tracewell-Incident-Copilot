"""Unit and integration tests for automatic GCP operational event ingestion."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from google.api_core import exceptions as gcp_exceptions
import google.auth.exceptions

from backend.ingestion import (
    associate_and_persist_operational_events,
    fetch_cloud_logging_errors,
    fetch_cloud_run_deployments,
    ingest_operational_events_for_incident,
    normalize_event_timestamp,
)
from backend.main import app
from backend.store import (
    create_local_incident,
    get_incident,
    list_operational_events,
)

client = TestClient(app)


import uuid


class TestIngestion(unittest.TestCase):
    def setUp(self):
        self.incident_id = f"test_inc_{uuid.uuid4().hex[:10]}"
        create_local_incident(
            self.incident_id,
            {
                "title": "Payment API failing with 500s",
                "service": "payment-api",
                "description": "500 Internal Server Error on /pay endpoint",
                "severity": "SEV2",
                "category": "config_error",
                "source": "human",
                "status": "open",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def test_normalize_event_timestamp(self):
        """Test timestamp normalization for diverse input formats."""
        # ISO string with Z
        ts_z = "2026-10-15T18:00:00Z"
        norm_z = normalize_event_timestamp(ts_z)
        self.assertIn("+00:00", norm_z)
        self.assertTrue(norm_z.startswith("2026-10-15T18:00:00"))

        # Datetime with tzinfo
        dt_aware = datetime(2026, 10, 15, 18, 30, 0, tzinfo=timezone.utc)
        norm_aware = normalize_event_timestamp(dt_aware)
        self.assertIn("+00:00", norm_aware)

        # Datetime naive
        dt_naive = datetime(2026, 10, 15, 19, 0, 0)
        norm_naive = normalize_event_timestamp(dt_naive)
        self.assertIn("+00:00", norm_naive)

        # None / invalid fallback to current ISO string
        norm_none = normalize_event_timestamp(None)
        self.assertIsInstance(norm_none, str)
        self.assertIn("T", norm_none)

    def test_associate_and_deduplicate_deployments(self):
        """Test deployment event normalization, association, and deduplication."""
        deployments_data = {
            "status": "success",
            "service": "payment-api",
            "revisions": [
                {
                    "name": "payment-api-00002-abc",
                    "created_at": "2026-10-15T17:45:00Z",
                    "ready": True,
                    "service": "payment-api",
                    "image": "gcr.io/corp/payment-api:v2",
                },
                {
                    "name": "payment-api-00001-xyz",
                    "created_at": "2026-10-15T16:00:00Z",
                    "ready": True,
                    "service": "payment-api",
                    "image": "gcr.io/corp/payment-api:v1",
                },
            ],
            "traffic": [
                {
                    "revision": "payment-api-00002-abc",
                    "percent": 100,
                    "tag": None,
                }
            ],
        }

        # First ingestion
        res1 = associate_and_persist_operational_events(
            self.incident_id,
            deployments_data=deployments_data,
        )
        self.assertEqual(res1["status"], "success")
        events_first_run = list_operational_events(self.incident_id)
        # 2 revisions + 1 traffic allocation = 3 events
        self.assertEqual(len(events_first_run), 3)

        # Second ingestion with exact same data must NOT create duplicates
        res2 = associate_and_persist_operational_events(
            self.incident_id,
            deployments_data=deployments_data,
        )
        self.assertEqual(res2["status"], "success")
        events_second_run = list_operational_events(self.incident_id)
        self.assertEqual(len(events_second_run), 3)

        # Verify event attributes
        deployment_events = [
            e for e in events_second_run if e["event_type"] == "deployment"
        ]
        self.assertEqual(len(deployment_events), 2)
        descriptions = [e["description"] for e in deployment_events]
        self.assertTrue(
            any("payment-api-00002-abc" in d for d in descriptions)
        )
        self.assertTrue(
            any("payment-api-00001-xyz" in d for d in descriptions)
        )
        self.assertEqual(deployment_events[0]["service"], "payment-api")
        self.assertEqual(
            deployment_events[0]["evidence"]["source"], "Cloud Run API"
        )
        self.assertEqual(
            deployment_events[0]["evidence"]["incident_id"], self.incident_id
        )

    def test_associate_and_deduplicate_errors(self):
        """Test application error log normalization, association, and deduplication."""
        errors_data = {
            "status": "success",
            "service": "payment-api",
            "query": 'resource.type="cloud_run_revision" AND service="payment-api"',
            "entries": [
                {
                    "timestamp": "2026-10-15T17:50:00Z",
                    "severity": "ERROR",
                    "payload": "DATABASE_URL invalid: connection refused",
                    "revision": "payment-api-00002-abc",
                    "insert_id": "insert-log-12345",
                    "service": "payment-api",
                },
                {
                    "timestamp": "2026-10-15T17:51:00Z",
                    "severity": "CRITICAL",
                    "payload": "Exception during /pay handler: DBConnectionError",
                    "revision": "payment-api-00002-abc",
                    "insert_id": "insert-log-67890",
                    "service": "payment-api",
                },
            ],
        }

        # First ingestion
        res1 = associate_and_persist_operational_events(
            self.incident_id,
            errors_data=errors_data,
        )
        self.assertEqual(res1["status"], "success")
        events1 = list_operational_events(self.incident_id)
        self.assertEqual(len(events1), 2)

        # Repeated ingestion with the same insert_ids must not create duplicates
        res2 = associate_and_persist_operational_events(
            self.incident_id,
            errors_data=errors_data,
        )
        self.assertEqual(res2["status"], "success")
        events2 = list_operational_events(self.incident_id)
        self.assertEqual(len(events2), 2)

        # Verify attributes
        err_event = events2[0]
        self.assertEqual(err_event["event_type"], "error_log")
        self.assertEqual(err_event["service"], "payment-api")
        self.assertIn("DATABASE_URL invalid", err_event["description"])
        self.assertEqual(
            err_event["evidence"]["insert_id"], "insert-log-12345"
        )

    def test_association_requires_matching_service_not_just_timestamp(self):
        """Ensure events from a mismatched service are rejected regardless of timing."""
        mismatched_deployments = {
            "status": "success",
            "service": "order-service",  # Different service
            "revisions": [
                {
                    "name": "order-service-00005-xyz",
                    "created_at": "2026-10-15T17:55:00Z",
                    "ready": True,
                    "service": "order-service",
                }
            ],
            "traffic": [],
        }

        mismatched_errors = {
            "status": "success",
            "service": "auth-service",  # Different service
            "entries": [
                {
                    "timestamp": "2026-10-15T17:55:00Z",
                    "severity": "ERROR",
                    "payload": "Failed login token verification",
                    "revision": "auth-service-00003-abc",
                    "service": "auth-service",
                }
            ],
        }

        associate_and_persist_operational_events(
            self.incident_id,
            deployments_data=mismatched_deployments,
            errors_data=mismatched_errors,
        )

        # Neither should have been associated with this incident!
        events = list_operational_events(self.incident_id)
        self.assertEqual(len(events), 0)

    def test_error_handling_missing_credentials(self):
        """Verify DefaultCredentialsError is handled gracefully with informative error messages."""
        with patch("google.cloud.run_v2.ServicesClient") as mock_client:
            mock_client.side_effect = (
                google.auth.exceptions.DefaultCredentialsError(
                    "File not found / no credentials"
                )
            )

            res = fetch_cloud_run_deployments(
                "payment-api", project="dummy-proj"
            )
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["error_type"], "MISSING_CREDENTIALS")
            self.assertIn("DefaultCredentialsError", res["error"])

    def test_error_handling_permission_denied(self):
        """Verify PermissionDenied returns a meaningful message without pretending success."""
        with patch("google.cloud.run_v2.ServicesClient") as mock_client:
            mock_client.side_effect = gcp_exceptions.PermissionDenied(
                "403 Caller lacks run.services.get permission"
            )

            res = fetch_cloud_run_deployments(
                "payment-api", project="dummy-proj"
            )
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["error_type"], "PERMISSION_DENIED")
            self.assertIn("PermissionDenied", res["error"])
            self.assertIn("run.services.get", res["error"])

    def test_error_handling_not_found(self):
        """Verify NotFound returns a clear message when a Cloud Run service does not exist."""
        with patch("google.cloud.run_v2.ServicesClient") as mock_client:
            mock_client.side_effect = gcp_exceptions.NotFound(
                "404 Service payment-api not found in region us-central1"
            )

            res = fetch_cloud_run_deployments(
                "payment-api", project="dummy-proj"
            )
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["error_type"], "NOT_FOUND")
            self.assertIn("NotFound", res["error"])

    def test_local_mode_fallback_preservation(self):
        """If GCP project is unconfigured, ingestion preserves local fallback."""
        with patch(
            "backend.ingestion.get_effective_project", return_value=None
        ):
            res = ingest_operational_events_for_incident(self.incident_id)
            self.assertEqual(res["status"], "skipped")
            self.assertEqual(res["mode"], "local_fallback")
            self.assertEqual(res["events_persisted"], 0)
            self.assertIn("local mode", res["message"])

    def test_api_ingest_endpoint(self):
        """Test POST /incidents/{incident_id}/ingest route."""
        # Non-existent incident -> 404
        r_404 = client.post("/incidents/non_existent_inc/ingest")
        self.assertEqual(r_404.status_code, 404)

        # Existing incident in local fallback mode
        with patch(
            "backend.ingestion.get_effective_project", return_value=None
        ):
            r = client.post(f"/incidents/{self.incident_id}/ingest")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertEqual(data["status"], "skipped")
            self.assertEqual(data["mode"], "local_fallback")

    def test_operational_events_timeline_api(self):
        """Ensure GET /incidents/{incident_id}/events returns persisted events for frontend timeline."""
        # Add a test operational event
        r = client.post(
            f"/incidents/{self.incident_id}/events",
            json={
                "event_type": "deployment",
                "service": "payment-api",
                "description": "Revision payment-api-00002 deployed",
                "evidence": {"ready": True},
            },
        )
        self.assertEqual(r.status_code, 201)

        # Fetch events for timeline
        get_res = client.get(f"/incidents/{self.incident_id}/events")
        self.assertEqual(get_res.status_code, 200)
        events = get_res.json()
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["service"], "payment-api")
        self.assertEqual(events[0]["event_type"], "deployment")
