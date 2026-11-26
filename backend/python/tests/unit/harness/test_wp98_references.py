"""WP98-REMAT-1..6: a reference an earlier turn fetched is materialised again on demand, from the cache, under the
approval it was fetched with (ADR-RUN-01 D2).

WP-82 D1 left a later turn to clone again, asking the person again unless they had allowed the repository for the
whole conversation, and fetching it over the network once the cache entry was ten minutes old. A reference is now the
session's: the brief names it and how to have it, the repository's approval holds, and the cache serves the exact
commit the session read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.repositories import (
    RepositoryAsks,
    RepositoryFetchTools,
    repository_effect,
)
from harness_core.effects.types import ToolContext
from harness_core.engines.briefing import earlier_files, workspace_briefing
from harness_core.engines.controller import (
    _SavingEndpoint,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.knowledge.bulk_export import ShelfRelay
from harness_core.repos.cache import RepositoryCache
from harness_core.repos.fetch import MIB, FetchedRepository
from harness_core.repos.location import parse_repository_url
from harness_core.workspace.sync.storage_classes import (
    STORAGE_RECORD_PATH,
    Reference,
    checkout_reference,
    reference_identity,
)
from harness_core.workspace.types import ObjectRef, WorkspaceFile
from harness_engines.pipeshub import ph
from tests.support.git_host import bare_repository, commit, working_copy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

ADDRESS = "https://github.com/onyx-dot-app/onyx"
ONYX = Reference("repos/onyx", ADDRESS, "57cb7b8e0f12", ref="main")
CTX = ToolContext(run_id="run-1", org_id="org-1", invocation_id="i1", attempt_id="a1", idempotency_key="k1")


class _Held:
    def __init__(self, *references: Reference) -> None:
        self.references = references
        self.vouched: list[str] = []

    def reference_for(self, address: str) -> Reference | None:
        return next((r for r in self.references if r.identity == reference_identity(address)), None)

    def fetched(self, address: str) -> None:
        self.vouched.append(address)


class _Ledger:
    def apply(self, events: object) -> None:
        del events

    def source_records(self, run_id: str) -> frozenset[str]:
        return frozenset()


def _call(url: str) -> ToolCall:
    return ToolCall(id="c1", name="repository_fetch", arguments={"url": url})


def _allow(call: ToolCall, effect: Any) -> str:  # noqa: ANN401
    return "allow"


def _asks(base: Any = _allow, references: _Held | None = None) -> RepositoryAsks:  # noqa: ANN401
    return RepositoryAsks(
        base, _Ledger(), "r", None, size_of=lambda location, call: None, cap_bytes=500 * MIB, references=references  # type: ignore[arg-type]
    )


def test_wp98_remat_1_the_approval_a_reference_was_fetched_with_holds_for_that_repository_only() -> None:
    effect = repository_effect()
    held = _Held(ONYX)
    assert _asks()(_call(ADDRESS), effect) == "ask"
    assert _asks(references=held)(_call(ADDRESS), effect) == "allow"
    assert _asks(references=held)(_call(ADDRESS.upper().replace("HTTPS", "https") + ".git"), effect) == "allow"
    assert _asks(references=held)(_call("https://github.com/onyx-dot-app/other"), effect) == "ask"

    def deny(call: ToolCall, effect: Any) -> str:  # noqa: ANN401
        return "deny"

    # The run's own rules (a classification, the flow rule) still decide over a held reference.
    assert _asks(deny, references=held)(_call(ADDRESS), effect) == "deny"


class _Fetcher:
    """The fetcher's surface the tool uses; records what it was asked."""

    def __init__(self, tar: Path) -> None:
        self.tar = tar
        self.asked: list[dict[str, Any]] = []
        self.cache = RepositoryCache(tar.parent / "cache", max_bytes=MIB, max_age_s=60)

        class _Config:
            repo_max_bytes = 500 * MIB

        self.config = _Config()

    async def fetch(self, org_id: str, location: Any, **kwargs: Any) -> FetchedRepository:  # noqa: ANN401
        self.asked.append(kwargs)
        return FetchedRepository(location, "k" * 64, self.tar, 3, "57cb7b8e0f12", None, False, cached=True, public=True)


class _Shelf:
    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str:
        _ = [chunk async for chunk in archive]
        return "/pipeshub/repos"


async def _hosts() -> list[dict[str, Any]]:
    return [{"host": "github.com"}]


