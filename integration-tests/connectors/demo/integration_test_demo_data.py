"""Acceptance test for the Acme Corp demo data, through the real connector path.

Creates the Demo connector and the two sample employees on the running
instance, waits for the records to index, then asks each golden question a
few times as Alice and as Bob and scores the citations with the same rules
the standalone harness uses (``app.connectors.sources.demo.harness``).

Pass criteria are the design's, scaled for CI: every question must pass at
least ``MIN_PASS`` of ``RUNS`` per persona, and the restricted pricing
question must pass every run — Bob always gets the document, Alice never
does. One leak fails the test.

Needs an LLM configured on the instance (the integration workflow does that
before the suite runs). ``DEMO_PERSONA_PASSWORD`` may be set to reuse
existing persona accounts; otherwise a throwaway password is generated.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
import requests
import yaml

from app.connectors.sources.demo.harness import kb_harness  # type: ignore[import-not-found]
from app.connectors.sources.demo.harness.kb_harness import (  # type: ignore[import-not-found]
    ask,
    build_name_index,
    cited_fixture_ids,
    score,
)
from helper.pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

# The harness module pulls in only httpx and yaml; the connector module would
# drag the whole connector framework into the test process.
FIXTURE_PATH = Path(kb_harness.__file__).resolve().parent.parent / "fixture" / "acme-corp.yaml"

pytestmark = [pytest.mark.integration, pytest.mark.demo, pytest.mark.slow]

RUNS = int(os.environ.get("DEMO_ACCEPTANCE_RUNS", "3"))
MIN_PASS = int(os.environ.get("DEMO_ACCEPTANCE_MIN_PASS", "2"))
INDEX_TIMEOUT_S = 420
CONNECTOR_NAME = "Acme Corp demo data: GitHub, Jira, Slack, Google Drive and ServiceNow"
PERSONAS = ("alice", "bob")


def _fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _api(client: PipeshubClient, method: str, path: str, **kwargs: Any) -> requests.Response:
    return requests.request(
        method,
        f"{client.base_url}{path}",
        headers={**client._headers(), "Content-Type": "application/json"},
        timeout=client.timeout_seconds,
        **kwargs,
    )


def _search_names(base_url: str, jwt: str, query: str) -> list[str]:
    resp = requests.post(
        f"{base_url}/api/v1/search",
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        json={"query": query, "limit": 5},
        timeout=120,
    )
    if resp.status_code >= 400:
        return []
    hits = (resp.json().get("searchResponse") or {}).get("searchResults") or []
    return [str((h.get("metadata") or {}).get("recordName") or "") for h in hits]


@pytest.fixture(scope="module")
def demo_password() -> str:
    return os.environ.get("DEMO_PERSONA_PASSWORD") or f"Demo-{uuid.uuid4().hex[:10]}-A1!"


@pytest.fixture(scope="module")
def personas(pipeshub_client: PipeshubClient, demo_password: str) -> Iterator[dict[str, str]]:
    """Alice and Bob as real org users, created with a starting password.

    Yields persona id -> session JWT. Accounts that already exist (a previous
    run with DEMO_PERSONA_PASSWORD set) are reused.
    """
    fx = _fixture()
    created: list[str] = []
    tokens: dict[str, str] = {}
    for p in fx["people"]:
        if p["id"] not in PERSONAS:
            continue
        resp = _api(
            pipeshub_client, "POST", "/api/v1/users/",
            json={"fullName": p["name"], "email": p["email"], "role": "member", "password": demo_password},
        )
        if resp.status_code < 400:
            created.append(str(resp.json().get("_id") or resp.json().get("id") or ""))
        try:
            tokens[p["id"]] = _login_with_password(
                pipeshub_client.base_url, p["email"], demo_password, pipeshub_client.timeout_seconds
            )
        except requests.HTTPError as exc:
            # An account left over from an earlier run with a different password.
            pytest.fail(
                f"persona {p['email']} exists but cannot sign in with the supplied password "
                f"(create: HTTP {resp.status_code} {resp.text[:120]}; login: {exc}). "
                "Set DEMO_PERSONA_PASSWORD to its password or delete the account."
            )
    yield tokens
    for user_id in created:
        if user_id:
            _api(pipeshub_client, "DELETE", f"/api/v1/users/{user_id}")


def _login_with_password(base_url: str, email: str, password: str, timeout: int) -> str:
    init = requests.post(f"{base_url}/api/v1/userAccount/initAuth", json={"email": email}, timeout=timeout)
    init.raise_for_status()
    session = init.headers.get("x-session-token")
    assert session, "initAuth returned no x-session-token"
    auth = requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session},
        json={"method": "password", "credentials": {"password": password}, "email": email},
        timeout=timeout,
    )
    auth.raise_for_status()
    return str(auth.json()["accessToken"])


@pytest.fixture(scope="module")
def demo_connector(pipeshub_client: PipeshubClient, personas: dict[str, str]) -> Iterator[str]:
    """The Demo connector, created after the personas so their group memberships attach, synced and indexed."""
    registry = _api(pipeshub_client, "GET", "/api/v1/connectors/registry").json()
    names = {str(c.get("name")) for c in registry.get("connectors", []) if isinstance(c, dict)}
    if "Demo" not in names:
        pytest.skip("Demo connector is not registered on this instance")

    resp = _api(
        pipeshub_client, "POST", "/api/v1/connectors/",
        json={
            "connectorType": "Demo",
            # Unique per run so a leftover instance from an aborted run cannot collide.
            "instanceName": f"{CONNECTOR_NAME} (acceptance {uuid.uuid4().hex[:6]})",
            "scope": "team",
            "authType": "NONE",
        },
    )
    assert resp.status_code < 400, f"create failed: {resp.status_code} {resp.text[:200]}"
    connector_id = resp.json()["connector"]["connectorId"]
    try:
        resp = _api(pipeshub_client, "PUT", f"/api/v1/connectors/{connector_id}/config", json={"auth": {}, "sync": {"strategy": "MANUAL"}})
        assert resp.status_code < 400, f"config failed: {resp.status_code} {resp.text[:200]}"
        resp = _api(pipeshub_client, "POST", f"/api/v1/connectors/{connector_id}/toggle", json={"type": "sync", "fullSync": True})
        assert resp.status_code < 400, f"toggle failed: {resp.status_code} {resp.text[:200]}"

        # Indexed when the two documents the questions hinge on are searchable
        # by the people allowed to see them.
        deadline = time.time() + INDEX_TIMEOUT_S
        while time.time() < deadline:
            handbook = any("on-call handbook" in n.lower() for n in _search_names(pipeshub_client.base_url, personas["alice"], "policy on being on-call over a public holiday"))
            pricing = any("pricing strategy" in n.lower() for n in _search_names(pipeshub_client.base_url, personas["bob"], "enterprise pricing strategy 2026"))
            if handbook and pricing:
                break
            time.sleep(10)
        else:
            pytest.fail(f"demo records did not become searchable within {INDEX_TIMEOUT_S}s")
        yield connector_id
    finally:
        _api(pipeshub_client, "POST", f"/api/v1/connectors/{connector_id}/toggle", json={"type": "sync"})
        _api(pipeshub_client, "DELETE", f"/api/v1/connectors/{connector_id}")


@pytest.mark.parametrize("persona", PERSONAS)
def test_golden_questions_pass_for_persona(
    pipeshub_client: PipeshubClient, demo_connector: str, personas: dict[str, str], persona: str
) -> None:
    fx = _fixture()
    name_to_id, thread_of = build_name_index(fx)
    jwt = personas[persona]
    failures: list[str] = []
    for q in fx["questions"]:
        expect = q["personas"][persona]
        passes = 0
        verdicts: list[str] = []
        for _ in range(RUNS):
            answer, cited_names = ask(pipeshub_client.base_url, jwt, q["ask"])
            ok, verdict = score(q, expect, cited_fixture_ids(cited_names, name_to_id, thread_of), answer)
            passes += int(ok)
            verdicts.append(verdict)
        # A leak of restricted material fails the whole demo: those questions must pass every run.
        need = RUNS if q.get("restricted") else MIN_PASS
        if passes < need:
            failures.append(f"{q['id']} [{persona}]: {passes}/{RUNS} (need {need}) — {verdicts}")
    assert not failures, "\n".join(failures)


def test_restricted_document_never_leaks_to_alice(
    pipeshub_client: PipeshubClient, demo_connector: str, personas: dict[str, str]
) -> None:
    """Belt and braces on top of Q5: the pricing records are not even retrievable for Alice."""
    fx = _fixture()
    restricted_titles = {r["title"] for r in fx["records"] if r["id"] in {"drive-pricing-2026"}} | {
        t["title"] for t in fx.get("threads", []) if t["id"] == "slack-pricing-0402"
    }
    names = _search_names(pipeshub_client.base_url, personas["alice"], "enterprise pricing strategy 2026 platform fee tiers")
    leaked = restricted_titles & set(names)
    assert not leaked, f"restricted records visible to Alice: {leaked}"
