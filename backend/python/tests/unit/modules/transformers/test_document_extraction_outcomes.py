
"""DocumentExtraction's contract: SemanticMetadata out, None for "nothing to
classify", and a typed error when the model fails — which fails extraction,
never the already-searchable record."""

import asyncio
import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from botocore.exceptions import ClientError
from google.genai import errors as genai_errors

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


def _png_uri(fmt: str = "PNG", declared: str = "image/png") -> str:
    from PIL import Image

    buf = io.BytesIO()
    # Varied pixels, so half the file still holds a full header and the cut lands in the pixel data.
    Image.frombytes("RGB", (64, 64), bytes(i * 7 % 251 for i in range(64 * 64 * 3))).save(buf, format=fmt)
    return f"data:{declared};base64,{base64.b64encode(buf.getvalue()).decode()}"


def _blocks_with_image() -> list[Block]:
    return [
        *_blocks(),
        Block(index=1, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": _png_uri()}),
    ]


def _multimodal_role() -> AsyncMock:
    return AsyncMock(return_value=(MagicMock(), {"isMultimodal": True, "contextLength": 8000}))


def _openai_error(
    status: int, error: dict | None = None,
) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "http://x"))
    body = {"error": error or {
        "message": "The image data you provided does not represent a valid image.",
        "type": "invalid_request_error", "param": "input", "code": "invalid_value",
    }}
    error_type = {400: openai.BadRequestError, 401: openai.AuthenticationError, 404: openai.NotFoundError}[status]
    # The SDK's own message format, which carries the body.
    return error_type(f"Error code: {status} - {body}", response=response, body=body)


def _bedrock_validation_error() -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "ValidationException", "Message": "Could not process image"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "Converse",
    )


def _image_parts(call: object) -> list[dict]:
    message = call.args[1][-1]  # type: ignore[attr-defined]
    return [part for part in message.content if part["type"] == "image_url"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rejection",
    [
        lambda: _openai_error(400),
        _bedrock_validation_error,
        lambda: genai_errors.ClientError(400, {"error": {"message": "Unable to process input image."}}),
    ],
    ids=["openai", "bedrock", "gemini"],
)
async def test_rejected_images_are_dropped_and_the_text_classified(rejection) -> None:
    extraction = _extraction()
    invoke = AsyncMock(side_effect=[rejection(), _classification()])
    with patch(f"{_MODULE}.get_llm_for_role", _multimodal_role()), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection", invoke
    ):
        metadata = await extraction.classify(_blocks_with_image(), "org-1", ["Engineering"])
    assert metadata is not None and metadata.categories == ["Security"]
    first, second = invoke.call_args_list
    assert len(_image_parts(first)) == 1
    assert _image_parts(second) == []


@pytest.mark.asyncio
async def test_fallback_after_a_text_only_retry_does_not_resend_images() -> None:
    extraction = _extraction()
    fallback = AsyncMock(return_value=SemanticMetadata(summary="s"))
    with patch(f"{_MODULE}.get_llm_for_role", _multimodal_role()), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection",
        AsyncMock(side_effect=[_openai_error(400), None]),
    ), patch.object(extraction, "_fallback_summary", fallback):
        await extraction.classify(_blocks_with_image(), "org-1", ["Engineering"])
    sent = fallback.call_args.args[1]
    assert sent and all(part["type"] != "image_url" for part in sent)


@pytest.mark.asyncio
async def test_a_text_only_retry_that_also_fails_is_an_extraction_error() -> None:
    extraction = _extraction()
    invoke = AsyncMock(side_effect=[_openai_error(400), _openai_error(400)])
    with patch(f"{_MODULE}.get_llm_for_role", _multimodal_role()), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection", invoke
    ):
        with pytest.raises(ExtractionLLMError):
            await extraction.classify(_blocks_with_image(), "org-1", ["Engineering"])
    assert invoke.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "blocks", "error"),
    [
        (_llm_role, _blocks, lambda: _openai_error(400)),
        (_multimodal_role, _blocks_with_image, lambda: _openai_error(401)),
        (_multimodal_role, _blocks_with_image, lambda: _openai_error(404)),
        (_multimodal_role, _blocks_with_image, lambda: RuntimeError("boom")),
        (
            _multimodal_role, _blocks_with_image,
            lambda: _openai_error(400, {
                "message": "Unsupported parameter: 'response_format' is not supported with this model.",
                "type": "invalid_request_error", "param": "response_format", "code": "unsupported_parameter",
            }),
        ),
        (
            _multimodal_role, _blocks_with_image,
            lambda: _openai_error(400, {
                "message": "The image was filtered due to the prompt triggering Azure OpenAI's content management policy.",
                "type": None, "param": "prompt", "code": "content_filter",
            }),
        ),
        (
            _multimodal_role, _blocks_with_image,
            lambda: _openai_error(400, {
                "message": "Budget has been exceeded for this key; image requests are paused.",
                "type": "budget_exceeded", "param": None, "code": "400",
            }),
        ),
        (_multimodal_role, lambda: _blocks_with_image()[1:], lambda: _openai_error(400)),
    ],
    ids=[
        "no-images", "auth", "model-not-found", "unclassified",
        "unsupported-parameter", "content-filter", "gateway-budget", "images-only",
    ],
)
async def test_other_failures_are_not_retried_without_images(role, blocks, error) -> None:
    extraction = _extraction()
    invoke = AsyncMock(side_effect=error())
    with patch(f"{_MODULE}.get_llm_for_role", role()), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection", invoke
    ):
        with pytest.raises(ExtractionLLMError):
            await extraction.classify(blocks(), "org-1", ["Engineering"])
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_outage_with_images_is_unavailable_not_retried_without_them() -> None:
    extraction = _extraction()
    invoke = AsyncMock(side_effect=LLMUnavailableError("down"))
    with patch(f"{_MODULE}.get_llm_for_role", _multimodal_role()), patch(
        f"{_MODULE}.invoke_with_structured_output_and_reflection", invoke
    ):
        with pytest.raises(LLMUnavailableError):
            await extraction.classify(_blocks_with_image(), "org-1", ["Engineering"])
    invoke.assert_awaited_once()


def test_a_truncated_image_is_skipped() -> None:
    from app.modules.transformers.document_extraction import _downscale_base64_image

    header, payload = _png_uri().split(",", 1)
    raw = base64.b64decode(payload)
    truncated = base64.b64encode(raw[: len(raw) // 2]).decode()
    assert _downscale_base64_image(f"{header},{truncated}") is None


def test_an_image_declared_with_the_wrong_type_is_relabelled() -> None:
    blocks = [
        Block(
            index=0, type=BlockType.IMAGE, format=DataFormat.BASE64,
            data={"uri": _png_uri(fmt="JPEG", declared="image/png")},
        )
    ]
    content = _extraction()._prepare_content(blocks, is_multimodal_llm=True, context_length=8000)
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
