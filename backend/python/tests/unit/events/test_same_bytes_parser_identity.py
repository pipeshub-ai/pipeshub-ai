"""The same-bytes skip also asks which parser built the indexed copy (the record's type may have changed under the same bytes).

A file renamed from ``.txt`` to ``.md`` keeps its bytes. An update that carries the new name used to be skipped against a copy
built as plain text. Now the skip compares the parser recorded with the copy with the one the record is read by in the graph
(its ``records`` node and its ``files`` document), through the one derivation that stored it.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.pipeline import parser_identity as pi
from app.services.messaging.config import IndexingEvent
from tests.support.parser_identity_world import BYTES, REV, T0, VRID, World

DONE = [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    monkeypatch.delenv("USE_PARSING_SERVICE", raising=False)
    pi.end_run_identity()


def _identity_fields(state: dict[str, Any]) -> dict[str, Any]:
    return {k: state.get(k) for k in pi.IDENTITY_FIELDS if k in state}


@pytest.mark.asyncio
async def test_a_type_change_queued_behind_an_update_of_the_same_bytes_runs_in_full() -> None:
    """The case this closes: e1 carried text/plain and parsed as text; the record was renamed to .md before e2 ran."""
    world = World()
    world.identified("inproc:txt@1")
    world.renamed_to_markdown()

    events = await world.run(world.event(mime="text/markdown", ext="md", name="notes.md"))

    assert events[-1] is IndexingEvent.INDEXING_COMPLETE
    assert world.parses == 1
    world.processor.process_md_document.assert_called_once()
    world.ingress.redrive_unchanged.assert_not_awaited()
    assert world.state[pi.IDENTITY_FIELD] == "inproc:md@1"
    assert world.state[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_PARSED
    assert world.state[pi.IDENTITY_AT_FIELD] == world.state["lastIndexTimestamp"]


@pytest.mark.asyncio
async def test_the_next_same_bytes_update_after_that_run_is_skipped() -> None:
    world = World()
    world.identified("inproc:txt@1")
    world.renamed_to_markdown()
    event = world.event(mime="text/markdown", ext="md", name="notes.md")
    await world.run(event)

    await world.run(event)

    assert world.parses == 1
    world.ingress.redrive_unchanged.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_same_bytes_of_the_same_parser_are_skipped_without_a_write() -> None:
    world = World()
    world.identified("inproc:txt@1")

    events = await world.run(world.event())

    assert events == DONE
    assert world.parses == 0
    world.ingress.redrive_unchanged.assert_awaited_once()
    world.graph.update_node.assert_not_awaited()
    assert world.conditional_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime", "ext", "name", "stored"),
    [
        ("text/html", "htm", "a.htm", "inproc:html@1"),
        ("text/html", "html", "a.html", "svc:html@1"),
        ("text/plain; charset=utf-8", "txt", "a.txt", "inproc:txt@1"),
        ("application/octet-stream", "txt", "a.txt", "inproc:txt@1"),
    ],
)
async def test_spellings_of_one_route_skip(mime: str, ext: str, name: str, stored: str) -> None:
    """The octet-stream row is the one the graph's records node alone cannot answer: the extension is on the files document."""
    world = World(node={"mimeType": mime, "recordName": name}, file_doc={"_key": "rec-1", "extension": ext})
    world.identified(stored)

    await world.run(world.event(mime=mime, ext=ext, name=name))

    assert world.parses == 0
    world.ingress.redrive_unchanged.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_replayed_older_event_is_judged_by_the_graph_not_its_payload() -> None:
    """e1 (text/plain) was dead-lettered, e2 (markdown) completed, e1 is replayed: it is skipped and the copy stays markdown."""
    world = World()
    world.renamed_to_markdown()
    world.identified("inproc:md@1")

    events = await world.run(world.event(mime="text/plain", ext="txt", name="notes.txt"))

    assert events == DONE
    assert world.parses == 0
    assert world.state[pi.IDENTITY_FIELD] == "inproc:md@1"


@pytest.mark.asyncio
async def test_an_absent_identity_skips_and_is_adopted_once() -> None:
    world = World()

    await world.run(world.event())
    await world.run(world.event())

    assert world.parses == 0
    assert len(world.conditional_writes) == 1
    assert _identity_fields(world.state) == {
        pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: T0, pi.IDENTITY_VRID_FIELD: VRID,
        pi.IDENTITY_SOURCE_FIELD: pi.SOURCE_ADOPTED,
    }


@pytest.mark.asyncio
async def test_adoption_never_fails_the_delivery_and_is_refused_when_the_record_moved() -> None:
    world = World()
    world.graph.update_node_fields_if = AsyncMock(side_effect=RuntimeError("down"))
    assert await world.run(world.event()) == DONE

    world = World()
    stale_read = dict(world.state, lastIndexTimestamp=T0 - 5)  # the record was re-indexed after the skip read it
    await world.ep._adopt_parser_identity(stale_read, "rec-1", "inproc:txt@1")
    assert pi.IDENTITY_FIELD not in world.state

    moved_rev = dict(world.state, contentRev="another")
    await world.ep._adopt_parser_identity(moved_rev, "rec-1", "inproc:txt@1")
    assert pi.IDENTITY_FIELD not in world.state


