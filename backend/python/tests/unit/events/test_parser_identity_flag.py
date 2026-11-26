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
async def test_a_stored_identity_is_ignored_while_the_setting_is_off() -> None:
    """Off is the behaviour of the same bytes alone: nothing keeps a stored identity current, so it must not decide."""
    world = World()
    world.identified("inproc:txt@1")
    world.renamed_to_markdown()

    await world.run(world.event(mime="text/markdown", ext="md", name="notes.md"))

    assert world.parses == 0
    assert world.conditional_writes == []


@pytest.mark.asyncio
async def test_the_setting_turned_off_after_it_was_on_does_not_rerun_every_same_bytes_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rollback: the identity was written while on; a full run with the setting off rebuilds the copy (the identity goes stale)."""
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    world = World()
    world.identified("inproc:txt@1")
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "false")
    world.state["lastIndexTimestamp"] += 5000

    for _ in range(4):
        await world.run(world.event())

    assert world.parses == 0


@pytest.mark.asyncio
async def test_a_forced_run_with_the_setting_off_then_same_bytes_updates_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    world = World()
    world.identified("inproc:txt@1")
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "false")
    await world.run(world.event(forceReindex=True))
    forced = world.parses

    for _ in range(3):
        await world.run(world.event())

    assert world.parses == forced


@pytest.mark.asyncio
async def test_the_setting_back_on_costs_one_full_run_per_stale_record_then_heals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    world = World()
    world.identified("inproc:txt@1")
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "false")
    await world.run(world.event(forceReindex=True))
    forced = world.parses
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")

    for _ in range(4):
        await world.run(world.event())

    assert world.parses - forced == 1
    assert pi.known_identity(world.state) == "inproc:txt@1"


@pytest.mark.asyncio
async def test_a_fleet_mixed_on_the_setting_runs_a_stale_record_in_full_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One process has the setting on, another off, the same record: the process that is off never re-arms the stale state for good."""
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    world = World()
    world.identified("inproc:txt@1")
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "false")
    await world.run(world.event(forceReindex=True))  # a process that is off rebuilds the copy
    forced = world.parses

    for i in range(8):
        monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true" if i % 2 == 0 else "false")
        await world.run(world.event())

    assert world.parses - forced <= 1


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
