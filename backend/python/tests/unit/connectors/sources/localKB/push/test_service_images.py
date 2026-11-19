"""A push with ``storage://`` images, end to end through the service with fakes that behave like the real services."""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    PushClaimRefused,
)
from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push import service as SV
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.services.graph_db.interface.graph_db_provider import RefAccess
from app.services.graph_db.push_claim import PushClaim
from app.services.storage_documents import StorageUploadError

from .image_harness import KB as KB_ID
from .image_harness import ORG, PNG, did, image_body, make_harness, ref

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 64


@pytest.fixture
async def h(tmp_path) -> AsyncIterator[object]:
    harness = await make_harness(tmp_path)
    harness.docs.put(1, PNG)
    yield harness
    await harness.store.stop()


async def refused(coro) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        await coro
    return e.value


class TestMaterialised:
    async def test_bytes_parse_as_blocks_all_images_data_rev_and_size_match(self, h) -> None:
        r = await h.push(image_body([ref(1), ref(1)]))
        data = h.docs.uploads[-1]
        stored = h.stored()
        uris = [b["data"]["uri"] for b in stored["blocks"] if b["type"] == "image"]
        assert uris == ["data:image/png;base64," + base64.b64encode(PNG).decode()] * 2
        assert stored["push_format"] == 1 and b"storage://" not in data and b"pb-img" not in data
        assert r.content_rev == hashlib.sha256(data).hexdigest()[:16]
        assert h.mat.tasks[0]["cap_bytes"] == 30 << 20

    async def test_revision_digest_holds_the_images_fingerprint(self, h) -> None:
        await h.push(image_body([ref(1)]))
        _, digest, _ = C.parse_revision_id(h.graph.record["externalRevisionId"])
        base = C.request_digest(hashlib.sha256(b"x").hexdigest(), None)
        assert digest != base and len(digest) == 64


class TestTextOnly:
    async def test_no_spawn_and_no_slot(self, h) -> None:
        await h.push(image_body([]))
        assert h.mat.runs == 0 and h.docs.info_calls == [] and h.admission._taken == 0

    @pytest.mark.parametrize("form", ["plain", "escaped_slash", "escaped_letter"])
    async def test_escaped_refs_take_a_slot(self, h, form) -> None:
        """The slot follows the parsed references, so a JSON-escaped spelling of ``storage://`` still takes one."""
        body = image_body([ref(1)])
        body = {"plain": body, "escaped_slash": body.replace(b"storage://", b"storage:\\/\\/"),
                "escaped_letter": body.replace(b"storage://", b"\\u0073torage://")}[form]
        assert body.count(b"storage://") == (1 if form == "plain" else 0)
        took = []
        orig = h.admission.reserve
        h.admission.reserve = lambda *a, **k: (took.append(1), orig(*a, **k))[1]
        await h.push(body)
        assert took == [1] and h.mat.runs == 1 and h.admission._taken == 0

    async def test_plain_text_storage_word_takes_no_slot(self, h) -> None:
        took = []
        orig = h.admission.reserve
        h.admission.reserve = lambda *a, **k: (took.append(1), orig(*a, **k))[1]
        await h.push(image_body([], text="see storage:// and storage://" + did(1) + " in prose"))
        assert took == [] and h.mat.runs == 0

    async def test_flag_off_service_refuses_refs_as_before(self, tmp_path) -> None:
        h2 = await make_harness(tmp_path, materialiser=False)
        try:
            e = await refused(h2.push(image_body([ref(1)])))
            assert (e.status, e.reason) == (422, "IMAGE_REF_INVALID")
        finally:
            await h2.store.stop()


