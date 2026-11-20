"""``RECORD_PARSER_IDENTITY_WRITE`` off (the default): nothing is written, nothing is judged by an identity that does not exist.

The record schema accepts the fields first and the writes follow one release later: a process still on the previous closed
schema re-applies it at start and refuses a write of a field it does not know.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.pipeline import parser_identity as pi
from tests.support.parser_identity_world import T0, World


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECORD_PARSER_IDENTITY_WRITE", raising=False)
    monkeypatch.delenv("USE_PARSING_SERVICE", raising=False)
    pi.end_run_identity()


def test_the_flag_is_off_by_default_and_reads_the_usual_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pi.identity_write_enabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", value)
        assert pi.identity_write_enabled() is True
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "off")
    assert pi.identity_write_enabled() is False


def test_no_builder_returns_a_field_while_the_flag_is_off() -> None:
    assert pi.indexed_parser_fields("inproc:txt@1", pi.SOURCE_PARSED, at=1, vrid="v") == {}
    twin = {
        "indexedParser": "inproc:txt@1", "indexedParserAt": 5, "indexedParserVrid": "v", "lastIndexTimestamp": 5,
        "virtualRecordId": "v",
    }
    assert pi.copied_identity_fields(twin, at=9, vrid="v") == {}
    assert pi.promotion_fields(twin, {"mimeType": "text/markdown", "recordName": "a.md", "recordType": "FILE"}, None, at=9, vrid="v") == {}


@pytest.mark.asyncio
async def test_a_record_without_an_identity_skips_as_before_and_is_not_read_or_adopted() -> None:
    world = World()

    await world.run(world.event())

    assert world.parses == 0
    world.ingress.redrive_unchanged.assert_awaited_once()
    assert world.conditional_writes == []
    assert not [c for c in world.graph.get_document.await_args_list if c.args[1] == "files"]


@pytest.mark.asyncio
async def test_a_run_writes_no_identity() -> None:
    world = World(node={"contentRev": "older"})

    await world.run(world.event())

    assert world.parses == 1
    assert not {*pi.IDENTITY_FIELDS} & world.state.keys()


@pytest.mark.asyncio
async def test_an_identity_written_earlier_is_still_compared() -> None:
    """Switched off after a time on: the records that carry one keep the protection (a read, not a write)."""
    world = World()
    world.identified("inproc:txt@1")
    world.renamed_to_markdown()

    await world.run(world.event(mime="text/markdown", ext="md", name="notes.md"))

    assert world.parses == 1


@pytest.mark.asyncio
async def test_a_twin_is_attached_whatever_its_type() -> None:
    world = World(node={"mimeType": "text/markdown", "recordName": "b.md"}, file_doc={"_key": "rec-1", "extension": "md"})
    twin = {"_key": "rec-9", "mimeType": "text/plain", "recordName": "a.txt", "recordType": "FILE", "virtualRecordId": "vx"}
    world.ep._files_document = AsyncMock(side_effect=AssertionError("no read while the identity is off"))

    assert await world.ep._twin_shares_parser(world.state, twin) is True


def test_the_sink_write_carries_nothing_while_off() -> None:
    fields: dict[str, Any] = {"lastIndexTimestamp": T0}
    fields.update(pi.indexed_parser_fields("inproc:txt@1", pi.SOURCE_PARSED, at=T0, vrid="v"))
    assert fields == {"lastIndexTimestamp": T0}