@pytest.mark.asyncio
async def test_a_stale_identity_runs_in_full_once_and_is_never_adopted() -> None:
    """The copy was rebuilt after the parse the identity describes (a writer that stores none): it labels nothing."""
    world = World()
    world.identified("inproc:txt@1")
    world.state["lastIndexTimestamp"] = T0 + 500

    await world.run(world.event())

    assert world.parses == 1
    assert world.conditional_writes == []
    assert world.state[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_PARSED
    assert world.state[pi.IDENTITY_AT_FIELD] == world.state["lastIndexTimestamp"]


@pytest.mark.asyncio
async def test_an_identity_written_with_another_copy_is_not_known() -> None:
    """A writer that moves the record to another copy (a re-point by an older process) leaves the timestamp as it was."""
    world = World()
    world.identified("inproc:txt@1")
    world.state[pi.IDENTITY_VRID_FIELD] = "an-older-copy"

    await world.run(world.event())

    assert world.parses == 1


@pytest.mark.asyncio
async def test_the_run_stores_the_route_that_parsed_not_the_one_the_graph_has_now() -> None:
    """Payload text/plain, graph markdown (renamed meanwhile), new bytes: the copy is plain text, and says so."""
    world = World(node={"contentRev": "older"})
    world.renamed_to_markdown()

    await world.run(world.event(mime="text/plain", ext="txt", name="notes.txt"))

    assert world.parses == 1
    assert world.state[pi.IDENTITY_FIELD] == "inproc:txt@1"
    # ... which the next update of the markdown record does not accept
    world.parses = 0
    await world.run(world.event(mime="text/markdown", ext="md", name="notes.md"))
    assert world.parses == 1
    assert world.state[pi.IDENTITY_FIELD] == "inproc:md@1"


@pytest.mark.asyncio
async def test_what_is_not_tracked_stores_nothing_and_skips_as_before() -> None:
    world = World(
        node={"mimeType": "application/octet-stream", "recordName": "blob"},
        file_doc={"_key": "rec-1", "extension": "unknown"},
    )

    await world.run(world.event(mime="application/octet-stream", ext="unknown", name="blob"))

    assert world.parses == 0
    assert world.conditional_writes == []
    assert pi.IDENTITY_FIELD not in world.state


@pytest.mark.asyncio
async def test_a_copy_a_tracked_parser_built_is_not_skipped_for_a_record_that_is_source_now() -> None:
    from unittest.mock import MagicMock

    from app.events.processor import Processor

    world = World(node={"mimeType": "text/plain", "recordName": "notes.py"}, file_doc={"_key": "rec-1", "extension": "py"})
    for name in dir(Processor):
        if name.startswith("process_"):
            setattr(world.processor, name, MagicMock(side_effect=world._parse))
    world.identified("inproc:txt@1")

    await world.run(world.event(mime="text/plain", ext="py", name="notes.py"))

    assert world.parses == 1
    assert world.state[pi.IDENTITY_FIELD] == f"inproc:{pi.UNTRACKED_KEY}"


@pytest.mark.asyncio
async def test_a_copy_an_untracked_parser_built_is_skipped_for_a_record_nothing_tracked_reads() -> None:
    world = World(node={"mimeType": "text/plain", "recordName": "notes.py"}, file_doc={"_key": "rec-1", "extension": "py"})
    world.identified(f"inproc:{pi.UNTRACKED_KEY}")

    await world.run(world.event(mime="text/plain", ext="py", name="notes.py"))

    assert world.parses == 0


@pytest.mark.asyncio
async def test_a_revision_bump_refuses_one_skip_and_then_skips_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.file_types import Route

    world = World()
    world.identified("inproc:txt@1")
    monkeypatch.setitem(pi.PARSER_ROUTE_REV, Route.TXT, 2)

    await world.run(world.event())
    assert world.parses == 1
    assert world.state[pi.IDENTITY_FIELD] == "inproc:txt@2"

    await world.run(world.event())
    assert world.parses == 1


@pytest.mark.asyncio
async def test_the_pipeline_is_stored_but_not_compared() -> None:
    """A fleet mixed on USE_PARSING_SERVICE does not re-run the same bytes back and forth."""
    world = World()
    world.identified("svc:txt@1")

    await world.run(world.event())

    assert world.parses == 0


@pytest.mark.asyncio
async def test_a_graph_read_that_fails_runs_in_full() -> None:
    world = World()
    world.identified("inproc:txt@1")
    real_get = world.graph.get_document.side_effect

    async def get(key: str, collection: str, **kw: object) -> dict[str, Any] | None:
        if collection == "files":
            raise RuntimeError("down")
        return await real_get(key, collection, **kw)

    world.graph.get_document = AsyncMock(side_effect=get)

    await world.run(world.event())

    assert world.parses == 1


@pytest.mark.asyncio
async def test_a_forced_reindex_is_never_skipped_whatever_the_identity() -> None:
    world = World()
    world.identified("inproc:txt@1")

    await world.run(world.event(forceReindex=True))

    assert world.parses == 1


def test_the_identity_of_the_run_is_the_payloads_and_is_reset_per_event() -> None:
    assert pi.run_identity() is None
    pi.begin_run_identity("inproc:txt@1")
    assert pi.run_identity() == "inproc:txt@1"
    pi.end_run_identity()
    assert pi.run_identity() is None


def test_known_identity_needs_the_timestamp_and_the_copy_it_was_written_with() -> None:
    base = {
        "indexedParser": "inproc:txt@1", "indexedParserAt": 5, "indexedParserVrid": "v", "lastIndexTimestamp": 5,
        "virtualRecordId": "v",
    }
    assert pi.known_identity(base) == "inproc:txt@1"
    assert pi.known_identity({**base, "lastIndexTimestamp": 6}) is None
    assert pi.known_identity({**base, "virtualRecordId": "w"}) is None
    assert pi.known_identity({**base, "indexedParserVrid": None}) is None
    assert pi.known_identity({**base, "indexedParser": None}) is None
    assert pi.known_identity({**base, "indexedParserAt": True, "lastIndexTimestamp": True}) is None
    assert REV and BYTES
