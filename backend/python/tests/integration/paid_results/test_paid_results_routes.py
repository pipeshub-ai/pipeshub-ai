"""The paid-result routes against real storage (QA S7a round 1 probes s1-s5, promoted; ledger rows QA-S7a-*).

Runs against storage-it-server.ts on local disk, S3 (MinIO) and Azure (Azurite): the storage job of
backend-matrix.yml. Skipped when ARTIFACTS_IT_STORAGE_URL is not set; that job fails on any skip.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import TYPE_CHECKING

import aiohttp
import pytest

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.utils.jwt import mint_service_token
from tests.support.paid_results_it import (
    SCOPED_SECRET,
    STORAGE_TYPE,
    STORAGE_URL,
    new_org,
)

if TYPE_CHECKING:
    from app.services.paid_cache.store import NodePaidResultStore

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600),
              pytest.mark.skipif(not STORAGE_URL, reason="needs ARTIFACTS_IT_STORAGE_URL")]
BASE = "/api/v1/document/internal/paid-results"
HEX = "a" * 64


def _hdr(org: str, scopes: list[str] | None = None) -> dict[str, str]:
    tok = mint_service_token(SCOPED_SECRET, {"orgId": org, "scopes": scopes or ["storage:token"]})
    return {"Authorization": f"Bearer {tok}"}


async def _req(method: str, path: str, org: str, **kw) -> tuple[int, object]:
    async with aiohttp.ClientSession() as s, s.request(method, STORAGE_URL + path, headers=_hdr(org), **kw) as r:
        try:
            body = await r.json(content_type=None)
        except Exception:
            body = await r.text()
        return r.status, body


# ---- S1: presign/list/delete cannot name another org; traversal and odd ids refused ------------------------

async def test_s1_presign_and_routes_refuse_another_org_and_traversal(store: NodePaidResultStore) -> None:
    a, b = new_org(), new_org()
    st, _ = await _req("POST", f"{BASE}/presign", b, json={"orgId": a, "recordId": "r1", "files": [{"path": f"ocr_page/{HEX}.json", "mode": "get"}]})
    assert st == 403
    st, _ = await _req("GET", f"{BASE}/r1?orgId={a}", b)
    assert st == 403
    st, _ = await _req("POST", f"{BASE}/r1/delete", b, json={"orgId": a, "paths": [f"ocr_page/{HEX}.json"]})
    assert st == 403
    bad_paths = [f"ocr_page/../../../{a}/PipesHub/paid-results/r1/ocr_page/{HEX}.json", f"../r2/ocr_page/{HEX}.json",
                 f"ocr_page/{HEX}.json/../x.json", f"ocr_page/{HEX.upper()}.json", f"manifests/../ocr_page/{HEX}.json",
                 "manifests/rev/../../x.json", f"ocr_page%2F..%2F{HEX}.json", f"/ocr_page/{HEX}.json", "manifests/a.b/index.json"]
    for p in bad_paths:
        st, _ = await _req("POST", f"{BASE}/presign", b, json={"recordId": "r1", "files": [{"path": p, "mode": "put"}]})
        assert st == 400, (p, st)
    for rid in ["..", "r1/../r2", "a.b", "org", "x" * 129, "%2e%2e"]:
        st, _ = await _req("POST", f"{BASE}/presign", b, json={"recordId": rid, "files": [{"path": f"ocr_page/{HEX}.json", "mode": "get"}]})
        assert st == 400, (rid, st)
    for pre in ["../", "ocr_page", "manifests/x/y/", "other/"]:
        st, _ = await _req("GET", f"{BASE}/r1", b, params={"prefix": pre})
        assert st == 400, (pre, st)
    st, _ = await _req("GET", f"{BASE}/r1", b, params={"unknown": "1"})
    assert st == 400
    # a wrong-scope token
    async with aiohttp.ClientSession() as s, s.get(STORAGE_URL + f"{BASE}/r1", headers=_hdr(b, ["fetch:config"])) as r:
        assert r.status in (401, 403)


async def test_s1b_listing_cursor_cannot_leave_the_record(store: NodePaidResultStore) -> None:
    a, b = new_org(), new_org()
    for n in range(3):
        await store.put(a, "r1", f"ocr_page/{n:064x}.json", b"x")
        await store.put(b, "r1", f"ocr_page/{n:064x}.json", b"y")
    # A forged cursor: another org's key (local) or garbage (S3/Azure token).
    for after in [f"{a}/PipesHub/paid-results/r1/ocr_page/{0:064x}.json", "AAAA", "../../", "zzzz"]:
        st, body = await _req("GET", f"{BASE}/r1", b, params={"after": after})
        assert st in (200, 400), (after, st)                         # never a 500 for a cursor this service did not issue
        if st == 200:
            assert all(not o["path"].startswith("..") for o in body["objects"])
            for o in body["objects"]:
                assert (await store.get(b, "r1", o["path"])) == b"y"


# ---- S2: record ids that are prefixes of each other; record and org purge are exact -----------------------

async def test_s2_prefix_record_ids_purge_exactly(store: NodePaidResultStore) -> None:
    org, other = new_org(), new_org()
    for rid in ["abc", "abc-longer", "abc_", "ab"]:
        await store.put(org, rid, f"ocr_page/{HEX}.json", rid.encode())
    await store.put(other, "abc", f"ocr_page/{HEX}.json", b"other")
    await store.purge_record(org, "abc")
    assert await store.list_files(org, "abc") == []
    for rid in ["abc-longer", "abc_", "ab"]:
        assert await store.get(org, rid, f"ocr_page/{HEX}.json") == rid.encode()
    assert await store.get(other, "abc", f"ocr_page/{HEX}.json") == b"other"
    assert len(await store.list_files(org, "ab")) == 1             # listing "ab" does not include "abc-longer"
    await store.purge_org(org)
    for rid in ["abc-longer", "abc_", "ab"]:
        assert await store.list_files(org, rid) == []
    assert await store.get(other, "abc", f"ocr_page/{HEX}.json") == b"other"


# ---- S3: conditional put; replace and refresh are signed for their own kind of path only -------------------

async def test_s3_conditional_put_and_the_first_writer_wins(store: NodePaidResultStore) -> None:
    org = new_org()
    path = f"ocr_page/{uuid.uuid4().hex}{uuid.uuid4().hex}.json"
    assert await store.put(org, "r1", path, b'{"first":1}') is True
    assert await store.put(org, "r1", path, b'{"second":1}') is False
    assert await store.get(org, "r1", path) == b'{"first":1}'
    signed = await store.presign(org, "r1", [(path, "put")])
    assert any(k.lower() == "if-none-match" for k in signed[(path, "put")].headers) or STORAGE_TYPE == "local"


async def test_s3b_replace_is_for_manifests_and_refresh_for_results(store: NodePaidResultStore) -> None:
    """QA-S7a-9: a signed overwrite of a stored result needs the mode that says so, and only for result paths."""
    org = new_org()
    result = f"ocr_page/{uuid.uuid4().hex}{uuid.uuid4().hex}.json"
    manifest = "manifests/rev1/parse.json"
    await store.put(org, "r1", result, b'{"first":1}')
    for path, mode in ((result, "replace"), (manifest, "refresh")):          # the wrong mode for the path
        st, _ = await _req("POST", f"{BASE}/presign", org, json={"recordId": "r1", "files": [{"path": path, "mode": mode}]})
        assert st == 400, (path, mode, st)
    await store.replace(org, "r1", manifest, b"{}")
    await store.replace(org, "r1", manifest, b'{"again":1}')
    assert await store.get(org, "r1", manifest) == b'{"again":1}'
    await store.overwrite(org, "r1", result, b'{"repaired":1}')               # the key found unreadable
    assert await store.get(org, "r1", result) == b'{"repaired":1}'
    assert await store.put(org, "r1", result, b'{"third":1}') is False         # put still never overwrites


async def test_s3c_the_local_file_route_applies_the_same_mode_rule(store: NodePaidResultStore) -> None:
    if STORAGE_TYPE != "local":
        return  # the file route is the local store's stand-in for a signed URL; S3 and Azure sign instead (s3b)
    org = new_org()
    result = f"ocr_page/{'b' * 64}.json"
    hdr = {**_hdr(org), "Content-Type": "application/octet-stream"}
    async with aiohttp.ClientSession() as s:
        for path, query in ((result, "replace=1"), ("manifests/r/parse.json", "refresh=1"), (result, "replace=1&refresh=1")):
            async with s.put(f"{STORAGE_URL}{BASE}/r1/files/{path}?{query}", headers=hdr, data=b"{}") as r:
                assert r.status == 400, (path, query, r.status)


# ---- QA-S7a-12: an id the routes cannot hold is a 400 on the real route; the cache never sends one --------------

async def test_s3d_a_record_id_the_routes_refuse_is_a_no_op_for_the_cache(store: NodePaidResultStore) -> None:
    org = new_org()
    cache = PaidResultCache(store)
    for record_id in ("rec.1", "a:b", "x" * 129):
        await cache.delete_record(org, record_id)
        await cache.purge_record(org, record_id)


# ---- S4: two orgs x 200 identical files through the real routes share nothing -----------------------------

async def test_s4_two_orgs_200_files_share_nothing(store: NodePaidResultStore) -> None:
    a, b = new_org(), new_org()
    cache = PaidResultCache(store)
    paid = {a: 0, b: 0}
    sem = asyncio.Semaphore(16)

    async def one(org: str, n: int) -> None:
        key = CacheKey(org, "rec-x", "ocr_page", 1, "m", "p", "q", f"file-{n}")

        async def compute() -> str:
            paid[org] += 1
            return f"t{n}"

        async with sem:
            assert await cache.get_or_compute(key, compute) == f"t{n}"

    await asyncio.gather(*(one(o, n) for n in range(200) for o in (a, b)))
    await asyncio.gather(*(one(o, n) for n in range(200) for o in (a, b)))
    assert paid == {a: 200, b: 200}
    la = {f.path for f in await store.list_files(a, "rec-x")}
    lb = {f.path for f in await store.list_files(b, "rec-x")}
    assert len(la) == len(lb) == 200 and la.isdisjoint(lb)


# ---- S5: purge of a large org: time per object, and a repeat after a partial purge completes ---------------

async def test_s5_large_org_purge_and_repeat(store: NodePaidResultStore) -> None:
    org = new_org()
    n_rec, n_obj = int(os.environ.get("QA_PURGE_RECORDS", "20")), int(os.environ.get("QA_PURGE_OBJECTS", "100"))
    sem = asyncio.Semaphore(32)

    async def put(r: int, i: int) -> None:
        async with sem:
            await store.put(org, f"rec{r}", f"ocr_page/{i:064x}.json", b"x" * 64)

    await asyncio.gather(*(put(r, i) for r in range(n_rec) for i in range(n_obj)))
    # A partial purge (a crash after some records): delete half the records, then the org purge runs.
    for r in range(n_rec // 2):
        await store.purge_record(org, f"rec{r}")
    t0 = time.monotonic()
    await store.purge_org(org)
    dt = time.monotonic() - t0
    left = sum([len(await store.list_files(org, f"rec{r}")) for r in range(n_rec)])
    print(f"{STORAGE_TYPE}: org purge of {n_rec * n_obj // 2} objects took {dt:.2f}s; {left} left")
    assert left == 0
    await store.purge_org(org)                                      # a repeat is a success
