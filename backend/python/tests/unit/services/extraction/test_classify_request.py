"""The classify request carries only what the extraction service reads.

The service-side pin: whatever the indexing side leaves out, the content
``DocumentExtraction._prepare_content`` builds from the request must equal
what it builds from the whole container.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import random
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.routes.extraction import ClassifyRequest
from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.transformers.document_extraction import (
    _MAX_CHARS_PER_TOKEN,
    DocumentExtraction,
    _token_floor,
    classify_request_blocks,
)
from app.services.extraction.client import ExtractionClient

# Strings tiktoken packs into few tokens per character, plus ones that make
# it fall back to the len // 4 heuristic (a special token raises).
_DENSE = [
    "=" * 4000,
    " " * 4000,
    "-" * 3000 + "\n" * 500,
    "a" * 5000,
    "😀" * 700,
    "東京都" * 900,
    "<|endoftext|>" * 50,
    " " * 2000,
]


def _png_uri() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _container(n_text: int = 400, seed: int = 7) -> BlocksContainer:
    rng = random.Random(seed)
    words = "index block vector graph parse token retry the of and".split()
    png = _png_uri()
    blocks: list[Block] = []

    def add(**kw) -> None:
        blocks.append(Block(index=len(blocks), parent_index=0, **kw))

    add(type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": png})
    add(type=BlockType.RECORD_SUMMARY, format=DataFormat.TXT, data="ignored by the service")
    add(type=BlockType.TEXT, format=DataFormat.TXT, data="")
    add(type=BlockType.IMAGE, format=DataFormat.BASE64, data=None)
    add(type=BlockType.TABLE_ROW, format=DataFormat.JSON,
        data={"row_natural_language_text": "Q3 revenue was 12M", "row_number": 1})
    add(type=BlockType.CODE, format=DataFormat.CODE, data={"text": "def f():\n    return 1\n"})
    add(type=BlockType.TABLE_ROW, format=DataFormat.TXT, data="a plain-string row")
    for i in range(n_text):
        if i % 50 == 0:
            add(type=BlockType.TEXT, format=DataFormat.TXT, data=_DENSE[(i // 50) % len(_DENSE)])
        elif i % 37 == 0:
            add(type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": png})
        else:
            add(type=BlockType.TEXT, format=DataFormat.MARKDOWN,
                data=" ".join(rng.choice(words) for _ in range(rng.randint(40, 200))),
                weburl="https://example.com/pr/1", source_id=f"sha{i}",
                source_creation_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    groups = [BlockGroup(index=0, type=GroupType.TEXT_SECTION, name="body", description="x" * 200)]
    return BlocksContainer(blocks=blocks, block_groups=groups)


def _extractor() -> DocumentExtraction:
    return DocumentExtraction(logger=logging.getLogger("test"), graph_provider=None, config_service=None)


def _as_received(request_blocks: list[dict]) -> list[Block]:
    """The blocks the route hands to the extractor, after the wire and validation."""
    wire = json.dumps({"block_container": {"blocks": request_blocks}})
    return ClassifyRequest.model_validate(json.loads(wire)).block_container.blocks


@pytest.fixture(params=["tiktoken", "len//4 fallback"])
def token_counter(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "tiktoken":
        pytest.importorskip("tiktoken")
    else:
        monkeypatch.setitem(sys.modules, "tiktoken", None)
    return request.param


@pytest.mark.parametrize("multimodal", [True, False])
@pytest.mark.parametrize(
    ("service_context", "client_ceiling"),
    [(2000, 2000), (2000, 3000), (5000, 5000), (128000, 128000), (-10, -10)],
)
def test_service_builds_the_same_content_from_the_trimmed_request(
    token_counter, multimodal, service_context, client_ceiling
) -> None:
    container = _container()
    extractor = _extractor()

    request_blocks = classify_request_blocks(container.blocks, client_ceiling)

    full = extractor._prepare_content(
        container.blocks, is_multimodal_llm=multimodal, context_length=service_context,
    )
    trimmed = extractor._prepare_content(
        _as_received(request_blocks), is_multimodal_llm=multimodal, context_length=service_context,
    )
    assert trimmed == full
    if client_ceiling == 2000:
        # The bound has to engage for this to say anything about trimming.
        assert len(request_blocks) < len(classify_request_blocks(container.blocks, None))


def test_unknown_context_keeps_every_block_the_service_reads() -> None:
    container = _container(n_text=50)
    request_blocks = classify_request_blocks(container.blocks, None)
    read = [
        b for b in container.blocks
        if b.type.value in {"text", "image", "table_row", "code"} and b.data
    ]
    assert len(request_blocks) == len(read)
    assert all(set(rb) == {"type", "format", "data"} for rb in request_blocks)


def test_request_data_matches_model_dump_for_non_string_data() -> None:
    data = {"text": "x", "when": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            "nested": [{"b": b"\x00raw"}, (1, 2)], "n": 1.5}
    block = Block(index=0, type=BlockType.CODE, format=DataFormat.CODE, data=data)
    [rb] = classify_request_blocks([block], None)
    dumped = block.model_dump(mode="json")
    assert rb == {"type": dumped["type"], "format": dumped["format"], "data": dumped["data"]}


def test_cl100k_has_no_token_longer_than_the_floor_assumes() -> None:
    tiktoken = pytest.importorskip("tiktoken")
    enc = tiktoken.get_encoding("cl100k_base")
    longest = 0
    for token in range(enc.n_vocab):
        try:
            longest = max(longest, len(enc.decode_single_token_bytes(token)))
        except KeyError:  # unassigned ids between the ranks and the special tokens
            continue
    assert longest <= _MAX_CHARS_PER_TOKEN


@pytest.mark.parametrize("text", [*_DENSE, "x", "ab", "abc", "abcd" * 33])
def test_token_floor_never_exceeds_the_service_count(text) -> None:
    tiktoken = pytest.importorskip("tiktoken")
    enc = tiktoken.get_encoding("cl100k_base")
    try:
        exact = len(enc.encode(text))
    except ValueError:  # special token: the service falls back to len // 4
        exact = max(1, len(text) // 4)
    assert _token_floor("text", text) <= exact
    assert _token_floor("text", text) <= max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _client(ai_models) -> ExtractionClient:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=ai_models)
    return ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1,
                            config_service=config_service)


async def _sent_payload(client: ExtractionClient, container: BlocksContainer) -> dict:
    post = AsyncMock(return_value=httpx.Response(200, json={"success": True, "classification": None}))
    with patch.object(client, "_post_json", new=post):
        await client.classify(container, "org-1")
    return post.call_args[0][1]


@pytest.mark.asyncio
async def test_client_sends_only_the_fields_the_service_reads() -> None:
    container = _container(n_text=20)
    payload = await _sent_payload(_client({"llm": [{"provider": "openai"}]}), container)

    sent = payload["block_container"]
    assert set(sent) == {"blocks"}
    assert all(set(b) == {"type", "format", "data"} for b in sent["blocks"])
    assert len(json.dumps(sent)) < len(json.dumps(container.model_dump(mode="json"))) // 2


@pytest.mark.asyncio
async def test_client_trims_to_the_largest_configured_context() -> None:
    container = _container()
    ai_models = {
        "llm": [{"provider": "a", "contextLength": 1500}, {"provider": "b", "contextLength": 2000}],
        "embedding": [{"provider": "c"}],  # no contextLength: the service's default applies
        "modelRoles": {"indexing": {"modelType": "llm", "modelKey": "k"}},
    }
    payload = await _sent_payload(_client(ai_models), container)
    assert payload["block_container"]["blocks"] == classify_request_blocks(container.blocks, 128000)

    ai_models["embedding"] = []
    payload = await _sent_payload(_client(ai_models), container)
    assert payload["block_container"]["blocks"] == classify_request_blocks(container.blocks, 2000)


@pytest.mark.asyncio
@pytest.mark.parametrize("ai_models", [
    None,
    {"llm": [{"contextLength": "200000"}]},
    {"llm": [{"contextLength": float("inf")}]},
    {"llm": [{"contextLength": True}]},
])
async def test_client_sends_every_block_when_the_ceiling_is_unknown(ai_models) -> None:
    container = _container()
    payload = await _sent_payload(_client(ai_models), container)
    assert payload["block_container"]["blocks"] == classify_request_blocks(container.blocks, None)


@pytest.mark.asyncio
async def test_client_without_config_service_sends_every_block() -> None:
    container = _container(n_text=20)
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    payload = await _sent_payload(client, container)
    assert payload["block_container"]["blocks"] == classify_request_blocks(container.blocks, None)


@pytest.mark.asyncio
async def test_the_trim_budget_reads_the_models_uncached_like_the_service() -> None:
    # The service reads contextLength uncached (app/utils/llm.py); a cached copy
    # could hold a length an admin has since raised and trim a tail it would read.
    client = _client({"llm": [{"provider": "openai", "contextLength": 8000}]})
    await _sent_payload(client, _container(n_text=3))
    client._config_service.get_config.assert_awaited_once()
    assert client._config_service.get_config.await_args.kwargs.get("use_cache") is False


@pytest.mark.asyncio
async def test_a_code_file_reaches_the_service_with_what_the_code_prompt_reads() -> None:
    """The summary block, code_metadata and block groups: without them the service
    finds no summary block and classifies the file with the document prompt."""
    from app.modules.parsers.code_parser import CodeFileParser
    from app.modules.transformers.document_extraction import find_code_summary_block

    source = b'class Store(Base):\n    """Persists vectors."""\n\n    def upsert(self, points):\n        return points\n'
    container = CodeFileParser().parse_to_blocks(source, "store.py", "app/store.py", "python")
    assert container.block_groups, "a class is a block group"
    post = AsyncMock(return_value=httpx.Response(200, json={"success": True, "classification": None}))
    client = _client({"llm": [{"provider": "openai"}]})
    with patch.object(client, "_post_json", new=post):
        await client.classify(container, "org-1", record_type="CODE_FILE")

    received = ClassifyRequest.model_validate(post.call_args[0][1]).block_container
    summary = find_code_summary_block(received.blocks)
    assert summary is not None
    extractor = DocumentExtraction(logging.getLogger("test"), MagicMock(), MagicMock())
    sent = extractor._prepare_code_content(received.blocks, summary, 128000, received.block_groups)
    local = extractor._prepare_code_content(
        container.blocks, find_code_summary_block(container.blocks), 128000, container.block_groups,
    )
    assert sent == local
    assert "Persists vectors." in sent