class TestResolvedBudget:
    async def test_three_20mib_occurrences_413_before_any_download(self, h) -> None:
        big = PNG + b"x" * ((20 << 20) - len(PNG))
        for n in (2, 3, 4):
            h.docs.put(n, big)
        h.graph.access = RefAccess(frozenset(), frozenset())  # unlinked own uploads
        e = await refused(h.push(image_body([ref(2), ref(3), ref(4)])))
        assert (e.status, e.extra["limit"]) == (413, "resolved") and e.extra["limitBytes"] == 30 << 20
        assert h.store.count("download") == 0 and h.mat.runs == 0 and h.docs.uploads == []

    async def test_17_occurrences_of_a_1kib_image_is_422_not_413(self, h) -> None:
        e = await refused(h.push(image_body([ref(1)] * 17)))
        assert (e.status, e.reason) == (422, "LIMIT_EXCEEDED")

    async def test_text_only_push_above_the_setting_is_413_resolved(self, h) -> None:
        h.docs.limit = 1000
        e = await refused(h.push(image_body([], text="x" * 5000)))
        assert (e.status, e.extra["limit"], e.extra["limitBytes"]) == (413, "resolved", 1000)
        assert h.graph.document_reads == 0 and h.docs.limit_reads == [True]  # refused up front, from the cached read, before any graph work
        assert "1000" not in e.extra["hint"] or "file size limit" in e.extra["hint"]

    async def test_limit_reads_per_phase_images(self, h) -> None:
        """Cached up front, then uncached at materialise, then uncached just before the store."""
        await h.push(image_body([ref(1)]))
        assert h.docs.limit_reads == [True, False, False]

    async def test_limit_reads_per_phase_text_only(self, h) -> None:
        await h.push(image_body([]))
        assert h.docs.limit_reads[0] is True and h.docs.limit_reads[-1] is False

    def _stale_cache(self, h, *uncached: int) -> None:
        """A cached read answers the old 30 MiB; the n-th uncached read answers ``uncached[n]`` (the last repeats)."""
        seen: list[int] = []

        async def read(*, use_cache=False) -> object:
            h.docs.limit_reads.append(use_cache)
            if use_cache:
                return 30 << 20
            seen.append(1)
            return uncached[min(len(seen), len(uncached)) - 1]

        h.docs.upload_limit = read

    async def test_materialise_cap_is_the_fresh_setting(self, h) -> None:
        self._stale_cache(h, 7 << 20)
        await h.push(image_body([ref(1)]))
        assert h.mat.tasks[0]["cap_bytes"] == 7 << 20

    async def test_store_cap_is_the_fresh_setting(self, h) -> None:
        """The setting drops after materialise: the pre-store read sees it and refuses; nothing is stored."""
        self._stale_cache(h, 30 << 20, 10)
        e = await refused(h.push(image_body([ref(1)])))
        assert (e.status, e.extra["limit"], e.extra["limitBytes"]) == (413, "resolved", 10) and h.docs.uploads == []

    async def test_unreadable_setting_is_a_retryable_503(self, h) -> None:
        h.docs.limit_error = StorageUploadError("x")
        e = await refused(h.push(image_body([])))
        assert (e.status, e.reason) == (503, "STORAGE_UNAVAILABLE") and e.extra["retryAfter"]

    async def test_cap_is_min_of_48mib_and_setting(self, h) -> None:
        h.docs.limit = 64 << 20
        await h.push(image_body([ref(1)]))
        assert h.mat.tasks[0]["cap_bytes"] == SV.MAX_INLINED_BYTES

    async def test_child_running_total_obeys_the_effective_cap(self, h) -> None:
        """``info`` lies (says 1 KiB); the body is 3 MiB x 16 and the cap 10 MiB: the child stops at the cap."""
        h.docs.limit = 10 << 20
        big = PNG + b"x" * (3 << 20)
        doc = h.docs.put(2, big)
        info_len = len(big)
        orig = h.docs.info_many

        async def lying(org, ids, **kw) -> object:
            out = await orig(org, ids, **kw)
            for v in out.values():
                if v:
                    object.__setattr__(v, "object_size", None)
                    object.__setattr__(v, "declared_size", 1024)
            return out

        h.docs.info_many = lying
        e = await refused(h.push(image_body([ref(2)] * 16)))
        assert (e.status, e.extra["limit"], e.extra["limitBytes"]) == (413, "resolved", 10 << 20) and info_len > 1024 and doc

    async def test_a_400_over_the_limit_is_413_resolved(self, h) -> None:
        h.docs.create_error = StorageUploadError("big", status=413, limit_bytes=5 << 20)
        e = await refused(h.push(image_body([ref(1)])))
        assert (e.status, e.extra["limitBytes"]) == (413, 5 << 20)

    async def test_a_400_under_the_limit_is_a_500_storage_rejected_not_413_not_503(self, h) -> None:
        h.docs.create_error = StorageUploadError("bad", status=400)
        e = await refused(h.push(image_body([ref(1)])))
        assert (e.status, e.reason) == (500, "STORAGE_REJECTED") and "retryAfter" not in e.extra


