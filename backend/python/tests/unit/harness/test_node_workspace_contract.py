"""PY-1..PY-3: the Node-backed workspace store and key provider pass P3.2's contracts.

The Node side is real (internal router, workspace controller, WorkspaceService,
SessionWorkspaceKeys, attempt-token authority, in-memory stores) behind the
journal test server; skipped where the Node workspace has no ``node_modules``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
import pytest

from app.harness.journal_node import NodeJournal
from app.harness.journal_node.client import NodeClaim
from app.harness.workspace import node_store
from app.harness.workspace.node_store import (
    NodeKeyProvider,
    NodeWorkspaceStore,
    NodeWorkspaceWriters,
)
from harness_core.contracts import dump_contract
from harness_core.journal.children import ChildRequest
from harness_core.journal.types import AttemptToken
from harness_core.testing.contracts.workspace import (
    KeyProviderContract,
    WorkspaceStoreContract,
    file,
)
from harness_core.testing.contracts.workspace_forks import (
    ChildWorkspacesContract,
    WorkspaceWritersContract,
)
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.workspace import (
    EXECUTABLE_MODE,
    FILE_MODE,
    SYMLINK_MODE,
    ChangeSet,
    InvalidChangeSet,
    ObjectScope,
    WorkspaceAccessDenied,
    WorkspaceError,
    WorkspaceFile,
    WorkspaceKeyDestroyed,
    WorkspaceRequestRefused,
)
from harness_core.workspace.forks import StoreChildWorkspaces
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.sealing import SealedObjectStore
from tests.unit.harness.test_node_journal_contract import (
    LEASE_MS,
    _Credential,
    node_server,
)

if TYPE_CHECKING:
    from tests.unit.harness.test_node_journal_contract import _NodeServer

__all__ = ["node_server"]

ORG = "org-contract"
OTHER_ORG = "org-other"
SESSION = "ses-ws"


class _Attempt:
    """One claimed run's routes: a store and a key provider bound to its lease."""

    def __init__(self, journal: NodeJournal, token: AttemptToken, org_id: str) -> None:
        route = journal.attempt_route(token)
        self.token = token
        self.store = NodeWorkspaceStore(route)
        self.keys = NodeKeyProvider(route)
        self.scope = ObjectScope(org_id)


class _World:
    def __init__(self, server: _NodeServer) -> None:
        self.control = server.control
        _ = self.control.post("/reset").raise_for_status()
        self.journal = NodeJournal(
            base_url=server.base_url, credentials=_Credential(server.credential), lease_ms=LEASE_MS
        )

    async def attempt(self, run_id: str, org_id: str, session_id: str = SESSION) -> _Attempt:
        _ = self.control.post(
            "/admit",
            json={
                "runId": run_id,
                "sessionId": session_id,
                "inputText": "hello",
                "spec": dump_contract(minimal_run_spec(max_children=2)),
                "orgId": org_id,
            },
        ).raise_for_status()
        claim = await self.journal.claim(run_id, worker_id="w1", org_id=org_id)
        assert claim is not None
        token = AttemptToken(claim.run_id, claim.attempt_id, claim.fence)
        return _Attempt(self.journal, token, org_id)

    async def child(self, parent: _Attempt, invocation_id: str) -> _Attempt:
        """A second run of the parent's session (one root run per session: a child)."""
        admitted = await self.journal.spawn(parent.token, ChildRequest(invocation_id, goal="write"))
        claim = await self.journal.claim(
            admitted.child_run_id, worker_id="w2", org_id=parent.scope.tenant_id
        )
        assert claim is not None
        token = AttemptToken(claim.run_id, claim.attempt_id, claim.fence)
        return _Attempt(self.journal, token, parent.scope.tenant_id)

    def shred(self, org_id: str, session_id: str) -> None:
        _ = self.control.post(
            "/shred-workspace-key", json={"orgId": org_id, "sessionId": session_id}
        ).raise_for_status()


class _StoreHarness:
    def __init__(self, main: _Attempt, other: _Attempt) -> None:
        self.store = main.store
        self.scope = main.scope
        self._other = other
        self.property_examples = 15

    def other_tenant(self) -> tuple[NodeWorkspaceStore, ObjectScope]:
        return self._other.store, self._other.scope

    def workspace_id(self, name: str) -> str:
        return f"ws:{SESSION}:{name}"


