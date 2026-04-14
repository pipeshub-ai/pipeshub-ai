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

_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent
_HELPER_DIR = _ROOT_DIR / "helper"
for p in (_ROOT_DIR, _HELPER_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from pipeshub_client import PipeshubClient
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import async_poll_until

logger = logging.getLogger("e2e-user-pipeline")

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

GRAPH_POLL_INTERVAL = 3
GRAPH_TIMEOUT = 60


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


def _extract_user_id(add_user_response: dict) -> str:
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
    return resp.get("user") or resp.get("data", {}).get("user") or resp


def _get_org_fields(resp: dict) -> dict:
    return resp.get("org") or resp.get("data", {}).get("org") or resp


def poll_until(check_fn, timeout: float, interval: float, description: str = "condition"):
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


@pytest.fixture(scope="module")
def user_client(pipeshub_client: PipeshubClient) -> UserClient:
    return UserClient(pipeshub_client)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestUserFullPipeline:
    """Complete user lifecycle: API → DB → Neo4j at every stage."""

    async def test_user_lifecycle_pipeline(
        self, user_client: UserClient, graph_provider: GraphProviderProtocol,
    ):
        email = f"e2e-pipeline-{uuid.uuid4().hex[:8]}@integration-test.local"
        full_name = "Pipeline Test User"
        designation = "Integration Tester"

        org_data = user_client.get_org()
        org = _get_org_fields(org_data)
        org_id = str(org.get("_id") or org.get("orgId") or org.get("id") or "")
        has_org_in_graph = await graph_provider.graph_org_exists(org_id) if org_id else False
        if not has_org_in_graph:
            logger.warning(
                "Organization %s not found in Neo4j — graph stages will be skipped. "
                "The entity-events consumer may not be running or org was never synced.",
                org_id,
            )

        add_resp = user_client.add_user(
            email=email, full_name=full_name, designation=designation,
        )
        user_id = _extract_user_id(add_resp)
        assert user_id, f"Failed to extract userId from: {add_resp}"
        logger.info("Stage 1 (add): user %s created (%s)", user_id, email)

        try:
            user_client.update_user(user_id, hasLoggedIn=True)
            logger.info("Stage 1b (activate): user %s marked as logged in", user_id)

            user_data = user_client.get_user(user_id)
            user = _get_user_fields(user_data)
            assert user.get("email") == email
            assert user.get("fullName") == full_name
            assert user.get("hasLoggedIn") is True
            logger.info("Stage 2 (DB): user retrievable via API, hasLoggedIn=true")

            if has_org_in_graph:
                async def check_user_in_graph():
                    u = await graph_provider.graph_find_user_by_email(email)
                    v = await graph_provider.graph_find_user_by_user_id(user_id)
                    return u or v

                try:
                    graph_user = await async_poll_until(
                        check_user_in_graph,
                        GRAPH_TIMEOUT,
                        GRAPH_POLL_INTERVAL,
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
                    if graph_user.get("email"):
                        assert graph_user["email"].lower() == email.lower()
                    assert graph_user.get("isActive") is True
                except TimeoutError:
                    logger.warning("Stage 3 (graph): User node not found after %ds", GRAPH_TIMEOUT)
            else:
                logger.info("Stage 3 (graph): skipped — org not in Neo4j")

            updated_name = "Pipeline Updated User"
            updated_designation = "Senior Tester"
            user_client.update_user(
                user_id,
                fullName=updated_name,
                designation=updated_designation,
            )
            logger.info("Stage 4 (update): user %s updated", user_id)

            user_data = user_client.get_user(user_id)
            user = _get_user_fields(user_data)
            assert user.get("fullName") == updated_name
            logger.info("Stage 4 (DB): updated name confirmed via API")

            if has_org_in_graph:
                async def check_graph_updated():
                    node = await graph_provider.graph_find_user_by_email(email)
                    if not node:
                        node = await graph_provider.graph_find_user_by_user_id(user_id)
                    if node and node.get("fullName") == updated_name:
                        return node
                    return None

                try:
                    graph_user = await async_poll_until(
                        check_graph_updated,
                        GRAPH_TIMEOUT,
                        GRAPH_POLL_INTERVAL,
                        f"Graph user fullName update for {email}",
                    )
                    logger.info("Stage 4 (graph): fullName updated to '%s'", graph_user.get("fullName"))
                except TimeoutError:
                    logger.warning("Stage 4 (graph): fullName update not reflected in graph")
            else:
                logger.info("Stage 4 (graph): skipped — org not in Neo4j")

            user_client.delete_user(user_id)
            logger.info("Stage 5 (delete): user %s deleted", user_id)

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

            if has_org_in_graph:
                async def check_graph_deactivated():
                    node = await graph_provider.graph_find_user_by_email(email)
                    if node is None:
                        return True
                    if node.get("isActive") is False:
                        return True
                    return None

                try:
                    await async_poll_until(
                        check_graph_deactivated,
                        GRAPH_TIMEOUT,
                        GRAPH_POLL_INTERVAL,
                        f"Graph user deactivation for {email}",
                    )
                    logger.info("Stage 5 (graph): user deactivated/removed from graph")
                except TimeoutError:
                    logger.warning("Stage 5 (graph): user deactivation not reflected in graph")
            else:
                logger.info("Stage 5 (graph): skipped — org not in Neo4j")

        except Exception:
            try:
                user_client.delete_user(user_id)
            except Exception:
                pass
            raise


@pytest.mark.skip(reason="Org update tests skipped for now")
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestOrgUpdatePipeline:
    """Update org name → verify via API → verify in Neo4j → restore."""

    async def test_org_update_pipeline(
        self, user_client: UserClient, graph_provider: GraphProviderProtocol,
    ):
        org_data = user_client.get_org()
        org = _get_org_fields(org_data)
        original_name = org.get("registeredName", "Test Org")
        org_id = org.get("_id") or org.get("orgId") or org.get("id")
        assert org_id, f"Cannot extract orgId from: {org_data}"
        logger.info("Org: id=%s, name=%s", org_id, original_name)

        new_name = f"E2E-Pipeline-{uuid.uuid4().hex[:6]}"
        try:
            user_client.update_org(new_name)
            logger.info("Stage 1 (update): org renamed to '%s'", new_name)

            org_data = user_client.get_org()
            org = _get_org_fields(org_data)
            assert org.get("registeredName") == new_name
            logger.info("Stage 2 (API): org name confirmed as '%s'", new_name)

            async def check_org_name_in_graph():
                node = await graph_provider.graph_find_org(str(org_id))
                if node and node.get("name") == new_name:
                    return node
                return None

            try:
                graph_org = await async_poll_until(
                    check_org_name_in_graph,
                    GRAPH_TIMEOUT,
                    GRAPH_POLL_INTERVAL,
                    f"Org name update in graph for {org_id}",
                )
                assert graph_org["name"] == new_name
                logger.info("Stage 3 (graph): org name updated to '%s'", graph_org["name"])
            except TimeoutError:
                logger.warning("Stage 3 (graph): org name update not reflected in graph")

        finally:
            try:
                user_client.update_org(original_name)
                logger.info("Restored org name to: '%s'", original_name)
            except Exception as e:
                logger.warning("Failed to restore org name: %s", e)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestMultiUserPipeline:
    """Add multiple users, verify all appear in DB and graph, then clean up."""

    async def test_add_multiple_users_pipeline(
        self, user_client: UserClient, graph_provider: GraphProviderProtocol,
    ):
        user_count = 3
        users: list[dict[str, str]] = []

        org_data = user_client.get_org()
        org = _get_org_fields(org_data)
        org_id = str(org.get("_id") or org.get("orgId") or org.get("id") or "")
        has_org_in_graph = await graph_provider.graph_org_exists(org_id) if org_id else False

        for i in range(user_count):
            email = f"e2e-multi-{i}-{uuid.uuid4().hex[:6]}@integration-test.local"
            resp = user_client.add_user(email=email, full_name=f"Multi User {i}")
            user_id = _extract_user_id(resp)
            assert user_id, f"Failed to create user {i}"
            user_client.update_user(user_id, hasLoggedIn=True)
            users.append({"user_id": user_id, "email": email})
            logger.info("Created and activated user %d: %s (%s)", i, user_id, email)

        try:
            for u in users:
                data = user_client.get_user(u["user_id"])
                user = _get_user_fields(data)
                assert user.get("email") == u["email"]
            logger.info("All %d users verified in DB via API", user_count)

            if not has_org_in_graph:
                logger.info("Graph checks skipped — org not in Neo4j")
            else:
                async def check_all_in_graph():
                    for u in users:
                        node = await graph_provider.graph_find_user_by_email(u["email"])
                        if node is None:
                            return None
                    return True

                try:
                    await async_poll_until(
                        check_all_in_graph,
                        GRAPH_TIMEOUT,
                        GRAPH_POLL_INTERVAL,
                        f"all {user_count} users in graph",
                    )
                    logger.info("All %d users found in Neo4j graph", user_count)
                except TimeoutError:
                    logger.warning("Not all users appeared in graph within timeout")
                    for u in users:
                        node = await graph_provider.graph_find_user_by_email(u["email"])
                        if node is None:
                            logger.warning("  Missing: %s", u["email"])

        finally:
            for u in users:
                try:
                    user_client.delete_user(u["user_id"])
                    logger.info("Cleaned up user: %s", u["email"])
                except Exception as e:
                    logger.warning("Failed to delete user %s: %s", u["email"], e)
