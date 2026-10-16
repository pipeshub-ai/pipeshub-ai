"""Code files that get no LLM classification: tests, near-empty files, or all when off (SC-10)."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.parsers.code_parser.classification_policy import (
    code_classification_skip_reason,
)
from app.services.parsing.interface import ParseResult, ParserProvider


def _container(*names: str | None) -> BlocksContainer:
    blocks = [Block(index=i, type=BlockType.CODE, name=n, data={"text": "x"}, format=DataFormat.CODE)
              for i, n in enumerate(names)]
    blocks.append(Block(index=len(names), type=BlockType.RECORD_SUMMARY, name="a.py",
                        data={"text": "a.py"}, format=DataFormat.TXT))
    return BlocksContainer(blocks=blocks, block_groups=[])


@pytest.mark.parametrize(("path", "names", "skipped"), [
    ("src/billing.py", ("charge", "refund"), False),
    ("tests/test_billing.py", ("test_a", "test_b"), True),
    ("db/migrations/0001_init.py", ("up", "down"), True),
    ("src/settings.py", (None,), True),
    ("src/one.py", ("only", None), True),
])
def test_which_code_files_are_classified(path, names, skipped) -> None:
    assert (code_classification_skip_reason(path, _container(*names)) is not None) is skipped


def test_the_switch_turns_classification_off() -> None:
    with patch.dict(os.environ, {"CODE_FILE_CLASSIFICATION": "false"}):
        assert code_classification_skip_reason("src/billing.py", _container("a", "b"))


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "classified"), [("tests/test_billing.py", False),
                                                  ("src/billing.py", True)])
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_test_file_is_indexed_without_an_llm_call(path: str, classified: bool) -> None:
    from tests.unit.events.test_orchestrator_flow import (
        _make_event_processor,
        _project_code_blocks_mock,
    )

    parsing_client = MagicMock(circuit_open=False)
    parsing_client.parse = AsyncMock(return_value=ParseResult(
        block_container=_container("test_a", "test_b"), provider_used=ParserProvider.DEFAULT,
        metadata={}))
    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock()
    sink = MagicMock()
    sink.index = AsyncMock()
    sink.enrich = AsyncMock()
    sink.blob_storage.apply = AsyncMock()
    processor = MagicMock()
    processor.project_code_blocks_to_graph = _project_code_blocks_mock()
    ep = _make_event_processor(parsing_client=parsing_client, extraction_client=extraction_client,
                               sink_orchestrator=sink, processor=processor)

    async for _ in ep._orchestrate_via_services(
            "rec-1", "org-1", "vr-1", "test_billing.py", "text/x-python", "py", "NEW_RECORD",
            None, b"def test_a(): pass\n", is_code=True, file_path=path):
        pass

    assert extraction_client.classify.await_count == int(classified)
    sink.index.assert_awaited_once()