@pytest.fixture
async def world(node_server: _NodeServer) -> _World:
    return _World(node_server)


class TestNodeWorkspaceStoreContract(WorkspaceStoreContract):
    @pytest.fixture
    async def harness(self, world: _World) -> _StoreHarness:  # type: ignore[override]
        main = await world.attempt("run-ws-main", ORG)
        other = await world.attempt("run-ws-other", OTHER_ORG)
        return _StoreHarness(main, other)


class _KeyHarness:
    def __init__(self, world: _World, main: _Attempt, others: list[tuple[_Attempt, str]]) -> None:
        self._world = world
        self._main = main
        self.keys = main.keys
        self.tenant_id = ORG
        self.key_domain = f"ws:{SESSION}"
        self._others = others

    def others(self) -> tuple[tuple[NodeKeyProvider, str, str], ...]:
        return tuple(
            (attempt.keys, attempt.scope.tenant_id, f"ws:{session}") for attempt, session in self._others
        )

    async def shred(self, tenant_id: str, key_domain: str) -> None:
        self._world.shred(tenant_id, key_domain.removeprefix("ws:"))
        self._main.keys.forget()


class TestNodeKeyProviderContract(KeyProviderContract):
    @pytest.fixture
    async def harness(self, world: _World) -> _KeyHarness:  # type: ignore[override]
        main = await world.attempt("run-key-main", ORG)
        other_session = await world.attempt("run-key-s2", ORG, "ses-ws-2")
        other_org = await world.attempt("run-key-org", OTHER_ORG)
        return _KeyHarness(world, main, [(other_session, "ses-ws-2"), (other_org, SESSION)])


async def test_py2_the_key_is_leased_and_refused_outside_its_scope(world: _World) -> None:
    attempt = await world.attempt("run-lease", ORG)
    key = await attempt.keys.data_key(ORG, f"ws:{SESSION}")
    with pytest.raises(WorkspaceAccessDenied):
        await attempt.keys.data_key(OTHER_ORG, f"ws:{SESSION}")
    with pytest.raises(WorkspaceAccessDenied):
        await attempt.keys.data_key(ORG, "ws:another-session")
    with pytest.raises(WorkspaceAccessDenied):
        await attempt.store.head(ObjectScope(OTHER_ORG), f"ws:{SESSION}")
    world.shred(ORG, SESSION)
    # Held for the lease: a shred reaches this worker when the lease ends.
    assert await attempt.keys.data_key(ORG, f"ws:{SESSION}") == key
    attempt.keys.forget()
    with pytest.raises(WorkspaceKeyDestroyed):
        await attempt.keys.data_key(ORG, f"ws:{SESSION}")


async def test_sk4_crypto_shred_end_to_end_through_node(world: _World) -> None:
    attempt = await world.attempt("run-shred", ORG)
    blobs = InMemoryBinaryObjectStore()
    objects = SealedObjectStore(blobs, attempt.keys)
    scope = ObjectScope(ORG, f"ws:{SESSION}")
    ref = await objects.put(scope, b"SENTINEL workspace bytes")
    workspace = f"ws:{SESSION}"
    await attempt.store.commit(
        attempt.scope,
        workspace,
        base_revision=0,
        changes=ChangeSet.of(WorkspaceFile("notes.md", ref)),
        reason="turn_end",
    )
    assert await objects.get(scope, ref) == b"SENTINEL workspace bytes"
    assert all(b"SENTINEL" not in stored for stored in blobs.stored_bytes())
    world.shred(ORG, SESSION)
    attempt.keys.forget()
    with pytest.raises(WorkspaceKeyDestroyed):
        await objects.get(scope, ref)


