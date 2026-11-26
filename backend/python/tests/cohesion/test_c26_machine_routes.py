"""C-26 (H6, OI-N28): every route the query service's JWT middleware skips carries its own guard.

The query app skips its user-JWT middleware for every path under ``/api/v1/harness/`` (``EXCLUDE_PATHS``). A route
that lands there, by any router or mount, or by a path parameter where ``harness`` would go, is served with no
authentication unless it brings its own. These cases enumerate the real app's routes, so a new one cannot slip in.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute

from app.api.routes import harness as harness_routes
from app.harness.journal_node.credentials import ConfiguredWorkerCredential
from tests.cohesion.support import NODE_HARNESS, REPO_ROOT

PREFIX = "/api/v1/harness/"
PREFIX_SEGMENTS = PREFIX.strip("/").split("/")


@pytest.fixture(scope="module")
def query_app() -> FastAPI:
    from app.query_main import EXCLUDE_PATHS, app

    assert PREFIX in EXCLUDE_PATHS, "the JWT-exempt prefix moved: update C-26 with it"
    return app


def _segments(path: str) -> list[str]:
    return path.strip("/").split("/") if path.strip("/") else []


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _is_path_param(segment: str) -> bool:
    return _is_param(segment) and segment[1:-1].partition(":")[2] == "path"


def _can_reach_prefix(template: str, *, is_mount: bool) -> bool:
    """Whether a route (or a mount, which matches by prefix) can serve some path under ``PREFIX``."""
    segments = _segments(template)
    for index, wanted in enumerate(PREFIX_SEGMENTS):
        if index >= len(segments):
            return is_mount
        segment = segments[index]
        if _is_path_param(segment):
            return True
        if segment != wanted and not _is_param(segment):
            return False
    return len(segments) > len(PREFIX_SEGMENTS) or is_mount


def _path(route: BaseRoute) -> str:
    return str(getattr(route, "path", ""))


def _guarded(route: APIRoute) -> bool:
    return any(dep.dependency is harness_routes.require_worker_credential for dep in route.dependencies)


def test_rs1_every_route_under_the_skipped_prefix_brings_its_guard(query_app: FastAPI) -> None:
    under = [route for route in query_app.routes if _path(route).startswith(PREFIX.rstrip("/"))]
    assert under, "C-26 found no harness route: the app's routes moved"
    unguarded: list[str] = []
    for route in under:
        if isinstance(route, APIRoute):
            if not _guarded(route):
                unguarded.append(f"{sorted(route.methods)} {route.path}")
        else:
            unguarded.append(f"{type(route).__name__} {_path(route)}")
    # The query service's stage-1 tool-gateway mount (RS-5) went with stage 1 (S1R): only the claim route is left.
    assert unguarded == [], "routes served without the JWT and without the worker credential"
    assert {route.path for route in under if isinstance(route, APIRoute)} == {"/api/v1/harness/runs/{run_id}/claim"}


def test_rs1_every_route_of_the_harness_router_has_the_guard() -> None:
    routes = [route for route in harness_routes.router.routes if isinstance(route, APIRoute)]
    assert routes and all(_guarded(route) for route in routes)


def test_rs2_no_other_route_can_match_a_path_under_the_skipped_prefix(query_app: FastAPI) -> None:
    reaching = [
        f"{type(route).__name__} {_path(route)}"
        for route in query_app.routes
        if isinstance(route, Route | WebSocketRoute | Mount)
        and not _path(route).startswith(PREFIX.rstrip("/"))
        and _can_reach_prefix(_path(route), is_mount=isinstance(route, Mount))
    ]
    assert reaching == [], "these would serve paths under /api/v1/harness/ without the JWT middleware"


@pytest.mark.parametrize(
    ("template", "is_mount", "reaches"),
    [
        ("/api/v1/harness/runs/{run_id}/claim", False, True),
        ("/api/v1/{name}/runs", False, True),
        ("/api/{rest:path}", False, True),
        ("/api/v1", True, True),
        ("/api/v1/harness", False, False),
        ("/api/v1/agent/{agent_id}", False, False),
        ("/api/v1/{name}", False, False),
        ("/health", False, False),
        ("/api/v1/agent", True, False),
    ],
)
def test_rs2_the_matcher_itself(template: str, is_mount: bool, reaches: bool) -> None:
    assert _can_reach_prefix(template, is_mount=is_mount) is reaches


class _Config:
    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document

    async def get_config(self, key: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return self._document


class _Inline:
    def __init__(self) -> None:
        self.credentials = ConfiguredWorkerCredential(_Config({"current": "phw_node"}))  # type: ignore[arg-type]
        self.hinted: list[tuple[str, str]] = []

    async def hint(self, org_id: str, run_id: str) -> bool:
        self.hinted.append((org_id, run_id))
        return True


def _router_app(inline: _Inline) -> FastAPI:
    app = FastAPI()
    app.include_router(harness_routes.router, prefix="/api/v1/harness")
    app.state.harness_inline = inline
    return app


@pytest.mark.parametrize("authorization", [None, "Bearer phw_wrong", "Basic phw_node", "Bearer "])
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/harness/runs/run-1/claim", {"orgId": "org-1"}),
        ("/api/v1/harness/runs/run-1/claim", {"orgId": "not an id!"}),
        ("/api/v1/harness/runs/run-1/claim", {}),
        ("/api/v1/harness/runs/run%20one/claim", {"orgId": "org-1"}),
    ],
)
async def test_rs3_the_credential_is_checked_before_the_body(
    authorization: str | None, path: str, body: dict[str, str]
) -> None:
    inline = _Inline()
    headers = {} if authorization is None else {"Authorization": authorization}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_router_app(inline)), base_url="http://query") as client:
        response = await client.post(path, json=body, headers=headers)
    assert response.status_code == 401
    assert inline.hinted == []


async def test_rs4_with_the_credential_the_hint_claims_and_a_bad_body_is_422() -> None:
    inline = _Inline()
    headers = {"Authorization": "Bearer phw_node"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_router_app(inline)), base_url="http://query") as client:
        accepted = await client.post("/api/v1/harness/runs/run-1/claim", json={"orgId": "org-1"}, headers=headers)
        malformed = await client.post("/api/v1/harness/runs/run-1/claim", json={"orgId": "not an id!"}, headers=headers)
    assert (accepted.status_code, accepted.json()) == (202, {"claimed": True})
    assert malformed.status_code == 422
    assert inline.hinted == [("org-1", "run-1")]


def test_rs12_python_callers_default_to_nodes_internal_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.inline import DEFAULT_STATE_URL, node_state_url

    monkeypatch.delenv("AGENT_HARNESS_STATE_URL", raising=False)
    assert node_state_url() == DEFAULT_STATE_URL
    listener = (NODE_HARNESS / "transport" / "internal-listener.ts").read_text(encoding="utf-8")
    port = re.search(r"DEFAULT_INTERNAL_PORT = (\d+);", listener)
    assert port is not None and DEFAULT_STATE_URL == f"http://localhost:{port.group(1)}"
    template = (REPO_ROOT / "backend" / "env.template").read_text(encoding="utf-8")
    assert f"AGENT_HARNESS_STATE_URL={DEFAULT_STATE_URL}\n" in template


def test_rs12_rs13_helm_points_every_caller_at_the_internal_port_and_publishes_it_nowhere() -> None:
    chart = REPO_ROOT / "deployment" / "helm" / "pipeshub-ai"
    deployment = (chart / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    env = dict(re.findall(r"- name: (AGENT_HARNESS_(?:STATE_URL|INTERNAL_PORT|INTERNAL_HOST))\n\s+value: (.+)", deployment))
    assert env == {
        "AGENT_HARNESS_INTERNAL_PORT": "{{ .Values.config.agentHarnessInternalPort | quote }}",
        "AGENT_HARNESS_INTERNAL_HOST": '"127.0.0.1"',
        "AGENT_HARNESS_STATE_URL": '{{ printf "http://localhost:%v" .Values.config.agentHarnessInternalPort | quote }}',
    }
    for exposed in ("service.yaml", "ingress.yaml", "networkpolicy.yaml"):
        text = (chart / "templates" / exposed).read_text(encoding="utf-8")
        assert "agentHarnessInternalPort" not in text and "3002" not in text, exposed
