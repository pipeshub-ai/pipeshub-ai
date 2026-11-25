"""QA6-S1: the e2e lane's scripted SaaS answers as Jira Data Center does, and counts each write once.

The server is the real one (``deployment/e2e/fake_saas/server.py``) under Starlette's test client; the requests are
the ones the product's ``Jira Data Center`` toolset sends (``JiraDataSource``'s v2 paths, a Bearer personal token).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

SERVER = Path(__file__).resolve().parents[5] / "deployment" / "e2e" / "fake_saas" / "server.py"
TOKEN = "pat-canary-qa6-test"
CONTROL = {"X-E2E-Control": "control"}


def _load() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ["FAKE_SAAS_CONTROL_TOKEN"] = "control"
    os.environ["FAKE_SAAS_JIRA_TOKEN"] = TOKEN
    spec = importlib.util.spec_from_file_location("qa6_fake_saas_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load()


@pytest.fixture
def client() -> TestClient:
    app = server.app()
    with TestClient(app) as c:
        c.post("/__control/reset", headers=CONTROL)
        yield c


BEARER = {"Authorization": f"Bearer {TOKEN}"}
ISSUE = {"fields": {"project": {"key": "SEC"}, "summary": "Follow up", "issuetype": {"name": "Task"}}}


@pytest.mark.parametrize("auth", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}])
def test_qa6_s1_jira_refuses_a_missing_or_wrong_token(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get("/rest/api/2/issue/SEC-7", headers=auth).status_code == 401
    assert client.post("/rest/api/2/issue", json=ISSUE, headers=auth).status_code == 401
    assert client.get("/__control/effects", headers=CONTROL).json()["effects"] == []


def test_qa6_s1_jira_serves_the_private_issue_and_records_one_effect_per_create(client: TestClient) -> None:
    issue = client.get("/rest/api/2/issue/SEC-7", headers=BEARER)
    assert issue.status_code == 200 and issue.json()["fields"]["summary"].endswith("(restricted)")
    assert client.get("/rest/api/2/issue/SEC-8", headers=BEARER).status_code == 404
    created = client.post("/rest/api/2/issue", json=ISSUE, headers=BEARER)
    assert created.status_code == 201 and created.json()["key"].startswith("SEC-")
    assert client.post("/rest/api/2/issue", json={"fields": {"summary": ""}}, headers=BEARER).status_code == 400
    record = client.get("/__control/effects", headers=CONTROL).json()
    assert [e["tool"] for e in record["effects"]] == ["jira.create_issue"]
    assert TOKEN not in str(record), "the record keeps the credential's digest, never the credential"
    assert record["effects"][0]["credentialDigest"]
    assert [r["tool"] for r in record["reads"]] == ["jira.get_issue"]


def test_qa6_s1_control_routes_need_the_control_token(client: TestClient) -> None:
    assert client.get("/__control/effects").status_code == 401
    assert client.post("/__control/reset").status_code == 401
