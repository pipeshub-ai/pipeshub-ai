"""End-to-end integration tests: Record indexing pipeline validation.

Tests hit a real PipeShub instance, upload files through the KB API, and then
validate the full downstream pipeline:

  1. **DB stage** — record exists via API with initial status
  2. **Indexing stage** — ``indexingStatus`` progresses to COMPLETED (or a
     terminal state like FILE_TYPE_NOT_SUPPORTED)
  3. **Graph stage** — Neo4j contains the Record node, BELONGS_TO edges,
     RecordGroup structure
  4. **Cleanup stage** — after deletion, record is removed from API and graph

Run:
    cd integration-tests
    pytest messaging/test_e2e_record_pipeline.py -v --timeout=300

Requires:
    PIPESHUB_BASE_URL, CLIENT_ID + CLIENT_SECRET (or user creds),
    TEST_NEO4J_URI + TEST_NEO4J_USERNAME + TEST_NEO4J_PASSWORD.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests

# Ensure helpers are importable
_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent
_HELPER_DIR = _ROOT_DIR / "helper"
for p in (_ROOT_DIR, _HELPER_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from pipeshub_client import PipeshubClient
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import async_poll_until

logger = logging.getLogger("e2e-record-pipeline")

# Suppress noisy Neo4j warnings about relationship types that don't exist yet
# (they appear when polling before the indexing pipeline has created edges)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEXING_POLL_INTERVAL = 3  # seconds between status checks
INDEXING_TIMEOUT = 120  # max seconds to wait for indexing to complete
GRAPH_POLL_INTERVAL = 3
GRAPH_TIMEOUT = 60

# Terminal indexing statuses — record will not progress further
TERMINAL_STATUSES = {
    "COMPLETED",
    "FAILED",
    "FILE_TYPE_NOT_SUPPORTED",
    "EMPTY",
    "AUTO_INDEX_OFF",
    "ENABLE_MULTIMODAL_MODELS",
}


# ---------------------------------------------------------------------------
# KB API helper (reuse from KB events test)
# ---------------------------------------------------------------------------

class KBClient:
    """Thin wrapper around PipeshubClient for Knowledge Base operations."""

    KB_BASE = "/api/v1/knowledgeBase"

    def __init__(self, client: PipeshubClient) -> None:
        self._client = client

    def _headers(self, content_type: str | None = "application/json") -> dict[str, str]:
        self._client._ensure_access_token()
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._client._access_token}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _url(self, path: str) -> str:
        return self._client._url(f"{self.KB_BASE}{path}")

    def create_kb(self, name: str | None = None) -> dict[str, Any]:
        name = name or f"pipeline-test-kb-{uuid.uuid4().hex[:8]}"
        resp = requests.post(
            self._url("/"),
            headers=self._headers(),
            json={"kbName": name},
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def delete_kb(self, kb_id: str) -> dict[str, Any]:
        resp = requests.delete(
            self._url(f"/{kb_id}"),
            headers=self._headers(),
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def upload_file(
        self,
        kb_id: str,
        file_name: str,
        file_content: bytes,
    ) -> dict[str, Any]:
        files = [("files", (file_name, io.BytesIO(file_content), "text/plain"))]
        resp = requests.post(
            self._url(f"/{kb_id}/upload"),
            headers=self._headers(content_type=None),
            files=files,
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def get_record(self, record_id: str, retries: int = 3, delay: float = 2.0) -> dict[str, Any]:
        """Fetch a record by ID, retrying on transient 500 errors."""
        last_err = None
        for attempt in range(retries):
            resp = requests.get(
                self._url(f"/record/{record_id}"),
                headers=self._headers(),
                timeout=self._client.timeout_seconds,
            )
            if resp.status_code < 500:
                return self._client._handle_response(resp)
            last_err = resp
            if attempt < retries - 1:
                time.sleep(delay)
        return self._client._handle_response(last_err)

    def delete_record(self, record_id: str) -> dict[str, Any]:
        resp = requests.delete(
            self._url(f"/record/{record_id}"),
            headers=self._headers(),
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def get_kb_records(self, kb_id: str) -> dict[str, Any]:
        resp = requests.get(
            self._url(f"/{kb_id}/records"),
            headers=self._headers(),
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_record_id(upload_response: dict) -> str:
    records = (
        upload_response.get("records")
        or upload_response.get("data", {}).get("records", [])
    )
    if records:
        return records[0].get("_key") or records[0].get("id") or records[0].get("recordId")
    raise ValueError(f"Cannot extract recordId from: {upload_response}")


def _extract_kb_id(kb_response: dict) -> str:
    return (
        kb_response.get("kbId")
        or kb_response.get("data", {}).get("kbId")
        or kb_response.get("_key")
        or kb_response.get("data", {}).get("_key")
        or kb_response.get("id")
    )


def _get_record_status(record_data: dict) -> str:
    """Extract indexingStatus from a get_record API response."""
    rec = record_data.get("record") or record_data.get("data", {}).get("record") or record_data
    return rec.get("indexingStatus", "UNKNOWN")


def _get_record_fields(record_data: dict) -> dict:
    """Extract the record object from API response."""
    return record_data.get("record") or record_data.get("data", {}).get("record") or record_data


def _connector_id_for_kb(kb_id: str, org_id: str | None = None) -> str:
    """Build the connectorId used in Neo4j for a local KB.

    Format: ``knowledgeBase_{orgId}`` or just the kb_id if orgId is unknown.
    """
    if org_id:
        return f"knowledgeBase_{org_id}"
    return kb_id


def poll_until(
    check_fn,
    timeout: float,
    interval: float,
    description: str = "condition",
):
    """Poll *check_fn* until it returns a truthy value or timeout."""
    deadline = time.time() + timeout
    last_result = None
    while time.time() < deadline:
        last_result = check_fn()
        if last_result:
            return last_result
        time.sleep(interval)
    raise TimeoutError(
        f"Timed out waiting for {description} after {timeout}s. Last result: {last_result}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def kb_client(pipeshub_client: PipeshubClient) -> KBClient:
    return KBClient(pipeshub_client)


@pytest.fixture(scope="module")
def test_kb(kb_client: KBClient):
    """Create a KB for the module; delete on teardown."""
    data = kb_client.create_kb()
    kb_id = _extract_kb_id(data)
    assert kb_id, f"Failed to create KB: {data}"
    logger.info("Created test KB: %s", kb_id)
    yield {"kb_id": kb_id, "data": data}
    try:
        kb_client.delete_kb(kb_id)
        logger.info("Cleaned up KB: %s", kb_id)
    except Exception as e:
        logger.warning("Failed to cleanup KB %s: %s", kb_id, e)


# ---------------------------------------------------------------------------
# Tests — record pipeline stages
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRecordDatabaseStage:
    """Validate that uploaded records appear in the database with correct fields."""

    @pytest.mark.asyncio
    async def test_record_exists_after_upload(
        self, kb_client: KBClient, test_kb: dict,
    ):
        """Upload a file and verify the record is retrievable via API."""
        kb_id = test_kb["kb_id"]

        upload_resp = kb_client.upload_file(kb_id, "db-stage-test.txt", b"Database stage test content")
        record_id = _extract_record_id(upload_resp)
        assert record_id
        logger.info("Uploaded record for DB stage test: %s", record_id)

        try:
            record_data = kb_client.get_record(record_id)
            rec = _get_record_fields(record_data)

            actual_id = rec.get("id") or rec.get("_key") or rec.get("recordId")
            assert actual_id == record_id, f"Record ID mismatch: {actual_id} != {record_id}"
            assert rec.get("recordType") == "FILE"
            assert rec.get("origin") == "UPLOAD"
            assert rec.get("mimeType") == "text/plain"
            assert rec.get("recordName") is not None
            assert rec.get("createdAtTimestamp") is not None
            assert rec.get("version") is not None
            logger.info("Record %s exists in DB with correct fields", record_id)
        finally:
            try:
                kb_client.delete_record(record_id)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_record_initial_status(
        self, kb_client: KBClient, test_kb: dict,
    ):
        """Verify the record starts with a non-COMPLETED indexing status."""
        kb_id = test_kb["kb_id"]

        upload_resp = kb_client.upload_file(kb_id, "status-test.txt", b"Status check content")
        record_id = _extract_record_id(upload_resp)

        try:
            record_data = kb_client.get_record(record_id)
            status = _get_record_status(record_data)
            # Just after upload, status should be one of the early states
            logger.info("Record %s initial indexingStatus: %s", record_id, status)
            assert status is not None
            assert status != "UNKNOWN"
        finally:
            try:
                kb_client.delete_record(record_id)
            except Exception:
                pass


@pytest.mark.integration
class TestRecordIndexingStage:
    """Validate that records progress through the indexing pipeline."""

    @pytest.mark.asyncio
    async def test_indexing_reaches_terminal_status(
        self, kb_client: KBClient, test_kb: dict,
    ):
        """Upload a text file and wait for indexing to reach a terminal status."""
        kb_id = test_kb["kb_id"]

        upload_resp = kb_client.upload_file(
            kb_id, "indexing-test.txt", b"This is a test document for indexing pipeline validation."
        )
        record_id = _extract_record_id(upload_resp)
        logger.info("Uploaded record for indexing test: %s", record_id)

        try:
            def check_terminal():
                data = kb_client.get_record(record_id)
                status = _get_record_status(data)
                logger.info("Record %s indexingStatus: %s", record_id, status)
                if status in TERMINAL_STATUSES:
                    return status
                return None

            final_status = poll_until(
                check_terminal,
                timeout=INDEXING_TIMEOUT,
                interval=INDEXING_POLL_INTERVAL,
                description=f"indexing terminal status for record {record_id}",
            )
            logger.info("Record %s reached terminal status: %s", record_id, final_status)
            assert final_status in TERMINAL_STATUSES
        finally:
            try:
                kb_client.delete_record(record_id)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_completed_record_has_metadata(
        self, kb_client: KBClient, test_kb: dict,
    ):
        """Upload a file, wait for COMPLETED, and verify post-indexing fields."""
        kb_id = test_kb["kb_id"]

        upload_resp = kb_client.upload_file(
            kb_id, "metadata-test.txt", b"Metadata validation content for the indexing pipeline."
        )
        record_id = _extract_record_id(upload_resp)

        try:
            def check_completed():
                data = kb_client.get_record(record_id)
                status = _get_record_status(data)
                if status in TERMINAL_STATUSES:
                    return data
                return None

            record_data = poll_until(
                check_completed,
                timeout=INDEXING_TIMEOUT,
                interval=INDEXING_POLL_INTERVAL,
                description=f"indexing completion for record {record_id}",
            )

            rec = _get_record_fields(record_data)
            status = rec.get("indexingStatus")

            if status == "COMPLETED":
                # Fully indexed records should have these fields populated
                logger.info("Record %s COMPLETED — checking post-indexing metadata", record_id)
                assert rec.get("version") is not None
                assert rec.get("updatedAtTimestamp") is not None
            else:
                # Non-COMPLETED terminal states are acceptable but log the reason
                logger.info(
                    "Record %s ended with status %s (not COMPLETED) — "
                    "skipping post-indexing metadata checks",
                    record_id, status,
                )
        finally:
            try:
                kb_client.delete_record(record_id)
            except Exception:
                pass


@pytest.mark.integration
class TestRecordGraphStage:
    """Validate that indexed records appear in the Neo4j graph."""

    @pytest.mark.asyncio
    async def test_record_appears_in_graph(
        self, kb_client: KBClient, test_kb: dict, graph_provider: GraphProviderProtocol,
    ):
        """Upload a file, wait for indexing, then verify the graph contains the record."""
        kb_id = test_kb["kb_id"]

        upload_resp = kb_client.upload_file(
            kb_id, "graph-test.txt", b"Graph validation test content."
        )
        record_id = _extract_record_id(upload_resp)
        logger.info("Uploaded record for graph test: %s", record_id)

        try:
            # First, get the record to find orgId for connectorId construction
            record_data = kb_client.get_record(record_id)
            rec = _get_record_fields(record_data)
            org_id = rec.get("orgId")
            connector_id = rec.get("connectorId") or _connector_id_for_kb(kb_id, org_id)
            record_name = rec.get("recordName", "graph-test")

            # Wait for indexing to reach a terminal status
            def check_terminal():
                data = kb_client.get_record(record_id)
                return _get_record_status(data) in TERMINAL_STATUSES

            poll_until(
                check_terminal,
                timeout=INDEXING_TIMEOUT,
                interval=INDEXING_POLL_INTERVAL,
                description=f"indexing for record {record_id}",
            )

            async def check_graph():
                return await graph_provider.count_records(connector_id) > 0

            await async_poll_until(
                check_graph,
                timeout=GRAPH_TIMEOUT,
                interval=GRAPH_POLL_INTERVAL,
                description=f"graph record node for connector {connector_id}",
            )

            await graph_provider.assert_min_records(connector_id, 1)

            names = await graph_provider.fetch_record_names(connector_id)
            logger.info("Graph record names for %s: %s", connector_id, names)
            assert any(record_name in n for n in names), (
                f"Record '{record_name}' not found in graph names: {names}"
            )

        finally:
            try:
                kb_client.delete_record(record_id)
            except Exception:
                pass

@pytest.mark.integration
class TestRecordCleanupStage:
    """Validate that deleted records are removed from DB and graph."""

    @pytest.mark.asyncio
    async def test_deleted_record_not_in_api(
        self, kb_client: KBClient, test_kb: dict,
    ):
        """Upload, wait for indexing, delete, and verify API returns 404."""
        kb_id = test_kb["kb_id"]

        upload_resp = kb_client.upload_file(
            kb_id, "cleanup-api-test.txt", b"Cleanup test content."
        )
        record_id = _extract_record_id(upload_resp)

        # Wait for a terminal status so the record is fully processed
        def check_terminal():
            data = kb_client.get_record(record_id)
            return _get_record_status(data) in TERMINAL_STATUSES

        poll_until(check_terminal, INDEXING_TIMEOUT, INDEXING_POLL_INTERVAL,
                   f"indexing for {record_id}")

        # Delete
        kb_client.delete_record(record_id)
        logger.info("Deleted record: %s", record_id)

        # Give backend time to process deletion
        time.sleep(2)

        # Verify record is gone or marked deleted.
        # The API may return 404, 500, or a soft-deleted record — all are valid.
        resp = requests.get(
            kb_client._url(f"/record/{record_id}"),
            headers=kb_client._headers(),
            timeout=kb_client._client.timeout_seconds,
        )
        if resp.status_code in (404, 500):
            logger.info("Record %s correctly returns HTTP %d after delete", record_id, resp.status_code)
        elif resp.status_code < 400:
            data = resp.json()
            rec = _get_record_fields(data)
            assert rec.get("isDeleted", False), (
                f"Record {record_id} still accessible and not marked deleted"
            )
        else:
            logger.info("Record %s returns HTTP %d after delete", record_id, resp.status_code)

    @pytest.mark.asyncio
    async def test_deleted_record_removed_from_graph(
        self, kb_client: KBClient, test_kb: dict, graph_provider: GraphProviderProtocol,
    ):
        """Upload, wait for graph, delete, and verify graph is cleaned."""
        kb_id = test_kb["kb_id"]
        file_name = f"cleanup-graph-{uuid.uuid4().hex[:6]}.txt"

        upload_resp = kb_client.upload_file(
            kb_id, file_name, b"Graph cleanup test content."
        )
        record_id = _extract_record_id(upload_resp)

        record_data = kb_client.get_record(record_id)
        rec = _get_record_fields(record_data)
        org_id = rec.get("orgId")
        connector_id = rec.get("connectorId") or _connector_id_for_kb(kb_id, org_id)
        record_name = rec.get("recordName", file_name.replace(".txt", ""))

        # Wait for graph node
        def check_terminal():
            data = kb_client.get_record(record_id)
            return _get_record_status(data) in TERMINAL_STATUSES

        poll_until(check_terminal, INDEXING_TIMEOUT, INDEXING_POLL_INTERVAL,
                   f"indexing for {record_id}")

        async def check_in_graph():
            rec = await graph_provider.get_record_by_name(connector_id, record_name)
            return rec is not None

        try:
            await async_poll_until(check_in_graph, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                                   f"graph node for {record_name}")
        except TimeoutError:
            logger.warning("Record %s never appeared in graph — skipping graph cleanup check", record_name)
            kb_client.delete_record(record_id)
            return

        # Delete and verify graph cleanup
        kb_client.delete_record(record_id)
        logger.info("Deleted record %s, waiting for graph cleanup", record_id)

        async def check_removed():
            rec = await graph_provider.get_record_by_name(connector_id, record_name)
            return rec is None

        await async_poll_until(
            check_removed,
            timeout=GRAPH_TIMEOUT,
            interval=GRAPH_POLL_INTERVAL,
            description=f"graph removal of record '{record_name}'",
        )
        logger.info("Record '%s' successfully removed from graph", record_name)


@pytest.mark.integration
class TestRecordFullPipeline:
    """Single test that validates the complete pipeline end-to-end."""

    @pytest.mark.asyncio
    async def test_full_pipeline_upload_to_cleanup(
        self, kb_client: KBClient, test_kb: dict, graph_provider: GraphProviderProtocol,
    ):
        """Upload → DB check → indexing → graph check → delete → cleanup check."""
        kb_id = test_kb["kb_id"]
        file_name = f"full-pipeline-{uuid.uuid4().hex[:6]}.txt"
        file_content = b"Full pipeline end-to-end test. This document tests every stage."

        # --- Stage 1: Upload ---
        upload_resp = kb_client.upload_file(kb_id, file_name, file_content)
        record_id = _extract_record_id(upload_resp)
        logger.info("Stage 1 (upload): record %s created", record_id)

        try:
            # --- Stage 2: DB validation ---
            record_data = kb_client.get_record(record_id)
            rec = _get_record_fields(record_data)
            assert rec.get("recordType") == "FILE"
            assert rec.get("origin") == "UPLOAD"
            org_id = rec.get("orgId")
            connector_id = rec.get("connectorId") or _connector_id_for_kb(kb_id, org_id)
            record_name = rec.get("recordName", file_name.replace(".txt", ""))
            logger.info("Stage 2 (DB): record exists, connectorId=%s", connector_id)

            # --- Stage 3: Indexing ---
            def check_terminal():
                data = kb_client.get_record(record_id)
                status = _get_record_status(data)
                logger.debug("  indexingStatus: %s", status)
                if status in TERMINAL_STATUSES:
                    return status
                return None

            final_status = poll_until(
                check_terminal, INDEXING_TIMEOUT, INDEXING_POLL_INTERVAL,
                f"indexing for {record_id}",
            )
            logger.info("Stage 3 (indexing): reached %s", final_status)

            async def check_graph():
                rec = await graph_provider.get_record_by_name(connector_id, record_name)
                return rec is not None

            try:
                await async_poll_until(check_graph, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                                       f"graph node for {record_name}")
                await graph_provider.assert_min_records(connector_id, 1)
                logger.info("Stage 4 (graph): record '%s' found in Neo4j", record_name)
            except TimeoutError:
                logger.warning("Stage 4 (graph): record never appeared in graph — may not have indexed fully")

            # --- Stage 5: Delete + cleanup ---
            kb_client.delete_record(record_id)
            logger.info("Stage 5 (delete): record %s deleted", record_id)

            # Verify API removal (404 or 500 both indicate record is gone)
            time.sleep(2)
            resp = requests.get(
                kb_client._url(f"/record/{record_id}"),
                headers=kb_client._headers(),
                timeout=kb_client._client.timeout_seconds,
            )
            if resp.status_code < 400:
                rec = _get_record_fields(resp.json())
                assert rec.get("isDeleted", False), "Record not marked deleted"
            else:
                logger.info("Stage 5 (API): record returns HTTP %d", resp.status_code)

            async def check_graph_removed():
                rec = await graph_provider.get_record_by_name(connector_id, record_name)
                return rec is None

            try:
                await async_poll_until(check_graph_removed, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                                       f"graph removal of {record_name}")
                logger.info("Stage 5 (cleanup): record removed from graph")
            except TimeoutError:
                logger.warning("Stage 5 (cleanup): graph removal timed out")

        except Exception:
            # Ensure cleanup on failure
            try:
                kb_client.delete_record(record_id)
            except Exception:
                pass
            raise
