"""A logged-in non-admin identity, for tests that ask "can *this* user see it?".

Every other suite runs as the org admin, who can reach everything, so no test
could distinguish "permission enforced" from "permission ignored". These tests
need a second person.

Deliberately *not* built on ``PIPESHUB_TEST_NON_ADMIN_EMAIL`` /
``PIPESHUB_TEST_NON_ADMIN_PASSWORD``: those are commented out in every env
template, so the one suite that reads them skips on every run. A fixture that
silently does nothing is worse than no fixture.

Deliberately *not* built on ``second_user_auth.second_pipeshub_client`` either:
that routes through an OAuth app whose scope list is fixed at creation, and
search is not in it. Logging in returns the same token the UI uses, which
carries the user's real permissions -- which is precisely what is under test.
"""

from __future__ import annotations

import datetime
import logging
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import bcrypt
import pytest
import requests
from pymongo import MongoClient

# Path setup precedes the local imports below: `config` and `pipeshub_client`
# live in this directory, and importing this module should not depend on a
# caller having configured sys.path first.
_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "helper"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from config import MONGO_DB_NAME, MONGO_URI, TEST_USER_PASSWORD  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

logger = logging.getLogger("second-user")

MONGO_CONTAINER = os.getenv("PIPESHUB_MONGO_CONTAINER", "mongodb")
GRAPH_USER_POLL_TIMEOUT_SEC = 60.0
GRAPH_USER_POLL_INTERVAL_SEC = 2.0


@dataclass
class SecondUser:
    """A real, logged-in, non-admin user.

    ``graph_id`` is the id the permission APIs expect, and is not the Mongo
    ``user_id`` that created the account -- granting against the wrong one
    silently grants nothing.
    """

    user_id: str
    graph_id: str
    email: str
    token: str
    base_url: str
    timeout: int

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def search(self, query: str, kb_id: str, limit: int = 5) -> requests.Response:
        """Search as this user, scoped to one knowledge base."""
        return requests.post(
            f"{self.base_url}/api/v1/search",
            headers=self.headers,
            json={"query": query, "filters": {"kb": [kb_id]}, "limit": limit},
            timeout=self.timeout,
        )


def search_result_count(resp: requests.Response) -> int:
    """Number of hits in a search response, tolerating either envelope."""
    body = resp.json()
    search_response = body.get("searchResponse") or body
    return len(search_response.get("searchResults") or [])


# A KB-filtered search by a user with no access to that KB answers 404 (the KB
# is not resolvable for them) rather than an empty 200 -- verified against a
# live deployment. 403 is accepted too so an authorization change upstream
# reads as still-denied rather than as a failure of these tests.
NO_ACCESS_STATUSES = (403, 404)


def describe_search(resp: requests.Response) -> str:
    if resp.status_code == 200:
        return f"http=200 hits={search_result_count(resp)}"
    return f"http={resp.status_code} body={resp.text[:200]}"


def has_no_access(resp: requests.Response) -> bool:
    """True when this search proves the user cannot reach the KB's records.

    Either the KB did not resolve for them at all, or it did and returned
    nothing.
    """
    if resp.status_code in NO_ACCESS_STATUSES:
        return True
    return resp.status_code == 200 and search_result_count(resp) == 0


def _create_user(client: PipeshubClient, email: str) -> dict[str, Any]:
    """`POST /users` creates the account with no credentials and sends no mail,
    so this works on a stack with no SMTP -- unlike the invite route."""
    resp = requests.post(
        f"{client.base_url}/api/v1/users",
        headers=client._headers(),
        json={"fullName": f"IT Second User {uuid.uuid4().hex[:8]}", "email": email},
        timeout=client.timeout_seconds,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"createUser failed for {email}: HTTP {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _credential_document(org_id: str, user_id: str, hashed: str) -> dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        # Both ids are declared String in userCredentials.schema.ts.
        "userId": str(user_id),
        "orgId": str(org_id),
        "hashedPassword": hashed,
        "ipAddress": "127.0.0.1",
        "wrongCredentialCount": 0,
        "isBlocked": False,
        "forceNewPasswordGeneration": False,
        "isDeleted": False,
        "createdAt": now,
        "updatedAt": now,
    }


def _seed_password_direct(org_id: str, user_id: str, hashed: str) -> None:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        collection = client[MONGO_DB_NAME].userCredentials
        # delete_many first: a re-run against a database that still holds a row
        # for this user would leave duplicates, and which one login picks up is
        # not defined.
        collection.delete_many({"userId": str(user_id), "orgId": str(org_id)})
        collection.insert_one(_credential_document(org_id, user_id, hashed))
    finally:
        client.close()