class TestIdempotentRepush:
    async def test_100_identical_pushes_with_refs(self, h) -> None:
        h.docs.put(2, JPEG)
        revs = set()
        for _ in range(100):
            r = await h.push(image_body([ref(1), ref(2)]))
            revs.add(r.content_rev)
        assert len(h.docs.uploads) == 1 and sum(1 for a, _ in h.claims if a == "create") == 1
        # one `info` per distinct ref per push; the first push reads once more for the re-check before its claim
        assert len(h.docs.info_calls) == 101 and all(c == [did(1), did(2)] for c in h.docs.info_calls)
        assert h.store.count("download") == 2 and h.mat.runs == 1 and len(revs) == 1
        assert h.graph.access_calls and len(h.graph.access_calls) == 101


class TestImageChange:
    async def test_replaced_bytes_higher_version_is_content(self, h) -> None:
        await h.push(image_body([ref(1)], version=1))
        doc = h.docs.docs[did(1)]
        doc.data, doc.counter = JPEG, 1
        h.store.objects[did(1)].data = JPEG
        r = await h.push(image_body([ref(1)], version=2))
        assert (r.status, r.content_changed) == ("updated", True) and len(h.docs.uploads) == 2

    async def test_same_bytes_rewritten_higher_version_is_metadata_no_document_no_event(self, h) -> None:
        await h.push(image_body([ref(1)], version=1))
        h.docs.docs[did(1)].counter = 1  # rewritten with the same bytes
        r = await h.push(image_body([ref(1)], version=2))
        assert (r.status, r.content_changed) == ("updated", False) and len(h.docs.uploads) == 1
        assert h.claims[-1][0] == "metadata"

    async def test_untouched_images_higher_version_is_metadata_and_answers_the_stored_content_rev(self, h) -> None:
        first = await h.push(image_body([ref(1)], version=1))
        r = await h.push(image_body([ref(1)], version=2))
        assert (r.status, r.content_changed) == ("updated", False) and h.claims[-1][0] == "metadata" and len(h.docs.uploads) == 1
        assert first.content_rev and r.content_rev == first.content_rev

    async def test_same_version_after_same_bytes_rewrite_and_its_retry_is_200_never_409(self, h) -> None:
        await h.push(image_body([ref(1)], version=1))
        h.docs.docs[did(1)].counter = 1
        first = await h.push(image_body([ref(1)], version=1))
        again = await h.push(image_body([ref(1)], version=1))
        assert (first.status, first.http_status, again.status) == ("unchanged", 200, "unchanged")
        assert len(h.docs.uploads) == 1 and h.claims[-1][0] == "retag"

    async def test_same_version_changed_bytes_is_409_with_hint(self, h) -> None:
        await h.push(image_body([ref(1)], version=1))
        doc = h.docs.docs[did(1)]
        doc.data, doc.counter = JPEG, 1
        h.store.objects[did(1)].data = JPEG
        e = await refused(h.push(image_body([ref(1)], version=1)))
        assert (e.status, e.reason) == (409, "VERSION_CONFLICT")

    async def test_after_a_retag_the_next_identical_push_is_unchanged_with_0_downloads(self, h) -> None:
        await h.push(image_body([ref(1)], version=1))
        h.docs.docs[did(1)].counter = 1
        await h.push(image_body([ref(1)], version=1))  # retag
        downloads = h.store.count("download")
        r = await h.push(image_body([ref(1)], version=1))
        assert r.status == "unchanged" and h.store.count("download") == downloads and h.claims[-1][0] == "retag"

    async def test_metadata_and_retag_write_the_new_digest(self, h) -> None:
        await h.push(image_body([ref(1)], version=1))
        old = C.parse_revision_id(h.graph.record["externalRevisionId"])
        h.docs.docs[did(1)].counter = 1
        await h.push(image_body([ref(1)], version=2))
        new = C.parse_revision_id(h.graph.record["externalRevisionId"])
        assert new[1] != old[1] and new[2] == old[2]


