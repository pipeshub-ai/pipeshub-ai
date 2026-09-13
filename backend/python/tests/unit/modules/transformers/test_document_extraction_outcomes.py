
"""DocumentExtraction's contract: SemanticMetadata out, None for "nothing to
classify", and a typed error when the model fails — which fails extraction,
never the already-searchable record."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlockType, DataFormat, SemanticMetadata
from app.modules.transformers.document_extraction import (
    DocumentClassification,
    DocumentExtraction,
    ExtractionLLMError,
    SubCategories,
)
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError

_MODULE = "app.modules.transformers.document_extraction"


def _extraction() -> DocumentExtraction:
    graph_provider = AsyncMock()
    graph_provider.get_departments = AsyncMock(return_value=["Engineering"])
    return DocumentExtraction(MagicMock(), graph_provider, AsyncMock())


def _blocks(text: str = "The VPN rollout plan for Q3.") -> list[Block]:
    return [Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data=text)]


def _classification(**overrides: object) -> DocumentClassification:
    fields: dict[str, object] = {
        "departments": ["Engineering"],
        "category": "Security",
        "subcategories": SubCategories(level1="Network", level2="", level3=""),
        "languages": ["English"],
        "sentiment": "Neutral",
        "confidence_score": 0.9,
        "topics": ["VPN"],
        "summary": "A VPN plan.",
    }
    fields.update(overrides)
    return DocumentClassification(**fields)


def _llm_role() -> AsyncMock:
    return AsyncMock(return_value=(MagicMock(), {"isMultimodal": False, "contextLength": 8000}))


class TestToSemanticMetadata:
    def test_blank_names_are_unknown_not_empty_strings(self) -> None:
        metadata = DocumentExtraction.to_semantic_metadata(
            _classification(category="  ", departments=["Engineering", " "], topics=["VPN", ""])
        )
        assert metadata.categories == []
        assert metadata.departments == ["Engineering"]
        assert metadata.topics == ["VPN"]
        assert metadata.sub_category_level_1 == "Network"
        assert metadata.sub_category_level_2 is None


class TestRenderPrompt:
    def test_placeholders_are_filled_and_braces_are_single(self) -> None:
        prompt = DocumentExtraction.render_prompt(["Engineering", "Legal {Contracts}"])
        assert "{department_list}" not in prompt
        assert "{{" not in prompt and "}}" not in prompt
        assert '"Legal {Contracts}"' in prompt
        assert '"topics": string[],' in prompt


@pytest.mark.asyncio
async def test_structured_result_is_returned_as_semantic_metadata() -> None:
    extraction = _extraction()
    with patch(f"{_MODULE}.get_llm_for_role", _llm_role()), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection",
        AsyncMock(return_value=_classification()),
    ):
        metadata = await extraction.extract_metadata(_blocks(), "org-1")
    assert isinstance(metadata, SemanticMetadata)
    assert metadata.categories == ["Security"]


@pytest.mark.asyncio
async def test_no_content_returns_none() -> None:
    extraction = _extraction()
    with patch(f"{_MODULE}.get_llm_for_role", _llm_role()):
        assert await extraction.extract_metadata([], "org-1") is None


@pytest.mark.asyncio
async def test_model_failure_raises_a_typed_error() -> None:
    extraction = _extraction()
    with patch(f"{_MODULE}.get_llm_for_role", _llm_role()), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection", AsyncMock(return_value=None)
    ), patch.object(extraction, "_fallback_summary", AsyncMock(return_value=None)):
        with pytest.raises(ExtractionLLMError):
            await extraction.classify(_blocks(), "org-1", ["Engineering"])


@pytest.mark.asyncio
async def test_fallback_summary_leaves_every_other_field_unknown() -> None:
    extraction = _extraction()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="Just a summary."))
    metadata = await extraction._fallback_summary(llm, [{"type": "text", "text": "doc"}])
    assert metadata is not None
    assert metadata.summary == "Just a summary."
    assert metadata.departments is None
    assert metadata.topics is None
    assert metadata.languages is None
    assert metadata.categories == []


@pytest.mark.asyncio
async def test_fallback_summary_keeps_plain_string_parts_as_text() -> None:
    extraction = _extraction()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="Summary."))
    metadata = await extraction._fallback_summary(
        llm,
        ["loose text", {"type": "text", "text": "doc"}, {"type": "audio", "data": "x"}]
    )
    assert metadata is not None
    sent = llm.ainvoke.call_args.args[0][0].content
    assert {"type": "text", "text": "loose text"} in sent
    assert {"type": "text", "text": "doc"} in sent
    assert all(part.get("type") != "audio" for part in sent)


@pytest.mark.asyncio
async def test_apply_turns_a_model_failure_into_no_metadata() -> None:
    extraction = _extraction()
    record = MagicMock()
    record.block_containers.blocks = _blocks()
    with patch.object(
        extraction, "process_document", AsyncMock(side_effect=ExtractionLLMError("down"))
    ):
        await extraction.apply(MagicMock(record=record))
    assert record.semantic_metadata is None


@pytest.mark.asyncio
async def test_no_llm_configured_skips_extraction_instead_of_failing_it() -> None:
    extraction = _extraction()
    record = MagicMock()
    record.block_containers.blocks = _blocks()
    ctx = MagicMock(record=record, extraction_skip_reason=None)
    with patch(
        f"{_MODULE}.get_llm_for_role",
        AsyncMock(side_effect=LLMNotConfiguredError("No LLM is configured for this organization")),
    ):
        await extraction.apply(ctx)
    assert record.semantic_metadata is None
    assert ctx.extraction_skip_reason == "No LLM is configured for this organization"


@pytest.mark.asyncio
async def test_concurrent_classifications_each_use_their_own_model() -> None:
    """One extractor serves several orgs at once; a shared model attribute let them swap."""
    extraction = _extraction()
    first, second = MagicMock(), MagicMock()
    first.ainvoke = AsyncMock(return_value=MagicMock(content="A"))
    second.ainvoke = AsyncMock(return_value=MagicMock(content="B"))
    models = [first, second]

    async def role(*_args: object, **_kwargs: object) -> tuple[MagicMock, dict[str, object]]:
        return models.pop(0), {"isMultimodal": False, "contextLength": 4096}

    async def no_structured_output(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0)  # the other classification resolves its model meanwhile

    with patch(f"{_MODULE}.get_llm_for_role", role), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection", no_structured_output
    ):
        a, b = await asyncio.gather(
            extraction.classify(_blocks(), "org-a", ["Engineering"]),
            extraction.classify(_blocks(), "org-b", ["Engineering"]),
        )
    assert a is not None and b is not None
    assert (a.summary, b.summary) == ("A", "B")
    first.ainvoke.assert_awaited_once()
    second.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_provider_outage_is_unavailable_not_a_bad_document() -> None:
    extraction = _extraction()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ConnectionRefusedError("refused"))

    async def role(*_args: object, **_kwargs: object) -> tuple[MagicMock, dict[str, object]]:
        return llm, {"isMultimodal": False, "contextLength": 4096}

    with patch(f"{_MODULE}.get_llm_for_role", role), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection", AsyncMock(return_value=None)
    ):
        with pytest.raises(LLMUnavailableError):
            await extraction.classify(_blocks(), "org-1", ["Engineering"])