async def test_py3_a_manifest_whose_hash_does_not_match_is_refused(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = await world.attempt("run-hash", ORG)
    await attempt.store.commit(
        attempt.scope,
        f"ws:{SESSION}",
        base_revision=0,
        changes=ChangeSet.of(file("a.txt", "a")),
        reason="seed",
    )
    original = httpx.Response.json

    def tampered(self: httpx.Response, **kwargs: object) -> object:
        body = original(self, **kwargs)
        if isinstance(body, dict) and "contentHash" in body:
            body["contentHash"] = "sha256:" + "0" * 64
        return body

    monkeypatch.setattr(httpx.Response, "json", tampered)
    with pytest.raises(WorkspaceError, match="content hash"):
        await attempt.store.head(attempt.scope, f"ws:{SESSION}")


async def test_refusals_are_logged_with_their_reason(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    attempt = await world.attempt("run-log", ORG)
    with caplog.at_level(logging.INFO, logger="app.harness.workspace"), pytest.raises(WorkspaceAccessDenied):
        await attempt.store.head(attempt.scope, "ws:someone-else")
    line = next(r for r in caplog.records if r.getMessage().startswith("workspace.refused"))
    assert line.harness["reason"] == "WORKSPACE_FORBIDDEN"  # type: ignore[attr-defined]
    assert line.harness["runId"] == "run-log"  # type: ignore[attr-defined]


async def test_md4_modes_round_trip_through_node(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = await world.attempt("run-modes", ORG)
    script, link = file("bin/run.sh", "echo"), file("latest", "bin/run.sh")
    changes = ChangeSet.of(
        WorkspaceFile(script.path, script.content, script.mime_type, EXECUTABLE_MODE),
        WorkspaceFile(link.path, link.content, link.mime_type, SYMLINK_MODE),
        file("plain.txt", "p"),
    )
    committed = await attempt.store.commit(
        attempt.scope, f"ws:{SESSION}", base_revision=0, changes=changes, reason="autosave"
    )
    head = await attempt.store.head(attempt.scope, f"ws:{SESSION}")
    assert {p: f.mode for p, f in head.files.items()} == {
        "bin/run.sh": EXECUTABLE_MODE,
        "latest": SYMLINK_MODE,
        "plain.txt": FILE_MODE,
    }
    assert head.content_hash == committed.content_hash
    original = node_store._file_wire  # pyright: ignore[reportPrivateUsage]

    def setuid(entry: WorkspaceFile) -> dict[str, object]:
        return {**original(entry), "mode": 0o104755}

    monkeypatch.setattr(node_store, "_file_wire", setuid)
    with pytest.raises(InvalidChangeSet):
        await attempt.store.commit(
            attempt.scope,
            f"ws:{SESSION}",
            base_revision=head.revision,
            changes=ChangeSet.of(file("suid", "x")),
            reason="autosave",
        )


async def test_wp82_node_2_a_commit_past_the_file_cap_is_a_typed_refusal_through_node(world: _World) -> None:
    """In a long research run, Node answered a 20,977-file commit with a bare 400, which the worker could only read as
    ``WorkspaceError("BAD_REQUEST")``. Now it is the store's size refusal, which the turn degrades on (WP-82)."""
    from harness_core.workspace import MAX_WORKSPACE_FILES

    attempt = await world.attempt("run-wp82-cap", ORG)
    blob = file("x", "x").content
    files = [WorkspaceFile(f"repos/onyx/f{i}.py", blob) for i in range(MAX_WORKSPACE_FILES + 1)]
    with pytest.raises(InvalidChangeSet, match="WORKSPACE_TOO_LARGE"):
        await attempt.store.commit(
            attempt.scope, f"ws:{SESSION}", base_revision=0, changes=ChangeSet.of(*files), reason="autosave"
        )
    committed = await attempt.store.commit(
        attempt.scope, f"ws:{SESSION}", base_revision=0, changes=ChangeSet.of(*files[:-1]), reason="autosave"
    )
    assert len(committed.files) == MAX_WORKSPACE_FILES


async def test_wp82_node_3_an_untyped_refusal_is_one_the_worker_does_not_send_again(world: _World) -> None:
    attempt = await world.attempt("run-wp82-untyped", ORG)
    with pytest.raises(WorkspaceRequestRefused):
        await attempt.store.commit(
            attempt.scope, f"ws:{SESSION}", base_revision=0, changes=ChangeSet.of(file("a", "a")), reason="merge"
        )


async def test_wp82_node_4_a_claim_carries_its_runs_deadline(world: _World) -> None:
    _ = world.control.post(
        "/admit",
        json={
            "runId": "run-wp82-deadline",
            "sessionId": "ses-wp82-deadline",
            "inputText": "hello",
            "spec": dump_contract(minimal_run_spec()),
            "orgId": ORG,
        },
    ).raise_for_status()
    claim = await world.journal.claim("run-wp82-deadline", worker_id="w1", org_id=ORG)
    assert isinstance(claim, NodeClaim) and claim.run is not None
    assert claim.deadline_at_ms == claim.run.absolute_deadline_at


async def test_ch14_node_a_claim_carries_the_stores_clock_at_the_claim(world: _World) -> None:
    """The deadline and every ``notBefore`` are on Node's store clock; the claim names that clock's reading (the run's
    ``updatedAt``, written by the claim), which the worker carries on its suspend-aware clock (``Claim.store_time``)."""
    _ = world.control.post(
        "/admit",
        json={
            "runId": "run-ch14-clock",
            "sessionId": "ses-ch14-clock",
            "inputText": "hello",
            "spec": dump_contract(minimal_run_spec()),
            "orgId": ORG,
        },
    ).raise_for_status()
    claim = await world.journal.claim("run-ch14-clock", worker_id="w1", org_id=ORG)
    assert isinstance(claim, NodeClaim) and claim.run is not None
    assert claim.store_now_ms == claim.run.updated_at
    assert claim.run.lease_expires_at == claim.store_now_ms + world.journal.lease_ms


# ---- P6.4: the writer lease and a child's merge through Node (WL, CW over HTTP) ----


class _WritersHarness:
    def __init__(self, world: _World, first: _Attempt, second: _Attempt) -> None:
        self._world = world
        self._first = NodeWorkspaceWriters(world.journal.attempt_route(first.token))
        self._second = NodeWorkspaceWriters(world.journal.attempt_route(second.token))
        self._first_run, self._second_run = first.token.run_id, second.token.run_id
        self.scope = first.scope
        self.ttl_ms = 300

    def holders(self) -> tuple[tuple[NodeWorkspaceWriters, str], tuple[NodeWorkspaceWriters, str]]:
        return (self._first, self._first_run), (self._second, self._second_run)

    def workspace_id(self, name: str) -> str:
        return f"ws:{SESSION}:{name}"

    def other_tenant(self) -> None:
        return None

    async def elapse(self, ms: int) -> None:
        _ = self._world.control.post("/advance", json={"seconds": ms / 1000}).raise_for_status()


class TestNodeWorkspaceWritersContract(WorkspaceWritersContract):
    @pytest.fixture
    async def harness(self, world: _World) -> _WritersHarness:  # type: ignore[override]
        first = await world.attempt("run-wl-a", ORG)
        second = await world.child(first, "t1.0")
        return _WritersHarness(world, first, second)


async def test_wl7_the_holder_is_the_attempts_own_run(world: _World) -> None:
    attempt = await world.attempt("run-wl-own", ORG)
    writers = NodeWorkspaceWriters(world.journal.attempt_route(attempt.token))
    with pytest.raises(WorkspaceAccessDenied):
        _ = await writers.acquire(attempt.scope, f"ws:{SESSION}", holder="someone-else", ttl_ms=1_000)
    with pytest.raises(WorkspaceAccessDenied):
        _ = await writers.acquire(ObjectScope(OTHER_ORG), f"ws:{SESSION}", holder="run-wl-own", ttl_ms=1_000)
    lease = await writers.acquire(attempt.scope, f"ws:{SESSION}", holder="run-wl-own", ttl_ms=1_000)
    assert (lease.holder, lease.renewed) == ("run-wl-own", False)


class _ChildHarness:
    def __init__(self, world: _World, parent: _Attempt) -> None:
        route = world.journal.attempt_route(parent.token)
        self.store = NodeWorkspaceStore(route)
        self._writers = NodeWorkspaceWriters(route)
        self.merger = StoreChildWorkspaces(lambda _token: (self.store, self._writers))
        self.tenant_id = ORG
        self.session_id = SESSION
        self.parent_run_id = parent.token.run_id
        self.token = parent.token

    def child_run_id(self, name: str) -> str:
        return f"child-{name}"

    async def hold_elsewhere(self, workspace_id: str) -> bool:
        del workspace_id
        return False


class TestNodeChildWorkspacesContract(ChildWorkspacesContract):
    @pytest.fixture
    async def harness(self, world: _World) -> _ChildHarness:  # type: ignore[override]
        return _ChildHarness(world, await world.attempt("run-cw-parent", ORG))
