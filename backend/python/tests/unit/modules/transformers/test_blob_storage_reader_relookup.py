"""A stored-record read takes one fresh mapping lookup when the document it
read may have been replaced since the mapping was read.

Triggers: a 404 from the gateway, a 403 or 404 on a URL signed in this call, a
failed ranged download. A cached URL keeps its re-sign path. The mapping naming
the same document means the original error is real and is re-raised unchanged:
an S3 403 is also an expired or skewed signature, and must never read as
"document gone".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers.blob_storage import (
    BlobStorage,
    SignedUrlReadError,
    StorageDocumentNotFoundError,
    StorageLookupUnavailableError,
)
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"
VRID = "vr-1"
_METRIC = "pipeshub_record_reader_relookup_total"


def _count(outcome: str, trigger: str) -> float:
    prefix = f'{_METRIC}{{outcome="{outcome}",trigger="{trigger}"}} '
    for line in METRICS_BACKEND.serialize().splitlines():
        if line.startswith(prefix):
            return float(line.split()[-1])
    return 0.0


def _bs(mapping_after: list[dict | None] | dict | None = None) -> BlobStorage:
    bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
    bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "azureBlob"))
    bs._cached_signed_url = AsyncMock(return_value=None)
    bs._store_signed_url = AsyncMock()
    answers = mapping_after if isinstance(mapping_after, list) else [mapping_after]
    bs.get_document_id_by_virtual_record_id = AsyncMock(side_effect=answers)
    return bs


def _lookup(doc: str, size: int | None = 10) -> dict:
    return {"record_doc_id": doc, "fileSizeBytes": size}


def _envelopes(bs: BlobStorage, by_doc: dict[str, Any]) -> list[str]:
    """Gateway answers per document id: a dict is the envelope, an exception is raised."""
    asked: list[str] = []

    async def fetch(session, url, headers, vrid) -> dict:
        doc = url.split("/internal/")[1].split("/")[0]
        asked.append(doc)
        outcome = by_doc[doc]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    bs._fetch_record_envelope = fetch
    return asked


@pytest.fixture(autouse=True)
def _session() -> Iterator[None]:
    with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=MagicMock()):
        yield


def _signed(url: str = "https://blob/doc") -> dict:
    return {"signedUrl": url}


class TestGatewayNotFound:
    async def test_t12_a_replaced_document_is_read_from_the_new_mapping(self) -> None:
        bs = _bs(_lookup("new"))
        asked = _envelopes(bs, {"old": StorageDocumentNotFoundError("gone"), "new": {"record": {"x": 1}}})
        bs._decode_record = AsyncMock(return_value={"record_name": "new"})
        before = _count("served", "not_found")

        record = await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        assert record == {"record_name": "new"}
        assert asked == ["old", "new"]
        assert bs.get_document_id_by_virtual_record_id.await_count == 1
        assert _count("served", "not_found") == before + 1

    async def test_t12_control_the_same_document_stays_a_404(self) -> None:
        bs = _bs(_lookup("old"))
        asked = _envelopes(bs, {"old": StorageDocumentNotFoundError("gone")})
        before = _count("unchanged", "not_found")

        with pytest.raises(StorageDocumentNotFoundError):
            await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        assert asked == ["old"]
        assert _count("unchanged", "not_found") == before + 1

    async def test_no_mapping_any_more_re_raises_the_404(self) -> None:
        bs = _bs(None)
        _envelopes(bs, {"old": StorageDocumentNotFoundError("gone")})

        with pytest.raises(StorageDocumentNotFoundError):
            await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

    async def test_a_failed_re_lookup_re_raises_the_404(self) -> None:
        bs = _bs([RuntimeError("graph down")])
        _envelopes(bs, {"old": StorageDocumentNotFoundError("gone")})
        before = _count("lookup_failed", "not_found")

        with pytest.raises(StorageDocumentNotFoundError):
            await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        assert _count("lookup_failed", "not_found") == before + 1

    async def test_the_new_document_failing_too_is_not_looked_up_again(self) -> None:
        bs = _bs([_lookup("new"), _lookup("newer")])
        asked = _envelopes(
            bs,
            {"old": StorageDocumentNotFoundError("a"), "new": StorageDocumentNotFoundError("b"), "newer": {"record": {}}},
        )
        before = _count("failed", "not_found")

        with pytest.raises(StorageDocumentNotFoundError):
            await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        assert asked == ["old", "new"]
        assert bs.get_document_id_by_virtual_record_id.await_count == 1, "exactly one re-lookup per read"
        assert _count("failed", "not_found") == before + 1

    async def test_a_lookup_that_was_not_passed_is_read_once_and_the_re_lookup_makes_a_second(self) -> None:
        bs = _bs([_lookup("old"), _lookup("new")])
        _envelopes(bs, {"old": StorageDocumentNotFoundError("gone"), "new": {"record": {"x": 1}}})
        bs._decode_record = AsyncMock(return_value={"record_name": "new"})

        assert (await bs.get_record_from_storage(VRID, ORG))["record_name"] == "new"
        assert bs.get_document_id_by_virtual_record_id.await_count == 2


class TestSignedUrlOfThisCall:
    @pytest.mark.parametrize("status", [403, 404])
    async def test_t20_single_get_status_on_a_fresh_url_re_looks_up(self, status: int) -> None:
        bs = _bs(_lookup("new"))
        _envelopes(bs, {"old": _signed("https://blob/old"), "new": _signed("https://blob/new")})
        reads: list[str] = []

        async def from_url(session, url, size, vrid, lease=None) -> dict:
            reads.append(url)
            if url.endswith("/old"):
                raise SignedUrlReadError(f"Failed to retrieve record: status {status}", status)
            return {"record_name": "new"}

        bs._record_from_signed_url = from_url
        bs._decode_record = AsyncMock(return_value={"record_name": "new"})
        before = _count("served", "signed_url")

        record = await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        assert record == {"record_name": "new"}
        assert bs.get_document_id_by_virtual_record_id.await_count == 1
        assert _count("served", "signed_url") == before + 1

    async def test_t20_failed_ranged_download_re_looks_up(self) -> None:
        bs = _bs(_lookup("new"))
        _envelopes(bs, {"old": _signed("https://blob/old"), "new": _signed("https://blob/new")})

        async def from_url(session, url, size, vrid, lease=None) -> dict:
            if url.endswith("/old"):
                raise SignedUrlReadError("Both parallel and fallback downloads failed: x", 404, ranged=True)
            return {"record_name": "new"}

        bs._record_from_signed_url = from_url
        before = _count("served", "ranged")

        record = await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old", None))

        assert record == {"record_name": "new"}
        assert _count("served", "ranged") == before + 1

    @pytest.mark.parametrize("ranged", [False, True])
    async def test_t20_control_403_on_the_same_document_re_raises_the_original_error(self, ranged: bool) -> None:
        """An expired or skewed signature: not "document gone", or the vector-only
        re-index marks content missing and the heal forces a re-index."""
        bs = _bs(_lookup("old"))
        _envelopes(bs, {"old": _signed("https://blob/old")})
        original = SignedUrlReadError("Failed to retrieve record: status 403", 403, ranged=ranged)
        bs._record_from_signed_url = AsyncMock(side_effect=original)

        with pytest.raises(SignedUrlReadError) as raised:
            await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        assert raised.value is original
        assert not isinstance(raised.value, StorageDocumentNotFoundError)
        assert bs.get_document_id_by_virtual_record_id.await_count == 1

    async def test_other_statuses_on_a_fresh_url_do_not_look_up(self) -> None:
        bs = _bs(_lookup("new"))
        _envelopes(bs, {"old": _signed()})
        bs._record_from_signed_url = AsyncMock(
            side_effect=SignedUrlReadError("Failed to retrieve record: status 500", 500)
        )

        with pytest.raises(SignedUrlReadError):
            await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        bs.get_document_id_by_virtual_record_id.assert_not_awaited()

    async def test_a_403_on_a_cached_url_re_signs_and_does_not_look_up(self) -> None:
        bs = _bs(_lookup("new"))
        bs._cached_signed_url = AsyncMock(return_value="https://blob/cached")
        _envelopes(bs, {"old": _signed("https://blob/fresh")})
        seen: list[str] = []

        async def from_url(session, url, size, vrid, lease=None) -> dict:
            seen.append(url)
            if url.endswith("cached"):
                raise SignedUrlReadError("Failed to retrieve record: status 403", 403)
            return {"record_name": "old"}

        bs._record_from_signed_url = from_url

        record = await bs.get_record_from_storage(VRID, ORG, lookup_result=_lookup("old"))

        assert record == {"record_name": "old"}
        assert seen == ["https://blob/cached", "https://blob/fresh"]
        bs.get_document_id_by_virtual_record_id.assert_not_awaited()


class TestSignedUrlErrors:
    """The error types ``_record_from_signed_url`` raises, from a real response."""

    def _session(self, *statuses: int) -> MagicMock:
        responses = []
        for status in statuses:
            res = AsyncMock()
            res.status = status
            res.headers = {}
            res.__aenter__ = AsyncMock(return_value=res)
            res.__aexit__ = AsyncMock(return_value=False)
            responses.append(res)
        session = MagicMock()
        session.get = MagicMock(side_effect=responses)
        session.head = MagicMock(side_effect=responses)
        return session

    @pytest.mark.parametrize("status,retirable", [(403, True), (404, True), (500, False)])
    async def test_a_single_get_raises_a_typed_error(self, status: int, retirable: bool) -> None:
        bs = _bs()
        with pytest.raises(SignedUrlReadError) as raised:
            await bs._record_from_signed_url(self._session(status), "https://b/x", 10, VRID)
        assert raised.value.http_status == status
        assert raised.value.may_be_retired is retirable
        assert str(raised.value) == f"Failed to retrieve record: status {status}"

    @pytest.mark.parametrize("status", [403, 404])
    async def test_a_failed_ranged_download_is_a_retirable_error(self, status: int) -> None:
        bs = _bs()
        bs._download_with_range_requests = AsyncMock(side_effect=Exception(f"range {status}"))
        with pytest.raises(SignedUrlReadError) as raised:
            await bs._record_from_signed_url(self._session(status), "https://b/x", None, VRID)
        assert raised.value.ranged and raised.value.may_be_retired
        assert raised.value.http_status == status
        assert "Both parallel and fallback downloads failed" in str(raised.value)


class TestActualContentPath:
    async def test_a_404_on_a_replaced_document_reads_the_new_documents_path(self) -> None:
        bs = _bs([_lookup("old"), _lookup("new")])
        bs.graph_provider = MagicMock()
        paths = {"old": None, "new": f"{ORG}/PipesHub/a/b"}
        bs._get_current_document_path = AsyncMock(side_effect=lambda org, doc, strict=False: paths[doc])
        before = _count("served", "path_not_found")

        assert await bs.get_actual_content_path(ORG, VRID) == "a/b"
        assert _count("served", "path_not_found") == before + 1

    async def test_t12_control_the_same_document_is_none(self) -> None:
        bs = _bs([_lookup("old"), _lookup("old")])
        bs.graph_provider = MagicMock()
        bs._get_current_document_path = AsyncMock(return_value=None)

        assert await bs.get_actual_content_path(ORG, VRID) is None
        assert bs._get_current_document_path.await_count == 1

    async def test_a_failed_re_lookup_is_unavailable_not_none(self) -> None:
        bs = _bs([_lookup("old"), RuntimeError("graph down")])
        bs.graph_provider = MagicMock()
        bs._get_current_document_path = AsyncMock(return_value=None)

        with pytest.raises(StorageLookupUnavailableError):
            await bs.get_actual_content_path(ORG, VRID)