async def test_wp98_remat_2_a_held_reference_comes_from_the_cache_at_its_commit_and_every_fetch_is_vouched(
    tmp_path: Path,
) -> None:
    tar = tmp_path / "repo.tar"
    tar.write_bytes(b"tar")
    fetcher = _Fetcher(tar)
    held = _Held(ONYX)
    tools = RepositoryFetchTools(fetcher=fetcher, hosts=_hosts, shelf=_Shelf(), references=held)  # type: ignore[arg-type]
    done = await tools.execute(_call(ADDRESS), CTX)
    assert done.status == "ok", done.content
    assert fetcher.asked[-1]["held"] == ONYX.version
    other = "https://github.com/onyx-dot-app/docs"
    assert (await tools.execute(_call(other), CTX)).status == "ok"
    assert fetcher.asked[-1]["held"] is None
    assert held.vouched == [parse_repository_url(ADDRESS).shown_url, parse_repository_url(other).shown_url]


def test_wp98_remat_3_the_cache_serves_the_commit_a_session_holds_however_old(tmp_path: Path) -> None:
    now = [1000.0]
    cache = RepositoryCache(tmp_path / "cache", max_bytes=10 * MIB, max_age_s=60, clock=lambda: now[0])
    staged = cache.temporary()
    (staged / "repo.tar").write_bytes(b"x" * 10)
    _ = cache.put("k", staged, head="57cb7b8e0f12", ref=None, label="onyx")
    now[0] += 3600
    assert cache.get("k") is None
    assert cache.get("k", head="57cb7b8e0f12") is not None
    assert cache.get("k", head="0000000") is None


def test_wp98_remat_4_the_brief_names_each_reference_and_how_to_have_it_again() -> None:
    record = WorkspaceFile(STORAGE_RECORD_PATH, ObjectRef("o", "h", 10), "application/json")
    note = WorkspaceFile("notes.md", ObjectRef("o2", "h2", 5), "text/markdown")
    others = earlier_files({STORAGE_RECORD_PATH: record, "notes.md": note}, ())
    assert [entry.path for entry in others] == ["notes.md"]
    brief = workspace_briefing((), "out", others, None, (ONYX,))
    assert "repos/onyx/: onyx-dot-app/onyx at 57cb7b8, a fetched copy" in brief
    assert f"`ph git clone {ADDRESS} repos/onyx --ref main`" in brief
    assert "without asking the person again" in brief
    assert STORAGE_RECORD_PATH not in brief


def test_wp98_remat_5_ph_git_clone_registers_its_checkout_with_the_commit_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = working_copy(tmp_path / "seed")
    head = commit(seed, "README.md", "one\n")
    copy = bare_repository(tmp_path / "copies" / "1", "x", seed)
    answer = {"path": str(copy), "url": ADDRESS, "label": "github.com/onyx-dot-app/onyx", "commit": head}

    def fake_call(gw: Any, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"content": [{"type": "text", "text": json.dumps(answer)}]}

    monkeypatch.setattr(ph, "call", fake_call)
    monkeypatch.chdir(tmp_path)
    assert ph.main(["git", "clone", ADDRESS, "repos/onyx", "--ref", "main"], gw=object()) == 0  # type: ignore[arg-type]
    config = (tmp_path / "repos" / "onyx" / ".git" / "config").read_bytes()
    assert checkout_reference("repos/onyx/.git/config", config) == Reference("repos/onyx", ADDRESS, head, ref="main")


@pytest.fixture
def relay() -> Iterator[tuple[ShelfRelay, _SavingEndpoint]]:
    shelf = ShelfRelay()
    endpoint = _SavingEndpoint(object())  # type: ignore[arg-type]
    shelf.attach(endpoint)  # type: ignore[arg-type]
    yield shelf, endpoint
    shelf.detach(endpoint)  # type: ignore[arg-type]


def test_wp98_remat_6_the_turn_holds_the_recorded_references_and_vouches_what_it_fetched(
    relay: tuple[ShelfRelay, _SavingEndpoint],
) -> None:
    shelf, endpoint = relay
    endpoint.references = (ONYX,)
    assert shelf.reference_for("https://github.com/Onyx-Dot-App/onyx.git") == ONYX
    assert shelf.reference_for("https://github.com/onyx-dot-app/docs") is None
    shelf.fetched("https://github.com/onyx-dot-app/docs")
    assert endpoint.vouched == {"github.com/onyx-dot-app/docs"}
    shelf.detach(endpoint)  # type: ignore[arg-type]
    assert shelf.reference_for(ADDRESS) is None
