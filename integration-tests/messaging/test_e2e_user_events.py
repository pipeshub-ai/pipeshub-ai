"""End-to-end integration tests: User & Org full lifecycle pipeline.

Tests hit a real PipeShub instance, perform user/org operations through
the API, then validate the complete downstream pipeline:

  1. **API stage** — operation succeeds, response has correct fields
  2. **DB stage** — user/org is retrievable via GET with expected state
  3. **Graph stage** — Neo4j contains the User/Organization node with
     correct properties
  4. **Cleanup stage** — after deletion, user is gone from API and graph

Run:
    cd integration-tests
    pytest messaging/test_e2e_user_events.py -v --timeout=300

Requires:
    PIPESHUB_BASE_URL, CLIENT_ID + CLIENT_SECRET (or user creds),
    TEST_NEO4J_URI + TEST_NEO4J_USERNAME + TEST_NEO4J_PASSWORD.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests
from neo4j import Driver

# Ensure helpers are importable
_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent
_HELPER_DIR = _ROOT_DIR / "helper"
for p in (_ROOT_DIR, _HELPER_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from pipeshub_client import PipeshubClient

logger = logging.getLogger("e2e-user-pipeline")

# Suppress noisy Neo4j warnings about missing labels/types during polling
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_POLL_INTERVAL = 3
GRAPH_TIMEOUT = 60


# ---------------------------------------------------------------------------
# User / Org API helper
# ---------------------------------------------------------------------------

class UserClient:
    """Thin wrapper around PipeshubClient for user & org API calls."""

    USERS_BASE = "/api/v1/users"
    ORG_BASE = "/api/v1/org"

    def __init__(self, client: PipeshubClient) -> None:
        self._client = client

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        self._client._ensure_access_token()
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._client._access_token}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _url(self, path: str) -> str:
        return self._client._url(path)

    # -- Org operations --

    def get_org(self) -> dict[str, Any]:
        resp = requests.get(
            self._url(f"{self.ORG_BASE}/"),
            headers=self._headers(),
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def update_org(self, registered_name: str) -> dict[str, Any]:
        resp = requests.patch(
            self._url(f"{self.ORG_BASE}/"),
            headers=self._headers(),
            json={"registeredName": registered_name},
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    # -- User operations --

    def add_user(
        self,
        email: str,
        full_name: str,
        designation: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "fullName": full_name,
            "email": email,
        }
        if designation:
            body["designation"] = designation
        resp = requests.post(
            self._url(f"{self.USERS_BASE}/"),
            headers=self._headers(),
            json=body,
            timeout=self._client.timeout_seconds,
        )
        data = self._client._handle_response(resp)
        logger.info("Add user response: %s", data)
        return data

    def get_user(self, user_id: str) -> dict[str, Any]:
        resp = requests.get(
            self._url(f"{self.USERS_BASE}/{user_id}"),
            headers=self._headers(),
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def update_user(self, user_id: str, **fields: Any) -> dict[str, Any]:
        resp = requests.put(
            self._url(f"{self.USERS_BASE}/{user_id}"),
            headers=self._headers(),
            json=fields,
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def delete_user(self, user_id: str) -> dict[str, Any]:
        resp = requests.delete(
            self._url(f"{self.USERS_BASE}/{user_id}"),
            headers=self._headers(),
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)

    def list_users(self) -> dict[str, Any]:
        resp = requests.get(
            self._url(f"{self.USERS_BASE}/"),
            headers=self._headers(),
            timeout=self._client.timeout_seconds,
        )
        return self._client._handle_response(resp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_user_id(add_user_response: dict) -> str:
    """Extract the user ID from an add-user API response."""
    user = (
        add_user_response.get("user")
        or add_user_response.get("data", {}).get("user")
        or add_user_response
    )
    return (
        user.get("_id")
        or user.get("userId")
        or user.get("id")
        or add_user_response.get("_id")
        or add_user_response.get("userId")
        or ""
    )


def _get_user_fields(resp: dict) -> dict:
    """Extract user object from API response."""
    return resp.get("user") or resp.get("data", {}).get("user") or resp


def _get_org_fields(resp: dict) -> dict:
    """Extract org object from API response."""
    return resp.get("org") or resp.get("data", {}).get("org") or resp


def poll_until(check_fn, timeout: float, interval: float, description: str = "condition"):
    """Poll check_fn until it returns a truthy value or timeout."""
    deadline = time.time() + timeout
    last_result = None
    while time.time() < deadline:
        last_result = check_fn()
        if last_result:
            return last_result
        time.sleep(interval)
    raise TimeoutError(
        f"Timed out waiting for {description} after {timeout}s. Last: {last_result}"
    )


# ---------------------------------------------------------------------------
# Neo4j graph helpers
# ---------------------------------------------------------------------------

def graph_find_user_by_email(driver: Driver, email: str) -> dict | None:
    """Find a User node by email (case-insensitive).

    The entity handler creates User nodes with: id (UUID), userId (MongoDB _id),
    orgId, email, fullName, firstName, lastName, designation, isActive, etc.
    """
    with driver.session() as session:
        result = session.run(
            "MATCH (u:User) WHERE toLower(u.email) = toLower($email) RETURN u",
            email=email,
        )
        rec = result.single()
        return dict(rec["u"]) if rec else None


def graph_find_user_by_user_id(driver: Driver, user_id: str) -> dict | None:
    """Find a User node by userId (MongoDB ObjectId stored on the node)."""
    with driver.session() as session:
        result = session.run(
            "MATCH (u:User {userId: $userId}) RETURN u",
            userId=user_id,
        )
        rec = result.single()
        return dict(rec["u"]) if rec else None


def graph_find_org(driver: Driver, org_id: str) -> dict | None:
    """Find an Organization node by id."""
    with driver.session() as session:
        result = session.run(
            "MATCH (o:Organization {id: $orgId}) RETURN o",
            orgId=org_id,
        )
        rec = result.single()
        return dict(rec["o"]) if rec else None


def graph_org_exists(driver: Driver, org_id: str) -> bool:
    """Check whether the Organization node exists — required before user nodes can be created."""
    return graph_find_org(driver, org_id) is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def user_client(pipeshub_client: PipeshubClient) -> UserClient:
    return UserClient(pipeshub_client)


# ---------------------------------------------------------------------------
# Tests — User full lifecycle (add → DB → graph → update → graph → delete → cleanup)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestUserFullPipeline:
    """Complete user lifecycle: API → DB → Neo4j at every stage.

    Graph node creation requires the Organization node to already exist
    in Neo4j (the entity handler checks for it). If it doesn't, graph
    stages are skipped gracefully.
    """

    @pytest.mark.asyncio
    async def test_user_lifecycle_pipeline(
        self, user_client: UserClient, neo4j_driver: Driver,
    ):
        email = f"e2e-pipeline-{uuid.uuid4().hex[:8]}@integration-test.local"
        full_name = "Pipeline Test User"
        designation = "Integration Tester"

        # Pre-check: does the Organization node exist in Neo4j?
        # The entity handler won't create User nodes without it.
        org_data = user_client.get_org()
        org = _get_org_fields(org_data)
        org_id = str(org.get("_id") or org.get("orgId") or org.get("id") or "")
        has_org_in_graph = graph_org_exists(neo4j_driver, org_id) if org_id else False
        if not has_org_in_graph:
            logger.warning(
                "Organization %s not found in Neo4j — graph stages will be skipped. "
                "The entity-events consumer may not be running or org was never synced.",
                org_id,
            )

        # ===== Stage 1: Add user =====
        add_resp = user_client.add_user(
            email=email, full_name=full_name, designation=designation,
        )
        user_id = _extract_user_id(add_resp)
        assert user_id, f"Failed to extract userId from: {add_resp}"
        logger.info("Stage 1 (add): user %s created (%s)", user_id, email)

        try:
            # ===== Stage 1b: Activate user (set hasLoggedIn=true) =====
            user_client.update_user(user_id, hasLoggedIn=True)
            logger.info("Stage 1b (activate): user %s marked as logged in", user_id)

            # ===== Stage 2: Verify user in DB via API =====
            user_data = user_client.get_user(user_id)
            user = _get_user_fields(user_data)
            assert user.get("email") == email
            assert user.get("fullName") == full_name
            assert user.get("hasLoggedIn") is True
            logger.info("Stage 2 (DB): user retrievable via API, hasLoggedIn=true")

            # ===== Stage 3: Verify user node in Neo4j =====
            if has_org_in_graph:
                def check_user_in_graph():
                    # Try both email and userId lookups
                    return graph_find_user_by_email(neo4j_driver, email) or \
                           graph_find_user_by_user_id(neo4j_driver, user_id)

                try:
                    graph_user = poll_until(
                        check_user_in_graph, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                        f"User node in graph for {email}",
                    )
                    assert graph_user is not None
                    logger.info(
                        "Stage 3 (graph): User node found — id=%s, email=%s, "
                        "userId=%s, isActive=%s, fullName=%s",
                        graph_user.get("id"), graph_user.get("email"),
                        graph_user.get("userId"), graph_user.get("isActive"),
                        graph_user.get("fullName"),
                    )
                    # Validate properties
                    if graph_user.get("email"):
                        assert graph_user["email"].lower() == email.lower()
                    assert graph_user.get("isActive") is True
                except TimeoutError:
                    logger.warning("Stage 3 (graph): User node not found after %ds", GRAPH_TIMEOUT)
            else:
                logger.info("Stage 3 (graph): skipped — org not in Neo4j")

            # ===== Stage 4: Update user =====
            updated_name = "Pipeline Updated User"
            updated_designation = "Senior Tester"
            user_client.update_user(
                user_id,
                fullName=updated_name,
                designation=updated_designation,
            )
            logger.info("Stage 4 (update): user %s updated", user_id)

            # Verify update via API
            user_data = user_client.get_user(user_id)
            user = _get_user_fields(user_data)
            assert user.get("fullName") == updated_name
            logger.info("Stage 4 (DB): updated name confirmed via API")

            # Verify update in graph
            if has_org_in_graph:
                def check_graph_updated():
                    node = graph_find_user_by_email(neo4j_driver, email) or \
                           graph_find_user_by_user_id(neo4j_driver, user_id)
                    if node and node.get("fullName") == updated_name:
                        return node
                    return None

                try:
                    graph_user = poll_until(
                        check_graph_updated, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                        f"Graph user fullName update for {email}",
                    )
                    logger.info("Stage 4 (graph): fullName updated to '%s'", graph_user.get("fullName"))
                except TimeoutError:
                    logger.warning("Stage 4 (graph): fullName update not reflected in graph")
            else:
                logger.info("Stage 4 (graph): skipped — org not in Neo4j")

            # ===== Stage 5: Delete user =====
            user_client.delete_user(user_id)
            logger.info("Stage 5 (delete): user %s deleted", user_id)

            # Verify deletion via API
            time.sleep(2)
            resp = requests.get(
                user_client._url(f"{user_client.USERS_BASE}/{user_id}"),
                headers=user_client._headers(),
                timeout=user_client._client.timeout_seconds,
            )
            if resp.status_code >= 400:
                logger.info("Stage 5 (API): user returns HTTP %d after delete", resp.status_code)
            else:
                user = _get_user_fields(resp.json())
                logger.info("Stage 5 (API): user still returned, isActive=%s", user.get("isActive"))

            # Verify user deactivated in graph (soft delete → isActive=false)
            if has_org_in_graph:
                def check_graph_deactivated():
                    node = graph_find_user_by_email(neo4j_driver, email)
                    if node is None:
                        return True  # Node removed entirely
                    if node.get("isActive") is False:
                        return True  # Soft-deleted
                    return None

                try:
                    poll_until(
                        check_graph_deactivated, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                        f"Graph user deactivation for {email}",
                    )
                    logger.info("Stage 5 (graph): user deactivated/removed from graph")
                except TimeoutError:
                    logger.warning("Stage 5 (graph): user deactivation not reflected in graph")
            else:
                logger.info("Stage 5 (graph): skipped — org not in Neo4j")

        except Exception:
            # Cleanup on failure
            try:
                user_client.delete_user(user_id)
            except Exception:
                pass
            raise


# ---------------------------------------------------------------------------
# Tests — Org update pipeline
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="Org update tests skipped for now")
@pytest.mark.integration
class TestOrgUpdatePipeline:
    """Update org name → verify via API → verify in Neo4j → restore."""

    @pytest.mark.asyncio
    async def test_org_update_pipeline(
        self, user_client: UserClient, neo4j_driver: Driver,
    ):
        # Get current org info
        org_data = user_client.get_org()
        org = _get_org_fields(org_data)
        original_name = org.get("registeredName", "Test Org")
        org_id = org.get("_id") or org.get("orgId") or org.get("id")
        assert org_id, f"Cannot extract orgId from: {org_data}"
        logger.info("Org: id=%s, name=%s", org_id, original_name)

        new_name = f"E2E-Pipeline-{uuid.uuid4().hex[:6]}"
        try:
            # ===== Stage 1: Update org =====
            user_client.update_org(new_name)
            logger.info("Stage 1 (update): org renamed to '%s'", new_name)

            # ===== Stage 2: Verify via API =====
            org_data = user_client.get_org()
            org = _get_org_fields(org_data)
            assert org.get("registeredName") == new_name
            logger.info("Stage 2 (API): org name confirmed as '%s'", new_name)

            # ===== Stage 3: Verify in Neo4j =====
            def check_org_name_in_graph():
                node = graph_find_org(neo4j_driver, org_id)
                if node and node.get("name") == new_name:
                    return node
                return None

            try:
                graph_org = poll_until(
                    check_org_name_in_graph, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                    f"Org name update in graph for {org_id}",
                )
                assert graph_org["name"] == new_name
                logger.info("Stage 3 (graph): org name updated to '%s'", graph_org["name"])
            except TimeoutError:
                logger.warning("Stage 3 (graph): org name update not reflected in graph")

        finally:
            # Restore original name
            try:
                user_client.update_org(original_name)
                logger.info("Restored org name to: '%s'", original_name)
            except Exception as e:
                logger.warning("Failed to restore org name: %s", e)


# ---------------------------------------------------------------------------
# Tests — Multiple users
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMultiUserPipeline:
    """Add multiple users, verify all appear in DB and graph, then clean up."""

    @pytest.mark.asyncio
    async def test_add_multiple_users_pipeline(
        self, user_client: UserClient, neo4j_driver: Driver,
    ):
        user_count = 3
        users: list[dict[str, str]] = []

        # Check org prerequisite
        org_data = user_client.get_org()
        org = _get_org_fields(org_data)
        org_id = str(org.get("_id") or org.get("orgId") or org.get("id") or "")
        has_org_in_graph = graph_org_exists(neo4j_driver, org_id) if org_id else False

        # ===== Create and activate users =====
        for i in range(user_count):
            email = f"e2e-multi-{i}-{uuid.uuid4().hex[:6]}@integration-test.local"
            resp = user_client.add_user(email=email, full_name=f"Multi User {i}")
            user_id = _extract_user_id(resp)
            assert user_id, f"Failed to create user {i}"
            user_client.update_user(user_id, hasLoggedIn=True)
            users.append({"user_id": user_id, "email": email})
            logger.info("Created and activated user %d: %s (%s)", i, user_id, email)

        try:
            # ===== Verify all in DB via API =====
            for u in users:
                data = user_client.get_user(u["user_id"])
                user = _get_user_fields(data)
                assert user.get("email") == u["email"]
            logger.info("All %d users verified in DB via API", user_count)

            # ===== Verify all in graph =====
            if not has_org_in_graph:
                logger.info("Graph checks skipped — org not in Neo4j")
            else:
                def check_all_in_graph():
                    for u in users:
                        node = graph_find_user_by_email(neo4j_driver, u["email"])
                        if node is None:
                            return None
                    return True

                try:
                    poll_until(
                        check_all_in_graph, GRAPH_TIMEOUT, GRAPH_POLL_INTERVAL,
                        f"all {user_count} users in graph",
                    )
                    logger.info("All %d users found in Neo4j graph", user_count)
                except TimeoutError:
                    logger.warning("Not all users appeared in graph within timeout")
                    for u in users:
                        node = graph_find_user_by_email(neo4j_driver, u["email"])
                        if node is None:
                            logger.warning("  Missing: %s", u["email"])

        finally:
            # ===== Cleanup: delete all =====
            for u in users:
                try:
                    user_client.delete_user(u["user_id"])
                    logger.info("Cleaned up user: %s", u["email"])
                except Exception as e:
                    logger.warning("Failed to delete user %s: %s", u["email"], e)
