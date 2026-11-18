"""Refresh lease cleanup, lease-less refreshes, manifest reuse by refresh, and the manifest write deadline."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache.cache import PaidResultCache, RefreshPurgeInProgress
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import RunScope, final_manifest_path, open_manifest_path
from tests.support.paid_cache import MemoryResultStore

ORG, REC = "org-1", "rec-1"


class _FailingLocks(InProcessLocks):
    """A lock backend that cannot be asked."""

    async def acquire(self, name: str, ttl_ms: int) -> str | None:
        raise ConnectionError("lock backend down")


async def test_a_cancellation_inside_the_refresh_still_releases_the_lease() -> None:
    locks = InProcessLocks()
    cache = PaidResultCache(MemoryResultStore(), locks)
    inside = asyncio.Event()

    async def holder() -> None:
        async with cache.refresh_purge(ORG, REC, "evt"):
            inside.set()
            await asyncio.sleep(30)

    task = asyncio.ensure_future(holder())
    await inside.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await locks.acquire(cache_module.refresh_name(ORG, REC, "evt") + ":lease", 1000) is not None   # free again


async def test_a_cancellation_during_the_marker_recheck_releases_the_lease() -> None:
    class Cancelling(InProcessLocks):
        calls = 0

        async def is_marked(self, name: str) -> bool:
            Cancelling.calls += 1
            if Cancelling.calls == 2:                         # the recheck after the lease was taken
                raise asyncio.CancelledError
            return await super().is_marked(name)

    locks = Cancelling()
    cache = PaidResultCache(MemoryResultStore(), locks)

    with pytest.raises(asyncio.CancelledError):
        async with cache.refresh_purge(ORG, REC, "evt"):
            pass

    assert await locks.acquire(cache_module.refresh_name(ORG, REC, "evt") + ":lease", 1000) is not None


async def test_with_writes_on_a_refresh_without_the_lease_is_handed_back_and_with_writes_off_it_purges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = PaidResultCache(MemoryResultStore(), _FailingLocks())

    monkeypatch.setenv("PAID_CACHE_WRITE", "on")
    with pytest.raises(RefreshPurgeInProgress):
        async with cache.refresh_purge(ORG, REC, "evt"):
            raise AssertionError("must not purge without the lease")

    monkeypatch.delenv("PAID_CACHE_WRITE", raising=False)
    async with cache.refresh_purge(ORG, REC, "evt") as must_purge:
        assert must_purge is True


class _Manifests:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.reads: list[str] = []
        self.replaced: list[str] = []

    async def get(self, org_id: str, record_id: str, path: str) -> bytes | None:
        self.reads.append(path)
        await asyncio.sleep(0.01)
        return self.files.get(path)

    async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        self.replaced.append(path)
        self.files[path] = data

    async def delete_files(self, org_id: str, record_id: str, paths: list[str]) -> None:
        for p in paths:
            self.files.pop(p, None)


def _scope(**kwargs: object) -> RunScope:
    return RunScope(ORG, REC, "rev", "w", refresh=True, refresh_token="evt-1", **kwargs)  # type: ignore[arg-type]


def _manifest(token: str | None, keys: object) -> bytes:
    body: dict = {"keys": keys}
    if token is not None:
        body["refreshToken"] = token
    return json.dumps(body).encode()


async def test_many_pages_share_one_load_of_the_prior_manifests() -> None:
    scope = _scope()
    open_path = open_manifest_path("rev", "w")
    store = _Manifests({open_path: _manifest("evt-1", ["ocr_page/a.json"])})

    results = await asyncio.gather(*(scope.prior_attempt_keys(store) for _ in range(32)))  # type: ignore[arg-type]

    assert all(r == {"ocr_page/a.json"} for r in results)
    assert len(store.reads) == 2                                   # the open and the final manifest, once


async def test_only_a_manifest_of_this_refresh_is_an_earlier_attempt_of_it() -> None:
    scope = _scope()
    store = _Manifests({
        open_manifest_path("rev", "w"): _manifest("evt-OTHER", ["ocr_page/old.json"]),
        final_manifest_path("rev", "w"): _manifest(None, ["ocr_page/older.json"]),      # no token: before any refresh
    })

    assert await scope.prior_attempt_keys(store) == set()          # type: ignore[arg-type]


async def test_a_manifest_whose_keys_are_not_a_list_is_unusable_not_an_error() -> None:
    scope = _scope()
    store = _Manifests({
        open_manifest_path("rev", "w"): _manifest("evt-1", None),
        final_manifest_path("rev", "w"): _manifest("evt-1", ["ocr_page/b.json"]),
    })

    assert await scope.prior_attempt_keys(store) == {"ocr_page/b.json"}      # type: ignore[arg-type]


async def test_an_undecodable_manifest_is_replaced_rather_than_wedging_every_completion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scope = RunScope(ORG, REC, "rev", "w")
    final_path = final_manifest_path("rev", "w")
    store = _Manifests({final_path: b'{"keys":["ocr_pa'})                      # a truncated write
    await scope.note(store, "ocr_page/c.json", opens=True)                    # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING):
        await scope.finish(store)                                             # type: ignore[arg-type]

    assert json.loads(store.files[final_path])["keys"] == ["ocr_page/c.json"]
    assert any("Unreadable manifest" in r.getMessage() for r in caplog.records)


async def test_a_manifest_is_not_replaced_once_the_stage_deadline_passed_during_the_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = RunScope(ORG, REC, "rev", "w")
    store = _Manifests()
    await scope.note(store, "ocr_page/d.json", opens=True)                    # type: ignore[arg-type]
    store.replaced.clear()
    scope._unflushed = 1
    answers = iter([False, True, True, True])                                 # in time at the start, past it after the reads
    monkeypatch.setattr(scope, "past_deadline", lambda: next(answers, True))

    await scope._write_open(store)                                            # type: ignore[arg-type]

    assert store.replaced == []
