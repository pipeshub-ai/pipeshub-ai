"""Code files that get no LLM classification: tests, near-empty files, or all when off (SC-10)."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.parsers.code_parser.classification_policy import (
    code_classification_skip_reason,
)


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
async def test_a_test_file_is_classified_without_an_llm_call(path: str, classified: bool) -> None:
    """The classify stage and the Extraction service both end in DocumentExtraction.classify;
    a skipped code file returns None there, which the stage records as skipped."""
    import logging

    from app.models.blocks import CodeMetadata
    from app.modules.transformers.document_extraction import DocumentExtraction

    container = _container("test_a", "test_b")
    summary = container.blocks[-1]
    summary.data = {"text": path, "file_path": path}
    summary.code_metadata = CodeMetadata(kind="file_summary", language="python")
    extraction = DocumentExtraction(logging.getLogger("test"), MagicMock(), MagicMock())
    extraction.process_code_document = AsyncMock(return_value=MagicMock())
    extraction._classify_blocks = AsyncMock()

    result = await extraction.classify(container.blocks, "org-1", [], "billing.py", "CODE_FILE")

    assert extraction.process_code_document.await_count == int(classified)
    assert (result is None) is not classified
    extraction._classify_blocks.assert_not_awaited()
