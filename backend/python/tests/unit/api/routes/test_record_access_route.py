"""`POST /api/v1/records/access`: which cited records the caller may read (P1b C-6).

The real route and `TieredRecordAuthorizer`; only the graph is replaced. The
identity is the authenticated request's, so each test also sends an org or
user of its own to prove the body cannot choose whose access is checked.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middlewares.token_policy import AuthTokenType
from app.api.routes.record_access import MAX_RECORD_IDS, router
from tests.support.bound_org import bound_like_auth
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from httpx import Response
    from starlette.types import ASGIApp, Receive, Scope, Send

USERS: dict[str, dict[str, Any]] = {
    "viewer": {"userId": "u-viewer", "orgId": "org-1"},
    "pat-conversation": {"userId": "u-viewer", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["conversation:read"]},
    "pat-agent": {"userId": "u-viewer", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["agent:read"]},
    "pat-other": {"userId": "u-viewer", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["kb:read"]},
    "orgless": {"userId": "u-viewer"},
    "service": {"orgId": "org-1", "userId": "u-viewer", "token_type": AuthTokenType.SCOPED.value, "scopes": ["conversation:read"]},
}


class _Graph:
    def __init__(self, fail: bool = False, fail_copies: bool = False) -> None:
        self.fail = fail
        self.fail_copies = fail_copies
        self.records = {
            "kb-open": SimpleNamespace(id="kb-open", org_id="org-1"),
            "kb-closed": SimpleNamespace(id="kb-closed", org_id="org-1"),
            "copy-open": SimpleNamespace(id="copy-open", org_id="org-1"),
            "copy-closed": SimpleNamespace(id="copy-closed", org_id="org-1"),
        }
        self.copies = {("f-open", "DRIVE"): "copy-open", ("f-closed", "SHAREPOINT ONLINE"): "copy-closed"}
        self.access = InMemoryRecordAccess().user("u-viewer", "org-1")
        for rid, record in self.records.items():
            self.access.record(rid, record.org_id)
        self.access.grant("kb-open", user="u-viewer").grant("copy-open", user="u-viewer").serve(self)
        self.copy_queries: list[str] = []
        self._graph_calls: list[str] = []

    @property
    def checked_as(self) -> list[tuple[str, str]]:
        return [(user_id, org_id) for user_id, org_id, _ in self.access.asked_about]

    @property
    def calls(self) -> list[str]:
        return [*self._graph_calls, *(rid for _, _, rid in self.access.asked_about)]

    async def get_typed_records_batch(self, record_ids: list[str]) -> dict:
        self._graph_calls.append(repr(record_ids))
        if self.fail:
            raise ConnectionError("graph down")
        return {rid: self.records[rid] for rid in record_ids if rid in self.records}

    async def get_records_by_external_ids(self, org_id: str, external_ids: list[str], connector_names: list[str]) -> list[dict]:
        self.copy_queries.append(org_id)
        self._graph_calls.append(repr((org_id, external_ids, connector_names)))
        if self.fail_copies:
            raise ConnectionError("graph down")
        return [
            {"id": rid, "externalRecordId": xid, "connectorName": name}
            for (xid, name), rid in self.copies.items()
            if xid in external_ids and name in connector_names
        ]

    async def get_user_by_user_id(self, user_id: str) -> dict:
        return {"_key": f"key-{user_id}"}

    async def get_edge(self, **_: str) -> None:
        return None

    async def get_document(self, doc_id: str, collection: str) -> None:
        """No artifact document carries recorded provenance here."""


class _Auth:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            name = dict(scope["headers"]).get(b"x-test-user")
            if name is not None:
                user = dict(USERS[name.decode()])
                scope.setdefault("state", {})["user"] = user
                with bound_like_auth(user):
                    await self.app(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _client(graph: _Graph) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def graph_provider() -> _Graph:
        return graph

    app.container = SimpleNamespace(graph_provider=graph_provider)
    app.add_middleware(_Auth)
    return TestClient(app, raise_server_exceptions=False)


def _ask(client: TestClient, user: str, body: dict[str, Any]) -> Response:
    return client.post("/api/v1/records/access", json=body, headers={"x-test-user": user})


def test_answers_the_readable_subset_for_the_token_identity() -> None:
    graph = _Graph()
    response = _ask(_client(graph), "viewer", {"recordIds": ["kb-open", "kb-closed", "gone"]})
    assert response.status_code == 200
    assert response.json() == {"readable": ["kb-open"]}
    assert set(graph.checked_as) == {("u-viewer", "org-1")}


@pytest.mark.parametrize("extra", [{"orgId": "org-2"}, {"userId": "u-owner"}])
def test_the_body_cannot_name_whose_access_is_checked(extra: dict[str, str]) -> None:
    response = _ask(_client(_Graph()), "viewer", {"recordIds": ["kb-open"], **extra})
    assert response.status_code == 422


@pytest.mark.parametrize("user", ["pat-conversation", "pat-agent"])
def test_a_read_scoped_token_may_ask(user: str) -> None:
    assert _ask(_client(_Graph()), user, {"recordIds": ["kb-open"]}).status_code == 200


def test_a_token_without_a_read_scope_is_refused() -> None:
    assert _ask(_client(_Graph()), "pat-other", {"recordIds": ["kb-open"]}).status_code == 403


def test_a_service_token_is_refused_only_the_viewer_credential_counts() -> None:
    assert _ask(_client(_Graph()), "service", {"recordIds": ["kb-open"]}).status_code == 403


def test_an_identity_without_an_org_is_refused() -> None:
    assert _ask(_client(_Graph()), "orgless", {"recordIds": ["kb-open"]}).status_code == 401


def test_too_many_ids_are_refused() -> None:
    body = {"recordIds": [f"r{i}" for i in range(MAX_RECORD_IDS + 1)]}
    assert _ask(_client(_Graph()), "viewer", body).status_code == 422


def test_a_graph_failure_is_an_error_not_an_empty_answer() -> None:
    response = _ask(_client(_Graph(fail=True)), "viewer", {"recordIds": ["kb-open"]})
    assert response.status_code == 500
    assert "readable" not in response.text


def _indexed(record_id: str, kind: str = "indexed") -> dict[str, str]:
    return {"kind": kind, "recordId": record_id}


def _live(external_id: str, connector: str = "DRIVE") -> dict[str, str]:
    return {"kind": "live", "connector": connector, "connectorId": "toolset-1", "externalRecordId": external_id}


def test_typed_references_get_one_decision_each() -> None:
    graph = _Graph()
    references = [
        _indexed("kb-open"),
        _indexed("kb-closed", "attachment"),
        _indexed("gone", "generated"),
        _live("f-open"),
        _live("f-closed", "SHAREPOINT ONLINE"),
        _live("f-nowhere"),
        _live("f-open"),
    ]
    response = _ask(_client(graph), "viewer", {"references": references})
    assert response.status_code == 200
    assert response.json() == {
        "readable": ["kb-open"],
        "decisions": [
            {"source": _indexed("kb-open"), "status": "readable"},
            {"source": _indexed("kb-closed", "attachment"), "status": "denied"},
            {"source": _indexed("gone", "generated"), "status": "denied"},
            {"source": _live("f-open"), "status": "readable"},
            {"source": _live("f-closed", "SHAREPOINT ONLINE"), "status": "denied"},
            {"source": _live("f-nowhere"), "status": "unresolved"},
        ],
    }
    # Live items resolve in one query, in the token's org.
    assert graph.copy_queries == ["org-1"]


def test_an_attachment_naming_its_content_is_answered_with_it() -> None:
    """C-21b: Node keys each decision by the source it asked, virtualRecordId included."""
    graph = _Graph()
    graph.records["kb-open"] = SimpleNamespace(id="kb-open", org_id="org-1", virtual_record_id="vr-own")
    own = {"kind": "attachment", "recordId": "kb-open", "virtualRecordId": "vr-own"}
    other = {"kind": "attachment", "recordId": "kb-open", "virtualRecordId": "vr-other"}
    response = _ask(_client(graph), "viewer", {"references": [own, other]})
    assert response.status_code == 200
    assert response.json()["decisions"] == [
        {"source": own, "status": "readable"},
        {"source": other, "status": "denied"},
    ]


def test_record_ids_and_references_answer_together() -> None:
    graph = _Graph()
    response = _ask(_client(graph), "viewer", {"recordIds": ["kb-open"], "references": [_indexed("kb-closed")]})
    assert response.json() == {
        "readable": ["kb-open"],
        "decisions": [{"source": _indexed("kb-closed"), "status": "denied"}],
    }
    assert graph.copy_queries == []


def test_a_live_reference_cannot_carry_a_record_id() -> None:
    graph = _Graph()
    response = _ask(_client(graph), "viewer", {"references": [{**_live("f-open"), "recordId": "live:SENTINEL"}]})
    assert response.status_code == 422
    assert graph.calls == []


def test_a_live_temporary_id_never_reaches_the_graph() -> None:
    graph = _Graph()
    response = _ask(_client(graph), "viewer", {"references": [_live("f-open"), _indexed("kb-open")]})
    assert response.status_code == 200
    assert "live:" not in " ".join(graph.calls)


def test_a_web_page_is_not_asked_about() -> None:
    assert _ask(_client(_Graph()), "viewer", {"references": [{"kind": "web"}]}).status_code == 422


def test_too_many_references_are_refused() -> None:
    body = {"recordIds": ["r0"], "references": [_indexed(f"r{i}") for i in range(MAX_RECORD_IDS)]}
    assert _ask(_client(_Graph()), "viewer", body).status_code == 422


def test_a_failed_live_resolution_is_an_error_not_an_empty_answer() -> None:
    response = _ask(_client(_Graph(fail_copies=True)), "viewer", {"references": [_live("f-open")]})
    assert response.status_code == 500
    assert "decisions" not in response.text


def test_the_log_counts_kinds_and_never_names_a_source(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="app.api.routes.record_access"):
        _ask(_client(_Graph()), "viewer", {"references": [_indexed("kb-closed"), _live("f-nowhere"), _live("f-open")]})
    [line] = [r.getMessage() for r in caplog.records if "records.access_checked" in r.getMessage()]
    assert "requested=3" in line and "readable=1" in line and "denied=1" in line and "unresolved=1" in line
    assert "live=2" in line
    for name in ("kb-closed", "f-nowhere", "f-open"):
        assert name not in caplog.text


def _with_artifacts(graph: _Graph) -> _Graph:
    from app.models.entities import RecordType

    for rid in ("art-1", "art-2"):
        graph.records[rid] = SimpleNamespace(id=rid, org_id="org-1", record_type=RecordType.ARTIFACT)
    return graph


def test_artifacts_are_decided_by_provenance_for_the_token_identity() -> None:
    """AV-8: production provenance is recorded per version; artifacts without any are unresolved (hidden)."""
    graph = _with_artifacts(_Graph())
    response = _ask(_client(graph), "viewer", {"artifactIds": ["art-1", "art-2", "art-1", "gone"]})
    assert response.status_code == 200
    assert response.json() == {
        "readable": [],
        "artifacts": [
            {"artifactId": "art-1", "status": "unresolved"},
            {"artifactId": "art-2", "status": "unresolved"},
            {"artifactId": "gone", "status": "unresolved"},
        ],
    }
    assert graph.checked_as == []


def test_artifact_provenance_is_checked_with_the_token_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """AV-8: known provenance decides with the token's user; the body cannot name another."""
    from app.api.routes import record_access
    from app.services.artifact_registry.viewer_access import ArtifactViewerAccess

    class Provenance:
        async def sources_of(self, org_id: str, artifact_ids: list[str]) -> dict[str, frozenset[str]]:
            known = {"art-1": frozenset({"kb-open"}), "art-2": frozenset({"kb-closed"})}
            return {aid: known[aid] for aid in artifact_ids}

    monkeypatch.setattr(record_access, "artifact_viewer_access", lambda g: ArtifactViewerAccess(g, Provenance()))
    graph = _with_artifacts(_Graph())
    client = _client(graph)

    response = _ask(client, "viewer", {"artifactIds": ["art-1", "art-2"], "recordIds": ["kb-open"]})
    assert response.json() == {
        "readable": ["kb-open"],
        "artifacts": [
            {"artifactId": "art-1", "status": "readable"},
            {"artifactId": "art-2", "status": "denied"},
        ],
    }
    assert set(graph.checked_as) == {("u-viewer", "org-1")}
    assert _ask(client, "viewer", {"artifactIds": ["art-1"], "userId": "u-owner"}).status_code == 422


def test_artifacts_count_toward_the_cap() -> None:
    body = {"recordIds": ["kb-open"], "artifactIds": [f"a{i}" for i in range(MAX_RECORD_IDS)]}
    assert _ask(_client(_Graph()), "viewer", body).status_code == 422