def _seed_password_via_docker(org_id: str, user_id: str, hashed: str) -> None:
    """Fallback for stacks that do not publish 27017.

    The integration compose publishes Mongo, so the direct client is the normal
    path. The shipped compose does not, and running these tests against an
    ordinary deployment is worth supporting -- same approach as
    `loadtest/seed_users.py`.
    """
    script = (
        f'db = db.getSiblingDB("{MONGO_DB_NAME}");'
        f'db.userCredentials.deleteMany({{userId:"{user_id}",orgId:"{org_id}"}});'
        f'db.userCredentials.insertOne({{userId:"{user_id}",orgId:"{org_id}",'
        f'hashedPassword:"{hashed}",ipAddress:"127.0.0.1",wrongCredentialCount:0,'
        f"isBlocked:false,forceNewPasswordGeneration:false,isDeleted:false,"
        f"createdAt:new Date(),updatedAt:new Date()}});"
    )
    uri = MONGO_URI.replace("/?", f"/{MONGO_DB_NAME}?")
    errors = []
    for prefix in (["docker"], ["sudo", "-n", "docker"]):
        try:
            result = subprocess.run(
                [*prefix, "exec", "-i", MONGO_CONTAINER, "mongosh", "--quiet",
                 uri, "--eval", script],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                return
            errors.append(f"{' '.join(prefix)}: {(result.stderr or result.stdout)[:200]}")
        except Exception as e:  # noqa: BLE001 - try the next invocation
            errors.append(f"{' '.join(prefix)}: {e}")
    raise RuntimeError("mongosh via docker exec failed:\n  " + "\n  ".join(errors))


def _seed_password(org_id: str, user_id: str) -> None:
    """Give the new account a password so it can log in.

    `POST /users` creates an account with no credentials, and the invite route
    needs SMTP, so the credential row is written directly.
    """
    hashed = bcrypt.hashpw(TEST_USER_PASSWORD.encode(), bcrypt.gensalt()).decode()
    try:
        _seed_password_direct(org_id, user_id, hashed)
    except Exception as direct_error:  # noqa: BLE001 - fall back, then report both
        logger.info("Direct Mongo seed failed (%s); trying docker exec", direct_error)
        try:
            _seed_password_via_docker(org_id, user_id, hashed)
        except Exception as docker_error:
            raise RuntimeError(
                f"Could not seed a password for {user_id}.\n"
                f"  direct: {direct_error}\n"
                f"  docker: {docker_error}"
            ) from docker_error


def _delete_credentials(org_id: str, user_id: str) -> None:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
        try:
            client[MONGO_DB_NAME].userCredentials.delete_many(
                {"userId": str(user_id), "orgId": str(org_id)}
            )
        finally:
            client.close()
    except Exception:  # noqa: BLE001 - teardown must not fail the run
        logger.warning("Could not clean up credentials for user %s", user_id)


def _login(base_url: str, email: str, timeout: int) -> str:
    init_resp = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth",
        json={"email": email},
        timeout=timeout,
    )
    if init_resp.status_code >= 400:
        raise RuntimeError(
            f"initAuth failed for {email}: HTTP {init_resp.status_code}: {init_resp.text[:200]}"
        )
    session_token = init_resp.headers.get("x-session-token")
    if not session_token:
        raise RuntimeError("initAuth returned no x-session-token")

    auth_resp = requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token},
        json={
            "method": "password",
            "credentials": {"password": TEST_USER_PASSWORD},
            "email": email,
        },
        timeout=timeout,
    )
    if auth_resp.status_code >= 400:
        raise RuntimeError(
            f"authenticate failed for {email}: "
            f"HTTP {auth_resp.status_code}: {auth_resp.text[:200]}"
        )
    return str(auth_resp.json()["accessToken"])


def _wait_for_graph_user(client: PipeshubClient, email: str) -> dict[str, Any]:
    """The account reaches the graph over Kafka, not in the create call.

    A permission granted before it lands comes back ``grantedCount: 0`` and the
    test then measures nothing.
    """
    # Imported here, not at module scope: this module reaches
    # helper.graph_provider, which needs the backend `app` package on sys.path,
    # and only conftest puts it there.
    from messaging.test_e2e_record_pipeline import poll_until  # noqa: PLC0415

    email_lower = email.lower()
    search_term = email.split("@", 1)[0]

    def _found() -> dict[str, Any] | None:
        resp = requests.get(
            f"{client.base_url}/api/v1/users/graph/list",
            headers=client._headers(),
            params={"search": search_term, "limit": "50"},
            timeout=client.timeout_seconds,
        )
        if resp.status_code != 200:
            return None
        for user in resp.json().get("users") or []:
            if str(user.get("email") or "").lower() == email_lower:
                return user
        return None

    return poll_until(
        _found,
        timeout=GRAPH_USER_POLL_TIMEOUT_SEC,
        interval=GRAPH_USER_POLL_INTERVAL_SEC,
        description=f"graph user for {email!r}",
    )


def create_second_user(client: PipeshubClient) -> SecondUser:
    """Create, credential and log in a non-admin user. Caller deletes it."""
    email = f"it-second-{uuid.uuid4().hex[:10]}@test-pipeshub.com"
    user = _create_user(client, email)
    user_id = str(user.get("_id") or user.get("id") or "")
    org_id = str(user.get("orgId") or client.org_id or "")
    if not user_id or not org_id:
        raise RuntimeError(f"createUser response missing _id/orgId: {sorted(user)}")

    _seed_password(org_id, user_id)
    graph_user = _wait_for_graph_user(client, email)
    graph_id = str(graph_user.get("id") or "")
    if not graph_id:
        raise RuntimeError(f"graph user for {email} has no id: {graph_user}")

    token = _login(client.base_url, email, client.timeout_seconds)
    logger.info("Second user ready: %s (graph id %s)", email, graph_id)
    return SecondUser(
        user_id=user_id,
        graph_id=graph_id,
        email=email,
        token=token,
        base_url=client.base_url,
        timeout=client.timeout_seconds,
    )


def delete_second_user(client: PipeshubClient, user: SecondUser) -> None:
    _delete_credentials(client.org_id, user.user_id)
    try:
        requests.delete(
            f"{client.base_url}/api/v1/users/{user.user_id}",
            headers=client._headers(),
            timeout=client.timeout_seconds,
        )
    except Exception:  # noqa: BLE001 - teardown must not fail the run
        logger.warning("Could not delete test user %s", user.user_id)


@pytest.fixture(scope="session")
def second_user(pipeshub_client: PipeshubClient) -> Iterator[SecondUser]:
    """Session-scoped: creating a user costs a Kafka round trip to the graph."""
    client = pipeshub_client
    client._ensure_access_token()
    user = create_second_user(client)
    try:
        yield user
    finally:
        delete_second_user(client, user)