class TestRealRetag:
    async def test_retag_writes_the_new_digest_with_the_stored_contentrev(self, h) -> None:
        tx = AsyncMock()
        tx.retag_push_record = AsyncMock(return_value=True)
        processor = AsyncMock()
        processor.data_store_provider.transaction = lambda: _Tx(tx)
        h.svc._processor = AsyncMock(return_value=processor)
        from app.connectors.sources.localKB.push.worker import Validated
        v = Validated("n", "k", 1, None, None, None, "0" * 64, "a" * 64, "/x", 5, "b" * 16, 0, 1, 0)
        old = C.revision_id("c" * 64, "b" * 16)
        assert await SV.KbPushService._retag(h.svc, {"id": "rid", "externalRevisionId": old}, v, KB_ID, ORG) is True
        assert tx.retag_push_record.await_args.kwargs["new_revision"] == C.revision_id("a" * 64, "b" * 16)


class _Tx:
    def __init__(self, tx: object) -> None:
        self.tx = tx

    async def __aenter__(self) -> object:
        return self.tx

    async def __aexit__(self, *exc: object) -> None:
        return None


class TestUntrusted:
    def distrust(self, h, **kw) -> None:
        for d in h.docs.docs.values():
            for k, v in kw.items():
                setattr(d, k, v)

    async def test_untrusted_fingerprint_never_answers_unchanged_and_materialises(self, h) -> None:
        self.distrust(h, trusted=False)
        for _ in range(3):
            r = await h.push(image_body([ref(1)]))
        assert r.status == "unchanged" and h.mat.runs == 3 and len(h.docs.uploads) == 1
        assert [a for a, _ in h.claims] == ["create", "retag", "retag"]

    async def test_absent_or_old_fingerprint_cannot_bind_the_download_and_is_refused(self, h) -> None:
        """An answer without a usable ``storageFingerprint`` carries no verified object (no etag): fail closed."""
        for value in (None, 99):
            self.distrust(h, fp_version=value)
            e = await refused(h.push(image_body([ref(1)])))
            assert (e.status, e.reason) == (422, "IMAGE_FETCH_FAILED")
        assert h.store.count("download") == 0 and h.docs.uploads == []

    async def test_old_style_info_100_pushes_100_downloads_1_upload_1_newrecord_one_contentrev_no_event_after_the_first(self, h) -> None:
        """An untrusted but verified answer (the service reported the object, not that it is stable)."""
        self.distrust(h, trusted=False)
        results = [await h.push(image_body([ref(1)])) for _ in range(100)]
        assert h.store.count("download") == 100 and len(h.docs.uploads) == 1
        assert sum(1 for a, _ in h.claims if a == "create") == 1 and len({r.content_rev for r in results}) == 1
        assert all(r.event is None for r in results[1:]) and all(a != "content" for a, _ in h.claims)

    async def test_request_key_is_nonce_free(self, h) -> None:
        self.distrust(h, trusted=False)
        seen = []
        orig = C.request_key
        C.request_key = lambda *a: (seen.append(orig(*a)), seen[-1])[1]
        try:
            await h.push(image_body([ref(1)]))
            await h.push(image_body([ref(1)]))
        finally:
            C.request_key = orig
        digests = []
        assert len(seen) == 2 and seen[0] == seen[1] and digests == []

    async def test_two_untrusted_pushes_of_one_body_have_equal_request_key_and_different_stored_digest(self, h) -> None:
        self.distrust(h, trusted=False)
        await h.push(image_body([ref(1)]))
        d1 = C.parse_revision_id(h.graph.record["externalRevisionId"])[1]
        await h.push(image_body([ref(1)]))
        d2 = C.parse_revision_id(h.graph.record["externalRevisionId"])[1]
        assert d1 != d2

    async def test_nonce_is_drawn_once_per_push_outside_the_retry_loop(self, h, monkeypatch) -> None:
        self.distrust(h, trusted=False)
        from types import SimpleNamespace
        draws = []
        real = SV.secrets.token_hex
        monkeypatch.setattr(SV, "secrets", SimpleNamespace(token_hex=lambda n: (draws.append(1), real(n))[1]))
        h.svc._claim_and_write_real = h.svc._claim_and_write
        calls = {"n": 0}
        inner = h.svc._claim_and_write

        async def lose_once(*a) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                raise PushClaimRefused(PushClaim.LOST)
            return await inner(*a)

        h.svc._claim_and_write = lose_once
        await h.push(image_body([ref(1)]))
        assert calls["n"] == 2 and draws == [1]

    @pytest.mark.parametrize("field,value", [("trusted", False), ("settled", False)])
    async def test_untrusted_counter_by_reason(self, h, field, value) -> None:
        SV.UNTRUSTED.clear()
        self.distrust(h, **{field: value})
        await h.push(image_body([ref(1)]))
        expected = {"trusted": "other", "settled": "open_writer"}[field]
        assert SV.UNTRUSTED[expected] >= 1

    async def test_one_bounded_settle_retry_when_not_settled(self, h) -> None:
        self.distrust(h, settled=False)
        await h.push(image_body([ref(1)]))
        assert len(h.docs.info_calls) == 3  # one read, one settle retry, then the nonce path; plus the re-check
        h.svc._sleep.assert_awaited_once_with(SV.SETTLE_RETRY_S)
        assert SV.SETTLE_RETRY_S <= 2.0

    async def test_download_refused_when_object_etag_is_none_or_vendor_missing(self, h) -> None:
        orig = h.docs.info_many

        async def no_etag(org, ids, **kw) -> object:
            out = await orig(org, ids, **kw)
            for v in out.values():
                object.__setattr__(v, "object_etag", None)
            return out

        h.docs.info_many = no_etag
        e = await refused(h.push(image_body([ref(1)])))
        assert e.reason == "IMAGE_FETCH_FAILED" and h.store.count("download") == 0

    async def test_one_untrusted_ref_makes_the_whole_push_nonce_digested(self, h) -> None:
        h.docs.put(2, JPEG, trusted=False)
        await h.push(image_body([ref(1), ref(2)]))
        await h.push(image_body([ref(1), ref(2)]))
        assert h.mat.runs == 2


