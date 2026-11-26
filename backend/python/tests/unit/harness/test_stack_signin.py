"""The lanes' password sign-in ends with an org access token in either edition (SIGNIN-SWITCH-1)."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest

E2E = Path(__file__).resolve().parents[5] / "integration-tests" / "harness" / "e2e"
sys.path.insert(0, str(E2E))

from stack_signin import org_access_token  # noqa: E402

ORG = "64b000000000000000000a01"


def token(claims: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.c2ln"


ACCESS = token({"userId": "u1", "orgId": ORG, "role": "admin"})
WORKSPACE = token({"email": "admin@e2e.example", "scopes": ["email:verified"], "hashProof": ["p"]})


class Server:
    def __init__(self, workspaces: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, Any, str]] = []
        self.workspaces = workspaces or []

    def __call__(self, method: str, path: str, body: Any, bearer: str) -> Any:  # noqa: ANN401
        self.calls.append((method, path, body, bearer))
        if path.startswith("/api/v1/workspaces"):
            return {"workspaces": self.workspaces}
        if path == "/api/v1/auth/token/switch":
            return {"accessToken": ACCESS, "refreshToken": "r"}
        raise AssertionError(path)


def test_community_answer_is_the_org_token() -> None:
    server = Server()
    assert org_access_token({"accessToken": ACCESS, "refreshToken": "r"}, server) == ACCESS
    assert server.calls == []


def test_enterprise_answer_switches_to_the_org_it_names() -> None:
    server = Server()
    assert org_access_token({"accessToken": WORKSPACE, "orgId": ORG}, server) == ACCESS
    assert server.calls == [("POST", "/api/v1/auth/token/switch", {"orgId": ORG}, WORKSPACE)]


def test_enterprise_answer_without_an_org_switches_to_the_only_workspace() -> None:
    server = Server([{"id": ORG, "isMember": True}, {"id": "other", "isMember": False}])
    assert org_access_token({"accessToken": WORKSPACE}, server) == ACCESS
    assert [c[1] for c in server.calls] == ["/api/v1/workspaces?isMember", "/api/v1/auth/token/switch"]
    assert server.calls[1][2] == {"orgId": ORG}


def test_two_workspaces_and_none_named_is_refused() -> None:
    server = Server([{"id": ORG, "isMember": True}, {"id": "b", "isMember": True}])
    with pytest.raises(RuntimeError, match="2 workspaces"):
        org_access_token({"accessToken": WORKSPACE}, server)


def test_an_answer_without_a_token_is_refused() -> None:
    with pytest.raises(RuntimeError, match="without a token"):
        org_access_token({"status": "success", "nextStep": 1}, Server())
