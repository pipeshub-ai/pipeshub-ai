"""Which QUEUED duplicates a finished record's copy is theirs to take, and the identity they take with it (the pure rule the three stores use)."""
from __future__ import annotations

import pytest

from app.modules.pipeline import parser_identity as pi


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")


def source(identity: str | None = "inproc:txt@1", *, source_label: str = "parsed") -> dict:
    doc = {"virtualRecordId": "v", "lastIndexTimestamp": 5}
    if identity:
        doc.update({pi.IDENTITY_FIELD: identity, pi.IDENTITY_AT_FIELD: 5, pi.IDENTITY_VRID_FIELD: "v", pi.IDENTITY_SOURCE_FIELD: source_label})
    return doc


TXT = {"_key": "q", "mimeType": "text/plain", "recordName": "q.txt", "recordType": "FILE"}
MD = {"_key": "q", "mimeType": "text/markdown", "recordName": "q.md", "recordType": "FILE"}
FILES = {"q": {"extension": "txt"}}
MD_FILES = {"q": {"extension": "md"}}


def test_an_equal_identity_is_promoted_with_the_batchs_own_timestamp() -> None:
    plan = pi.plan_promotions(source(), [TXT], FILES, at=99, vrid="v")
    assert plan == {"q": {
        pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 99, pi.IDENTITY_VRID_FIELD: "v", pi.IDENTITY_SOURCE_FIELD: pi.SOURCE_TWIN,
    }}


def test_a_different_identity_stays_queued() -> None:
    assert pi.plan_promotions(source(), [MD], MD_FILES, at=99, vrid="v") == {}


def test_the_pipeline_does_not_count() -> None:
    assert "q" in pi.plan_promotions(source("svc:txt@1"), [TXT], FILES, at=99, vrid="v")


def test_the_extension_comes_from_the_files_document() -> None:
    octet = {"_key": "q", "mimeType": "application/octet-stream", "recordName": "q", "recordType": "FILE"}
    assert "q" in pi.plan_promotions(source(), [octet], {"q": {"extension": "txt"}}, at=1, vrid="v")
    assert pi.plan_promotions(source(), [octet], {"q": {"extension": "md"}}, at=1, vrid="v") == {}


def test_an_adopted_label_stays_adopted() -> None:
    plan = pi.plan_promotions(source(source_label=pi.SOURCE_ADOPTED), [TXT], FILES, at=1, vrid="v")
    assert plan["q"][pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_ADOPTED


@pytest.mark.parametrize("src", [source(None), {**source(), "lastIndexTimestamp": 6}, {**source(), pi.IDENTITY_VRID_FIELD: "w"}], ids=["absent", "stale", "other-copy"])
def test_a_source_with_no_known_identity_promotes_as_before_and_writes_none(src: dict) -> None:
    assert pi.plan_promotions(src, [MD], MD_FILES, at=1, vrid="v") == {"q": {}}


def test_a_duplicate_that_is_not_tracked_is_promoted_as_before() -> None:
    blob = {"_key": "q", "mimeType": "application/octet-stream", "recordName": "q", "recordType": "FILE"}
    assert pi.plan_promotions(source(), [blob], {"q": None}, at=1, vrid="v") == {"q": {}}


@pytest.mark.asyncio
async def test_only_a_completed_promotion_is_planned() -> None:
    async def fetch(_key: str) -> dict:
        return {"extension": "md"}

    assert await pi.promotion_plan(source(), [MD], "FAILED", "v", 1, fetch) is None
    assert await pi.promotion_plan(source(), [MD], "COMPLETED", "v", 1, fetch) == {}