class TestFetchFailures:
    @pytest.mark.parametrize("mode", ["500", "reset", "truncated"])
    async def test_download_failures_are_422_image_fetch_failed(self, h, mode) -> None:
        h.store.mode[did(1)] = mode
        e = await refused(h.push(image_body([ref(1)])))
        assert (e.status, e.reason) == (422, "IMAGE_FETCH_FAILED")
        assert h.docs.uploads == [] and h.claims == []
        import os
        assert not os.path.exists(os.path.join(h.svc.spool.dir, "imgwork"))

    async def test_download_timeout_is_422(self, h) -> None:
        h.store.mode[did(1)] = "slow"
        orig = h.mat.run

        async def quick(task) -> object:
            return await orig({**task, "request_timeout_s": 0.5})

        h.mat.run = quick
        e = await refused(h.push(image_body([ref(1)])))
        assert e.reason == "IMAGE_FETCH_FAILED"

    @pytest.mark.parametrize("error", [StorageUploadError("answered 500", status=500), StorageUploadError("could not be reached: TimeoutError")])
    async def test_info_failures_are_422_image_fetch_failed(self, h, error) -> None:
        h.docs.info_error = error
        e = await refused(h.push(image_body([ref(1)])))
        assert (e.status, e.reason) == (422, "IMAGE_FETCH_FAILED") and h.docs.uploads == [] and h.claims == []

    async def test_if_match_412_is_422_with_a_resend_hint(self, h) -> None:
        h.store.objects[did(1)].data = PNG + b"changed"
        e = await refused(h.push(image_body([ref(1)])))
        assert e.reason == "IMAGE_FETCH_FAILED" and "send the push again" in e.message

    async def test_graph_outage_is_retryable_not_a_denial(self, h) -> None:
        from app.exceptions.graph_db_exceptions import (
            PermissionVerificationUnavailableError,
        )
        h.graph.access_error = PermissionVerificationUnavailableError("down")
        e = await refused(h.push(image_body([ref(1)])))
        assert (e.status, e.reason) == (503, "PERMISSION_CHECK_UNAVAILABLE")


