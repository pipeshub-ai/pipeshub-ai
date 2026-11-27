"""CPD-8 IN-2 (Python half): ``NodeBindingWriter`` over WP-3's ``NodeServiceClient`` speaks Node's internal binding routes."""

from __future__ import annotations

import json

import httpx
import pytest

from app.harness.credentials.binding_writer import (
    BindingConflict,
    BindingNotImported,
    BindingWriteFailed,
    ImportRequest,
    Material,
    NodeBindingWriter,
)
from app.harness.journal_node.http import NodeServiceClient
from app.utils import error_class

BINDING = {"id": "bnd_01J9ZQ5Y8K3M4N5P6Q7R8S0001", "orgId": "org-1", "status": "active", "revision": 2,
           "secret": {"path": "/services/controlPlane/credentials/org-1/ts-1/user/u1", "version": 1, "versionDigest": "sha256:" + "a" * 64}}


def writer(*replies: httpx.Response) -> tuple[NodeBindingWriter, list[httpx.Request], list[tuple[str | None, str | None]]]:
    seen: list[httpx.Request] = []
    minted: list[tuple[str | None, str | None]] = []
    queue = list(replies)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    client = NodeServiceClient(
        base_url="http://node.test", timeout_s=5, http_client=httpx.AsyncClient(base_url="http://node.test", transport=httpx.MockTransport(handle))
    )

    async def token(org: str | None, user: str | None) -> str:
        minted.append((org, user))
        return f"tok-{org}"

    return NodeBindingWriter(client, token), seen, minted


async def test_import_posts_the_record_with_the_orgs_token_and_reads_the_outcome() -> None:
    w, seen, minted = writer(httpx.Response(200, json={"binding": BINDING, "outcome": "created"}))
    request = ImportRequest("ts-1", "user:u1", "oauth", "toolset", "slack", Material("record", {"a": 1}, 5), "/services/toolsets/ts-1/u1", "sha256:" + "b" * 64)
    imported = await w.import_record("org-1", request, user_id="u1")
    assert imported.outcome == "created" and imported.binding is not None and imported.binding.version == 1
    assert seen[0].url.path == "/api/v1/control-plane/internal/bindings/import"
    assert seen[0].headers["authorization"] == "Bearer tok-org-1" and minted == [("org-1", "u1")]
    body = json.loads(seen[0].content)
    assert "orgId" not in body and body["source"]["digest"] == "sha256:" + "b" * 64 and body["observed"] is None


async def test_due_has_no_org_and_refreshed_conflicts_map_to_their_errors() -> None:
    w, seen, minted = writer(
        httpx.Response(200, json={"bindings": [{**BINDING, "installation": "ts-1", "owner": "user:u1", "mode": "oauth", "expiresAt": 9, "path": None}]}),
        httpx.Response(409, json={"error": {"code": "CP_REVISION_CONFLICT"}}),
        httpx.Response(409, json={"error": {"code": "CP_BINDING_NOT_IMPORTED"}}),
        httpx.Response(503, json={}),
    )
    [due] = await w.due(100, 10, "replica-a")
    assert (due.id, due.expires_at, due.path) == (BINDING["id"], 9, None)
    assert seen[0].url.params["before"] == "100" and seen[0].url.params["holder"] == "replica-a" and due.claimed
    assert minted[0] == (None, None)
    with pytest.raises(BindingConflict):
        _ = await w.refreshed("org-1", BINDING["id"], 2, Material("record", {"x": 1}))
    with pytest.raises(BindingNotImported):
        _ = await w.revert_import("org-1", "ts-1", "user:u1")
    with pytest.raises(BindingWriteFailed):
        _ = await w.refresh_failed("org-1", BINDING["id"], 2, "invalid_grant", permanent=True)


async def test_revoke_installation_posts_the_installation_with_the_orgs_token_and_reads_every_row() -> None:
    second = {**BINDING, "id": "bnd_01J9ZQ5Y8K3M4N5P6Q7R8S0002", "status": "revoked", "revision": 3}
    w, seen, minted = writer(httpx.Response(200, json={"bindings": [{**BINDING, "status": "revoked"}, second]}))
    rows = await w.revoke_installation("org-1", "inst-1", user_id="admin-1")
    assert [(r.id, r.status, r.org_id) for r in rows] == [(BINDING["id"], "revoked", "org-1"), (second["id"], "revoked", "org-1")]
    assert seen[0].method == "POST" and seen[0].url.path == "/api/v1/control-plane/internal/bindings/revoke-installation"
    assert json.loads(seen[0].content) == {"installation": "inst-1"} and minted == [("org-1", "admin-1")]


@pytest.mark.parametrize(
    ("status", "error_type", "retry"),
    [(503, "unavailable", True), (504, "timeout", True), (500, "upstream_5xx", False)],
)
async def test_a_node_5xx_carries_its_status(status: int, error_type: str, retry: bool) -> None:
    w, _, _ = writer(httpx.Response(status, json={}))
    with pytest.raises(BindingWriteFailed) as failed:
        _ = await w.revoke_installation("org-1", "inst-1")
    assert failed.value.status_code == status
    assert (error_class.classify_error(failed.value), error_class.retry_later(failed.value)) == (error_type, retry)


async def test_a_node_4xx_to_our_own_call_stays_an_internal_error() -> None:
    w, _, _ = writer(httpx.Response(401, json={}))
    with pytest.raises(BindingWriteFailed) as rejected:
        _ = await w.revoke_installation("org-1", "inst-1")
    assert rejected.value.status_code is None and error_class.classify_error(rejected.value) == "internal"
    assert not error_class.retry_later(rejected.value)
