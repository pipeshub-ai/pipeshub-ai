"""A run whose parser was decided by the file path stores no identity: the graph record holds no path, so it could never
reproduce the key. A path that agrees with the name is tracked as before."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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


def test_a_key_built_with_a_deciding_path_is_not_recorded() -> None:
    args = ("application/octet-stream", "unknown", "Report.docx", "FILE")
    assert pi.parser_identity(*args, pipeline=pi.PIPELINE_IN_PROCESS, file_path="x/app.log") is None
    assert pi.parser_identity(*args, pipeline=pi.PIPELINE_IN_PROCESS) == "inproc:docx@1"
    assert pi.parser_identity(*args, pipeline=pi.PIPELINE_IN_PROCESS, file_path="x/Report.docx") == "inproc:docx@1"


def test_the_graph_key_never_reads_a_path() -> None:
    record = {"mimeType": "application/octet-stream", "recordName": "Report.docx", "recordType": "FILE", "filePath": "x/app.log"}
    assert pi.graph_parser_key(record, {"extension": "unknown"}, service=False) == "docx@1"


@pytest.mark.asyncio
async def test_a_run_decided_by_its_path_through_the_event_path_stores_no_identity() -> None:
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)

    await world.run(world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/app.log"))

    assert world.parses == 1
    assert pi.IDENTITY_FIELD not in world.state


@pytest.mark.asyncio
async def test_a_run_whose_path_agrees_with_its_name_stores_the_identity() -> None:
    world = World(node={"contentRev": "older"})
    _every_handler_parses(world)

    await world.run(world.event(mime="application/octet-stream", ext="unknown", name="Report.docx", filePath="x/Report.docx"))

    assert world.state[pi.IDENTITY_FIELD] == "inproc:docx@1"
