"""A run whose parser was decided by the file path stores no identity: the graph record holds no path, so it could never
reproduce the key. A path that agrees with the name is tracked as before."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.events.dedup import DedupDecision
from app.events.processor import Processor
from app.modules.pipeline import parser_identity as pi
from tests.support.parser_identity_world import World


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    monkeypatch.delenv("USE_PARSING_SERVICE", raising=False)
    pi.end_run_identity()


def _every_handler_parses(world: World) -> None:
    for name in dir(Processor):
        if name.startswith("process_"):
            setattr(world.processor, name, MagicMock(side_effect=world._parse))


def test_the_identity_of_a_path_decided_run_is_the_key_of_the_parser_the_path_chose() -> None:
    args = ("application/octet-stream", "unknown", "Report.docx", "FILE")
    assert pi.parser_identity(*args, pipeline=pi.PIPELINE_IN_PROCESS, file_path="x/app.log") == "inproc:plain_text@1"
    assert pi.parser_identity(*args, pipeline=pi.PIPELINE_IN_PROCESS) == "inproc:docx@1"
    assert pi.parser_identity(*args, pipeline=pi.PIPELINE_IN_PROCESS, file_path="x/Report.docx") == "inproc:docx@1"


def test_the_graph_key_never_reads_a_path() -> None:
    record = {"mimeType": "application/octet-stream", "recordName": "Report.docx", "recordType": "FILE", "filePath": "x/app.log"}
    assert pi.graph_parser_key(record, {"extension": "unknown"}, service=False) == "docx@1"


@pytest.mark.asyncio
async def test_a_run_decided_by_its_path_through_the_event_path_stores_the_key_of_the_path() -> None:
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)

    await world.run(world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/app.log"))

    assert world.parses == 1
    assert world.state[pi.IDENTITY_FIELD] == "inproc:plain_text@1"


@pytest.mark.asyncio
async def test_a_run_whose_path_agrees_with_its_name_stores_the_identity() -> None:
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)

    await world.run(world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/Report.docx"))

    assert world.state[pi.IDENTITY_FIELD] == "inproc:docx@1"


@pytest.mark.asyncio
async def test_a_second_same_bytes_update_of_a_path_decided_copy_runs_in_full_and_adopts_nothing() -> None:
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)
    event = world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/app.log")
    world.files = {"_key": "rec-1", "extension": "unknown"}
    world.state.update({"mimeType": "application/octet-stream", "recordName": "Report.docx"})

    await world.run(event)
    assert world.parses == 1 and world.state[pi.IDENTITY_FIELD] == "inproc:plain_text@1"

    await world.run(event)

    assert world.parses == 2
    assert world.state[pi.IDENTITY_FIELD] == "inproc:plain_text@1"
    assert world.conditional_writes == []


@pytest.mark.asyncio
async def test_a_same_bytes_update_whose_path_agrees_with_the_name_skips_normally() -> None:
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)
    world.files = {"_key": "rec-1", "extension": "unknown"}
    world.state.update({"mimeType": "application/octet-stream", "recordName": "Report.docx"})
    event = world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/Report.docx")

    await world.run(event)
    assert world.parses == 1 and world.state[pi.IDENTITY_FIELD] == "inproc:docx@1"
    await world.run(event)

    assert world.parses == 1


def _path_decided_copy() -> dict:
    """A finished record whose copy was built by the plain-text reader because its path said so; its name says docx."""
    return {
        "_key": "rec-9", "mimeType": "application/octet-stream", "recordName": "Report.docx", "recordType": "FILE",
        "virtualRecordId": "v9", "lastIndexTimestamp": 7, pi.IDENTITY_FIELD: "inproc:plain_text@1", pi.IDENTITY_AT_FIELD: 7,
        pi.IDENTITY_VRID_FIELD: "v9", pi.IDENTITY_SOURCE_FIELD: pi.SOURCE_PARSED,
    }


@pytest.mark.asyncio
async def test_a_docx_with_the_same_bytes_is_not_attached_to_a_path_decided_copy() -> None:
    world = World()
    world.files = {"_key": "rec-1", "extension": "unknown"}
    doc = {"_key": "rec-1", "mimeType": "application/octet-stream", "recordName": "Report.docx", "recordType": "FILE"}

    assert await world.ep._twin_shares_parser(doc, _path_decided_copy()) is False


@pytest.mark.asyncio
async def test_a_record_whose_own_path_decides_never_attaches_even_when_the_name_keys_coincide() -> None:
    world = World()
    world.files = {"_key": "rec-1", "extension": "unknown"}
    doc = {"_key": "rec-1", "mimeType": "application/octet-stream", "recordName": "Report.docx", "recordType": "FILE"}
    twin = _path_decided_copy() | {pi.IDENTITY_FIELD: "inproc:docx@1"}  # its stored key equals the record's name key

    assert await world.ep._twin_shares_parser(doc, twin) is True
    pi.begin_run_identity(None, path_decided=True)
    try:
        assert await world.ep._twin_shares_parser(doc, twin) is False
    finally:
        pi.end_run_identity()
    assert pi.run_path_decided() is False


def test_the_queued_duplicates_of_a_path_decided_copy_stay_queued() -> None:
    source = _path_decided_copy()
    queued = {"_key": "q", "mimeType": "application/octet-stream", "recordName": "Other.docx", "recordType": "FILE"}
    assert pi.promotion_fields(source, queued, {"extension": "unknown"}, at=9, vrid="v9") is None
    assert pi.plan_promotions(source, [queued], {"q": {"extension": "unknown"}}, at=9, vrid="v9") == {}


@pytest.mark.asyncio
async def test_a_path_decided_record_never_skips_even_when_a_stored_key_equals_its_name_key() -> None:
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)
    world.files = {"_key": "rec-1", "extension": "unknown"}
    world.state.update({"mimeType": "application/octet-stream", "recordName": "Report.docx"})
    event = world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/app.log")
    await world.run(event)
    world.state.update({pi.IDENTITY_FIELD: "inproc:docx@1"})  # stored by a process that did not see the path
    parses = world.parses

    await world.run(event)

    assert world.parses == parses + 1


@pytest.mark.asyncio
async def test_a_text_record_is_not_attached_to_a_copy_the_path_made_by_the_source_parser() -> None:
    """text/plain named ``notes`` at ``src/x.py`` is read as source, which is not tracked; its copy must not read as text."""
    identity = pi.parser_identity("text/plain", None, "notes", "FILE", pipeline=pi.PIPELINE_IN_PROCESS, file_path="src/x.py")
    assert identity == f"inproc:{pi.UNTRACKED_KEY}"
    world = World()
    world.files = {"_key": "rec-1", "extension": "txt"}
    doc = {"_key": "rec-1", "mimeType": "text/plain", "recordName": "notes.txt", "recordType": "FILE"}
    twin = {
        "_key": "rec-9", "mimeType": "text/plain", "recordName": "notes", "recordType": "FILE", "virtualRecordId": "v9",
        "lastIndexTimestamp": 7, pi.IDENTITY_FIELD: identity, pi.IDENTITY_AT_FIELD: 7, pi.IDENTITY_VRID_FIELD: "v9",
        pi.IDENTITY_SOURCE_FIELD: pi.SOURCE_PARSED,
    }

    assert await world.ep._twin_shares_parser(doc, twin) is False
    queued = {"_key": "q", "mimeType": "text/plain", "recordName": "q.txt", "recordType": "FILE"}
    assert pi.promotion_fields(twin, queued, {"extension": "txt"}, at=9, vrid="v9") is None


@pytest.mark.asyncio
async def test_a_same_bytes_rename_from_source_to_markdown_runs_in_full_and_is_labelled_for_what_ran() -> None:
    world = World(node={"contentRev": "older", "mimeType": "text/plain", "recordName": "notes.py"}, file_doc={"_key": "rec-1", "extension": "py"})
    _every_handler_parses(world)
    await world.run(world.event(mime="text/plain", ext="py", name="notes.py"))
    assert world.parses == 1 and world.state[pi.IDENTITY_FIELD] == f"inproc:{pi.UNTRACKED_KEY}"
    world.state.update({"mimeType": "text/markdown", "recordName": "notes.md"})
    world.files = {"_key": "rec-1", "extension": "md"}

    await world.run(world.event(mime="text/markdown", ext="md", name="notes.md"))

    assert world.parses == 2
    assert world.state[pi.IDENTITY_FIELD] == "inproc:md@1"


@pytest.mark.asyncio
async def test_a_record_the_graph_reads_as_nothing_tracked_never_attaches_or_adopts() -> None:
    world = World()
    world.files = {"_key": "rec-1", "extension": "unknown"}
    doc = {"_key": "rec-1", "mimeType": "application/xml", "recordName": "a.b.docx", "recordType": "FILE"}
    assert pi.graph_parser_key(doc, world.files, service=False) is None
    twin = _path_decided_copy() | {pi.IDENTITY_FIELD: "inproc:docx@1", "mimeType": "application/xml", "recordName": "a.b.docx"}

    assert await world.ep._twin_shares_parser(doc, twin) is False


@pytest.mark.asyncio
async def test_the_event_path_flags_a_path_decided_record_so_that_it_never_attaches_to_a_twin() -> None:
    """Through ``on_event``: the dedup step sees a path-decided record and refuses a twin whose stored key equals its name key."""
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)
    world.files = {"_key": "rec-1", "extension": "unknown"}
    world.state.update({"mimeType": "application/octet-stream", "recordName": "Report.docx"})
    twin = _path_decided_copy() | {pi.IDENTITY_FIELD: "inproc:docx@1"}
    seen: list[bool] = []

    async def dedup(*_a: object, **_k: object) -> DedupDecision:
        seen.append(await world.ep._twin_shares_parser(world.state, twin))
        return DedupDecision()

    world.ep._check_duplicate_by_md5 = dedup  # type: ignore[method-assign]

    await world.run(world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/app.log"))
    await world.run(world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/Report.docx"))

    assert seen == [False, True]