class TestRetry:
    async def test_lost_attempt_reuses_the_materialised_document_and_spliced_file(self, h) -> None:
        inner = h.svc._claim_and_write
        n = {"c": 0}

        async def lose_first(*a) -> object:
            n["c"] += 1
            if n["c"] == 1:
                raise PushClaimRefused(PushClaim.LOST)
            return await inner(*a)

        h.svc._claim_and_write = lose_first
        await h.push(image_body([ref(1)]))
        assert n["c"] == 2 and h.mat.runs == 1 and len(h.docs.uploads) == 1


class TestRecheck:
    async def test_recheck_runs_on_every_attempt_and_a_denial_cleans_the_document(self, h) -> None:
        inner = h.svc._claim_and_write
        n = {"c": 0}

        async def lose_first(*a) -> object:
            n["c"] += 1
            if n["c"] == 1:
                h.graph.access = RefAccess(frozenset({did(1)}), frozenset())  # access revoked meanwhile
                raise PushClaimRefused(PushClaim.LOST)
            return await inner(*a)

        h.graph.access = RefAccess(frozenset({did(1)}), frozenset({did(1)}))
        h.svc._claim_and_write = lose_first
        e = await refused(h.push(image_body([ref(1)])))
        assert e.reason == "IMAGE_REF_INVALID" and len(h.graph.access_calls) == 3 and h.claims == []
        assert h.cleanups, "the stored document of the denied attempt is cleaned up"

    async def test_recheck_skipped_for_retag_unchanged_and_metadata(self, h) -> None:
        await h.push(image_body([ref(1)], version=1))
        before = len(h.graph.access_calls)
        await h.push(image_body([ref(1)], version=1))  # unchanged
        assert len(h.graph.access_calls) == before + 1  # only the push's own read
        h.docs.docs[did(1)].counter = 1
        await h.push(image_body([ref(1)], version=2))  # metadata
        assert len(h.graph.access_calls) == before + 2

    async def test_recheck_never_changes_the_stored_fingerprint(self, h) -> None:
        inner = h.svc._claim_and_write
        n = {"c": 0}

        async def bump_then_lose(*a) -> object:
            n["c"] += 1
            if n["c"] == 1:
                h.docs.docs[did(1)].counter = 5  # the fingerprint moved between the first read and the re-check
                raise PushClaimRefused(PushClaim.LOST)
            return await inner(*a)

        h.svc._claim_and_write = bump_then_lose
        await h.push(image_body([ref(1)]))
        digest = C.parse_revision_id(h.graph.record["externalRevisionId"])[1]
        h.docs.docs[did(1)].counter = 0
        fresh = await h.push(image_body([ref(1)]))
        assert fresh.status == "unchanged" and C.parse_revision_id(h.graph.record["externalRevisionId"])[1] == digest
