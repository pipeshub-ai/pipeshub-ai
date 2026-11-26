"""The Local Repository connector end to end, through the push API behind ``pipeshub index``.

Registers a repository, sends its manifest and files, commits, and waits for the status to reach ``ready`` (the connector synced the mirror,
every record is indexed and the code graph is built). Then a second, ordinary user tries to reach it: the repository is invisible to them, and
the same key under their own login is a separate, empty repository.

The calls are the product's own REST API (``/api/v1/local-repos``); nothing here reads the server's disk. The first user is the suite's admin
session and the second one the disposable ``second_user``; session tokens are not scope-limited, so neither needs ``code:write`` in MCP_SCOPES.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import requests

from helper.pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.second_user import SecondUser  # type: ignore[import-not-found]

pytestmark = [pytest.mark.integration, pytest.mark.local_repository]

READY_TIMEOUT_S = 420
FILES = {
    "pkg/b.py": "def target(x):\n    return helper(x)\n\n\ndef helper(x):\n    return x\n",
    "pkg/a.py": "from pkg.b import target\n\n\ndef caller():\n    return target(1)\n",
    "README.md": "# Shop\n\nA small demo repository.\n",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Api:
    """The push API as one identity: a bearer header and a base URL."""

    def __init__(self, base_url: str, headers: dict[str, str], timeout: int = 60) -> None:
        self.base, self.headers, self.timeout = base_url.rstrip("/"), headers, timeout

    def call(self, method: str, path: str, body: Any = None) -> requests.Response:
        return requests.request(method, f"{self.base}/api/v1/local-repos{path}", headers=self.headers, json=body, timeout=self.timeout)


@pytest.fixture(scope="module")
def owner(pipeshub_client: PipeshubClient) -> Api:
    return Api(pipeshub_client.base_url, pipeshub_client.auth_headers, pipeshub_client.timeout_seconds)


@pytest.fixture(scope="module")
def other(second_user: SecondUser) -> Api:
    return Api(second_user.base_url, second_user.headers, second_user.timeout)


@pytest.fixture(scope="module")
def key(owner: Api, other: Api) -> Iterator[str]:
    repo_key = uuid.uuid4().hex
    yield repo_key
    for who in (owner, other):  # the second user's own empty repository of the same key, then the pushed one
        who.call("DELETE", f"/{repo_key}")


def _wait_ready(api: Api, repo_key: str) -> dict[str, Any]:
    deadline, last = time.time() + READY_TIMEOUT_S, {}
    while time.time() < deadline:
        resp = api.call("GET", f"/{repo_key}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["state"] in ("ready", "failed", "partial", "stuck"):
            return last
        time.sleep(3)
    pytest.fail(f"the repository was still {last.get('state')} after {READY_TIMEOUT_S} s: {last}")


class TestLocalRepositoryPush:
    def test_register_upload_commit_and_the_status_reaches_ready(self, owner: Api, key: str) -> None:
        reg = owner.call("PUT", f"/{key}", {"name": "shop"})
        assert reg.status_code == 201, reg.text
        body = reg.json()
        assert body["created"] is True and body["state"] == "empty" and body["connectorId"]
        assert body["limits"]["maxFiles"] > 0 and body["limits"]["maxReposPerUser"] > 0

        manifest = owner.call("POST", f"/{key}/manifest", {
            "complete": True, "files": [{"path": p, "sha256": _sha(t), "size": len(t.encode())} for p, t in FILES.items()]})
        assert manifest.status_code == 200, manifest.text
        assert sorted(manifest.json()["upload"]) == sorted(FILES)

        up = owner.call("PUT", f"/{key}/files", {"files": [{"path": p, "sha256": _sha(t), "content": t} for p, t in FILES.items()]})
        assert up.status_code == 200 and sorted(up.json()["stored"]) == sorted(FILES) and up.json()["rejected"] == [], up.text

        commit = owner.call("POST", f"/{key}/commit")
        assert commit.status_code == 202 and commit.json()["generation"] == 1, commit.text

        status = _wait_ready(owner, key)
        assert status["state"] == "ready", status
        assert status["syncedGeneration"] == 1 and status["files"] == len(FILES)
        assert status["indexing"]["completed"] == len(FILES) and status["indexing"]["failed"] == 0

    def test_a_second_manifest_of_the_same_files_asks_for_nothing(self, owner: Api, key: str) -> None:
        manifest = owner.call("POST", f"/{key}/manifest", {
            "complete": True, "files": [{"path": p, "sha256": _sha(t), "size": len(t.encode())} for p, t in FILES.items()]})
        assert manifest.status_code == 200 and manifest.json()["upload"] == [] and manifest.json()["unchanged"] == len(FILES)

    def test_a_path_that_leaves_the_repository_is_refused_not_stored(self, owner: Api, key: str) -> None:
        bad = ["../escape.py", "/etc/passwd", "a/../../x.py", ".git/config"]
        up = owner.call("PUT", f"/{key}/files", {"files": [{"path": p, "sha256": _sha("x"), "content": "x"} for p in bad]})
        assert up.status_code == 200 and up.json()["stored"] == []
        assert {r["reason"] for r in up.json()["rejected"]} == {"invalid_path"}

    def test_another_user_cannot_see_or_change_the_repository(self, owner: Api, other: Api, key: str) -> None:
        assert other.call("GET", f"/{key}").status_code == 404
        assert key not in [r["repoKey"] for r in other.call("GET", "").json()["repos"]]
        assert other.call("POST", f"/{key}/manifest", {"complete": True, "files": []}).status_code == 404
        assert other.call("PUT", f"/{key}/files", {"files": [{"path": "x.py", "sha256": _sha("x"), "content": "x"}]}).status_code == 404
        assert other.call("POST", f"/{key}/commit").status_code == 404
        assert other.call("DELETE", f"/{key}").status_code == 404
        # the owner's repository is untouched by all of that
        assert owner.call("GET", f"/{key}").json()["files"] == len(FILES)

    def test_the_same_key_under_another_login_is_a_separate_empty_repository(self, owner: Api, other: Api, key: str) -> None:
        mine = other.call("PUT", f"/{key}", {"name": "theirs"})
        assert mine.status_code == 201 and mine.json()["state"] == "empty" and mine.json()["files"] == 0
        assert mine.json()["connectorId"] != owner.call("GET", f"/{key}").json()["connectorId"]
        assert owner.call("GET", f"/{key}").json()["files"] == len(FILES)

    def test_delete_removes_the_repository(self, owner: Api, key: str) -> None:
        assert owner.call("DELETE", f"/{key}").status_code == 202
        assert owner.call("GET", f"/{key}").status_code == 404
