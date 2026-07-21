"""Targeted coverage for app.api.routes.chatbot (attachments, web search stream, helpers)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.blocks import BlockType

# ---------------------------------------------------------------------------
# Helpers: collapse, MIME, attachment extension
# ---------------------------------------------------------------------------


class TestCollapseSingleTextUserContent:
    def test_empty_parts_returns_empty_string(self):
        from app.api.routes.chatbot import _collapse_single_text_user_content

        assert _collapse_single_text_user_content([]) == ""

    def test_single_text_block_returns_plain_string(self):
        from app.api.routes.chatbot import _collapse_single_text_user_content

        assert (
            _collapse_single_text_user_content([{"type": "text", "text": "hello"}])
            == "hello"
        )

    def test_multiple_parts_returns_list(self):
        from app.api.routes.chatbot import _collapse_single_text_user_content

        parts = [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": {}}]
        assert _collapse_single_text_user_content(parts) is parts


class TestAttachmentMimeHelpers:
    def test_supported_mimes(self):
        from app.api.routes.chatbot import _is_supported_attachment_mime

        assert _is_supported_attachment_mime("application/pdf") is True
        assert _is_supported_attachment_mime("IMAGE/PNG") is True
        assert _is_supported_attachment_mime("text/plain") is True
        assert _is_supported_attachment_mime("text/markdown") is True
        assert _is_supported_attachment_mime("text/mdx") is True

    def test_text_attachment_detection(self):
        from app.api.routes.chatbot import _is_text_attachment

        assert _is_text_attachment("text/plain") is True
        assert _is_text_attachment("TEXT/MARKDOWN") is True
        assert _is_text_attachment("text/mdx") is True
        assert _is_text_attachment("application/pdf") is False

    def test_image_detection(self):
        from app.api.routes.chatbot import _is_image_attachment

        assert _is_image_attachment("image/webp") is True

    def test_extension_prefers_suffix(self):
        from app.api.routes.chatbot import _attachment_extension

        assert _attachment_extension("Report.PDF", "application/pdf") == "pdf"

    def test_extension_from_mime_fallbacks(self):
        from app.api.routes.chatbot import _attachment_extension

        assert _attachment_extension("no_suffix", "application/pdf") == "pdf"
        assert _attachment_extension("x", "image/jpeg") == "jpg"
        assert _attachment_extension("x", "image/png") == "png"
        assert _attachment_extension("x", "text/plain") == "txt"
        assert _attachment_extension("x", "text/markdown") == "md"
        assert _attachment_extension("x", "text/mdx") == "mdx"
        assert _attachment_extension("plain", "application/octet-stream") == "bin"


@pytest.mark.parametrize("mime,expected_subtype", [
    ("image/jpeg", "image/jpeg"),
    ("image/png", "image/png"),
])
def test_build_image_blocks_mime_paths(mime, expected_subtype):
    from app.api.routes.chatbot import _build_image_blocks

    raw = b"\x89PNG\r\n\x1a\n" if "png" in mime else b"\xff\xd8\xff\xd9"
    container = _build_image_blocks(raw, mime)
    assert container.blocks[0].data["uri"].startswith(f"data:{expected_subtype}")


@pytest.mark.asyncio
async def test_build_text_blocks_parses_markdown():
    from app.api.routes.chatbot import _build_text_blocks

    container = await _build_text_blocks(b"# Hello\n\nWorld")
    assert container.blocks
    assert any(
        getattr(b, "type", None) == "text" or (isinstance(b, dict) and b.get("type") == "text")
        for b in container.blocks
    )


@pytest.mark.parametrize("needs_ocr_per_page,len_pages,expect", [
    (True, 1, True),
    (False, 2, False),
])
def test_pdf_has_any_ocr_page_mocked_doc(needs_ocr_per_page, len_pages, expect):
    """ocr_count / total >= 0.5 when all scanned pages."""
    from app.api.routes.chatbot import _pdf_has_any_ocr_page

    mock_pages = [MagicMock() for _ in range(len_pages)]
    mock_pdf = MagicMock()
    mock_pdf.pages = mock_pages

    class FakePDFOpen:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return mock_pdf

        def __exit__(self, *_):
            pass

    with patch("app.api.routes.chatbot.pdfplumber.open", FakePDFOpen):
        with patch(
            "app.api.routes.chatbot.OCRStrategy.needs_ocr",
            return_value=needs_ocr_per_page,
        ):
            assert _pdf_has_any_ocr_page(b"%PDF-x") is expect


def test_pdf_has_any_empty_doc():
    from app.api.routes.chatbot import _pdf_has_any_ocr_page

    mock_pdf = MagicMock()
    mock_pdf.pages = []

    class FakePDFOpen:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return mock_pdf

        def __exit__(self, *_):
            pass

    with patch("app.api.routes.chatbot.pdfplumber.open", FakePDFOpen):
        assert _pdf_has_any_ocr_page(b"%PDF-empty") is False


def test_pdf_page_count_fixture():
    from app.api.routes.chatbot import _pdf_page_count

    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock() for _ in range(5)]

    class FakePDFOpen:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return mock_pdf

        def __exit__(self, *_):
            pass

    with patch("app.api.routes.chatbot.pdfplumber.open", FakePDFOpen):
        assert _pdf_page_count(b"%PDF-x") == 5


def test_build_pdf_image_blocks_one_page():
    from app.api.routes.chatbot import _build_pdf_image_blocks

    mock_image = MagicMock()

    with patch(
        "app.api.routes.chatbot.render_all_pages_as_pil_from_bytes_sync",
        return_value=[mock_image],
    ) as mock_render:
        bc = _build_pdf_image_blocks(b"%PDF-sample")
        assert len(bc.blocks) == 1
        assert bc.blocks[0].type == BlockType.IMAGE
        mock_render.assert_called_once_with(b"%PDF-sample", resolution=144)
        mock_image.save.assert_called_once()


@pytest.mark.parametrize("append_citations", [True, False])
def test_build_system_prompt_time_and_citations(append_citations):
    from app.api.routes.chatbot import _build_system_prompt

    with patch("app.api.routes.chatbot.build_llm_time_context", return_value="TZ context"):
        out = _build_system_prompt(
            "standard",
            {"customSystemPrompt": "CUSTOM"},
            "2026-05-01T00:00:00Z",
            "UTC",
            append_citation_rules=append_citations,
        )
    assert "CUSTOM" in out
    assert "TZ context" in out
    if append_citations:
        assert "## Citation Rules" in out


@pytest.mark.asyncio
class TestCreateInternalSearchToolErrors:

    async def test_bad_status_returns_error_dict(self):
        from app.api.routes.chatbot import create_internal_search_tool

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={
                "searchResults": [{}],
                "status_code": 404,
                "message": "no hits",
            },
        )

        fn = create_internal_search_tool(
            retrieval,
            "org",
            "user",
            None,
            {},
            MagicMock(),
            False,
            {},
            MagicMock(),
            MagicMock(),
            [],
        )
        out = await fn.ainvoke({"query": "q"})
        assert out["ok"] is False
        assert out["result_type"] == "internal_search"

    async def test_exception_in_search_returns_error_dict(self):
        from app.api.routes.chatbot import create_internal_search_tool

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={
                "searchResults": [{}],
                "virtual_to_record_map": {},
                "status_code": 200,
            },
        )

        with patch(
            "app.api.routes.chatbot.get_flattened_results",
            new_callable=AsyncMock,
            side_effect=RuntimeError("flatten boom"),
        ):
            fn = create_internal_search_tool(
                retrieval,
                "org",
                "user",
                None,
                {},
                MagicMock(),
                False,
                {},
                MagicMock(),
                MagicMock(),
                [],
            )
            out = await fn.ainvoke({"query": "q"})
        assert "Internal search failed" in out["error"]


@pytest.mark.asyncio
async def test_append_conversation_pdf_history_with_logger_warning():
    from app.api.routes.chatbot import CitationRefMapper, _append_conversation_history

    log = MagicMock()
    bs = AsyncMock()
    bs.get_record_from_storage = AsyncMock(return_value={"id": "r1"})

    conv = [
        {
            "role": "user_query",
            "content": "prior",
            "attachments": [{"virtualRecordId": "vr1", "mimeType": "application/pdf"}],
        },
    ]
    msgs: list = [{"role": "system", "content": ""}]
    vmap: dict = {}
    mapper = CitationRefMapper()

    with patch("app.api.routes.chatbot.record_to_message_content") as rtc:
        rtc.side_effect = RuntimeError("parse fail")

        prev = await _append_conversation_history(
            msgs,
            conv,
            blob_store=bs,
            org_id="o",
            ref_mapper=mapper,
            virtual_record_id_to_result=vmap,
            logger=log,
            is_multimodal_llm=False,
        )

    assert prev is False
    log.warning.assert_called()


@pytest.mark.asyncio
async def test_append_multimodal_list_sets_flag():
    from app.api.routes.chatbot import _append_conversation_history

    cq_spec = [
        {
            "role": "user_query",
            "content": "hello",
            "attachments": [{"virtualRecordId": "x", "mimeType": "image/png"}],
        },
    ]
    msgs: list = [{"role": "system", "content": "s"}]

    with patch(
        "app.utils.chat_helpers.build_multimodal_user_content",
        new_callable=AsyncMock,
        return_value=[{"type": "image_url", "image_url": {"url": "x"}}],
    ):
        bs = AsyncMock()

        prev = await _append_conversation_history(
            msgs,
            cq_spec,
            is_multimodal_llm=True,
            blob_store=bs,
            org_id="o1",
            ref_mapper=None,
            virtual_record_id_to_result=None,
        )

    assert prev is True
    assert isinstance(msgs[1]["content"], list)


@pytest.mark.asyncio
async def test_build_attachment_llm_messages_pdf_and_image_captions():
    from app.api.routes.chatbot import ChatQuery, _build_attachment_llm_messages

    q_pdf = ChatQuery(
        query="look at pdf",
        attachments=[{"virtualRecordId": "vr-pdf", "mimeType": "application/pdf"}],
    )
    log = MagicMock()
    bs = AsyncMock()

    bs.get_record_from_storage = AsyncMock(
        return_value={"id": "r", "block_containers": {"blocks": []}},
    )

    with patch("app.api.routes.chatbot.record_to_message_content") as rtc_pdf:
        rtc_pdf.return_value = ([{"type": "text", "text": "**block**"}], MagicMock())
        msgs, _mapper = await _build_attachment_llm_messages(
            q_pdf,
            {},
            "",
            log,
            is_multimodal_llm=True,
            blob_store=bs,
            org_id="org",
            has_attachments=True,
            virtual_record_id_to_result={},
        )

    user_parts = msgs[-1]["content"]
    assert isinstance(user_parts, list)
    user_text_blob = "".join(
        str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in user_parts
    )
    assert "Attached documents" in user_text_blob

    q_img = ChatQuery(
        query="with images",
        attachments=[{"virtualRecordId": "i", "mimeType": "image/png"}],
    )
    with patch(
        "app.utils.chat_helpers.build_multimodal_user_content",
        new_callable=AsyncMock,
        return_value=[{"type": "image_url", "image_url": {"url": "u"}}],
    ):
        msgs2, _ = await _build_attachment_llm_messages(
            q_img,
            {},
            "",
            MagicMock(),
            is_multimodal_llm=True,
            blob_store=AsyncMock(),
            org_id="org",
            has_attachments=False,
            virtual_record_id_to_result={},
        )

    u_parts = msgs2[-1]["content"]
    captions = "".join(str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in u_parts)
    assert "Attachments/Image queries" in captions


@pytest.mark.asyncio
async def test_append_pdf_history_collapses_when_multipart_content_exists():
    from app.api.routes.chatbot import CitationRefMapper, _append_conversation_history

    bs = AsyncMock()
    bs.get_record_from_storage = AsyncMock(return_value={"id": "pdfrec"})

    conv = [
        {
            "role": "user_query",
            "content": "prior",
            "attachments": [{"virtualRecordId": "vpdf", "mimeType": "application/pdf"}],
        },
    ]
    msgs: list = [{"role": "system", "content": "s"}]

    mapper = CitationRefMapper()
    parts_vmap = {}

    with patch(
        "app.utils.chat_helpers.build_multimodal_user_content",
        new_callable=AsyncMock,
        return_value=[
            {"type": "image_url", "image_url": {"url": "u"}},
            {"type": "text", "text": "cap"},
        ],
    ):
        with patch("app.api.routes.chatbot.record_to_message_content") as rtc:
            rtc.return_value = ([{"type": "text", "text": "**pdfblocks**"}], mapper)

            prev = await _append_conversation_history(
                msgs,
                conv,
                is_multimodal_llm=True,
                blob_store=bs,
                org_id="o",
                ref_mapper=CitationRefMapper(),
                virtual_record_id_to_result=parts_vmap,
                logger=MagicMock(),
            )

    assert prev is True
    user_msg = msgs[1]["content"]
    assert isinstance(user_msg, list)


@pytest.mark.asyncio
async def test_build_web_search_messages():
    from app.api.routes.chatbot import ChatQuery, _build_web_search_messages

    q = ChatQuery(query="weather", timezone="Etc/UTC", currentTime="2026-05-01T12:00:00Z")

    msgs = await _build_web_search_messages(q, {}, "weather today")
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_build_chat_llm_messages_image_blocks_kwarg():

    from app.api.routes.chatbot import ChatQuery, _build_chat_llm_messages

    q = ChatQuery(
        query="describe",
        attachments=[{"virtualRecordId": "z", "mimeType": "image/jpeg"}],
    )

    fake_img = [
        {"type": "text", "text": "embedded"},
        {"type": "image_url", "image_url": {"url": "u"}},
    ]

    with patch(
        "app.utils.chat_helpers.build_multimodal_user_content",
        new_callable=AsyncMock,
        return_value=fake_img,
    ):
        with patch("app.api.routes.chatbot.get_message_content") as gmc:
            gmc.return_value = ("combined", MagicMock())
            _, _ref = await _build_chat_llm_messages(
                q,
                {},
                [],
                {},
                "",
                MagicMock(),
                is_multimodal_llm=True,
                blob_store=MagicMock(),
                org_id="o",
            )

    ib = gmc.call_args.kwargs["image_blocks"]
    assert isinstance(ib, list)
    assert any(p.get("type") == "image_url" for p in ib)


@pytest.mark.asyncio
async def test_generate_web_search_stream_success():
    from app.api.routes.chatbot import ChatQuery, _generate_web_search_stream

    qi = ChatQuery(query="news", chatMode="web_search")

    req = MagicMock()
    req.state.user = {"orgId": "o", "userId": "u"}
    req.app.container.logger.return_value = MagicMock()

    cfg = AsyncMock()
    cfg.get_config = AsyncMock(
        side_effect=[
            {"llm": [{
                "modelKey": "k",
                "configuration": {"model": "gpt"},
                "provider": "openai",
                "isMultimodal": False,
                "contextLength": 8192,
            }]},
            {"providers": [{"isDefault": True, "provider": "x", "configuration": {}}]},
        ],
    )

    async def sse_stream(**kwargs):
        yield {"event": "token", "data": {"text": "a"}}

    with patch(
        "app.api.routes.chatbot.get_llm_for_chat",
        new_callable=AsyncMock,
        return_value=(
            MagicMock(),
            {"provider": "openai", "isMultimodal": False, "contextLength": 1000},
            {},
        ),
    ):
        with patch(
            "app.api.routes.chatbot.stream_llm_response_with_tools",
            return_value=sse_stream(),
        ):
            with patch("app.api.routes.chatbot.create_web_search_tool"):
                with patch("app.api.routes.chatbot.create_fetch_url_tool"):
                    chunks: list[str] = []
                    async for ch in _generate_web_search_stream(req, qi, cfg):
                        chunks.append(ch)

    assert chunks


@pytest.mark.asyncio
async def test_generate_web_search_stream_setup_error_emits_sse():
    from app.api.routes.chatbot import ChatQuery, _generate_web_search_stream

    qi = ChatQuery(query="news", chatMode="web_search")
    req = MagicMock()
    req.app.container.logger.return_value = MagicMock()
    cfg = AsyncMock()

    with patch(
        "app.api.routes.chatbot.get_llm_for_chat",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm boom"),
    ):
        out = []
        async for ch in _generate_web_search_stream(req, qi, cfg):
            out.append(ch)

    assert any("boom" in c for c in out)


@pytest.mark.asyncio
async def test_generate_web_search_no_default_provider_warning():

    """Config without default provider triggers warning branch (covers 1037-1038)."""

    from app.api.routes.chatbot import ChatQuery, _generate_web_search_stream

    qi = ChatQuery(query="q", chatMode="web_search")

    req = MagicMock()

    req.state.user = {"orgId": "o", "userId": "u"}

    req.app.container.logger.return_value = MagicMock()

    cfg = AsyncMock()

    cfg.get_config = AsyncMock(
        side_effect=[
            {
                "llm": [{
                    "modelKey": "k",
                    "configuration": {"model": "gpt"},
                    "provider": "openai",

                    "isMultimodal": False,

                    "contextLength": 8192,

                }],
            },

            {},  # no providers key -> warning branch

        ],
    )



    async def sse(**kwargs):

        yield {"event": "done", "data": {}}

    with patch(

        "app.api.routes.chatbot.get_llm_for_chat",

        new_callable=AsyncMock,

        return_value=(

            MagicMock(),

            {"provider": "openai", "isMultimodal": False, "contextLength": 1000},

            {},

        ),

    ):
        with patch(
            "app.api.routes.chatbot.stream_llm_response_with_tools",
            return_value=sse(),
        ):
            with patch("app.api.routes.chatbot.create_web_search_tool"):
                with patch("app.api.routes.chatbot.create_fetch_url_tool"):
                    out = []

                    async for ch in _generate_web_search_stream(req, qi, cfg):

                        out.append(ch)

                    assert len(out) >= 1

@pytest.mark.asyncio
async def test_generate_web_search_stream_iteration_error():
    from app.api.routes.chatbot import ChatQuery, _generate_web_search_stream

    qi = ChatQuery(query="q", chatMode="web_search")
    req = MagicMock()
    req.state.user = {"orgId": "o", "userId": "u"}
    req.app.container.logger.return_value = MagicMock()

    cfg = AsyncMock()
    cfg.get_config = AsyncMock(
        side_effect=[
            {"llm": [{
                "modelKey": "k",
                "configuration": {"model": "gpt"},
                "provider": "openai",
                "isMultimodal": False,
                "contextLength": 8192,
            }]},
            {"providers": [{"isDefault": True, "provider": "x", "configuration": {}}]},
        ],
    )

    async def boom_stream(**kwargs):
        raise RuntimeError("stream iterate fail")
        yield  # pragma: no cover

    with patch(
        "app.api.routes.chatbot.get_llm_for_chat",
        new_callable=AsyncMock,
        return_value=(
            MagicMock(),
            {"provider": "openai", "isMultimodal": False, "contextLength": 8192},
            {},
        ),
    ):
        with patch(
            "app.api.routes.chatbot.stream_llm_response_with_tools",
            return_value=boom_stream(),
        ):
            with patch("app.api.routes.chatbot.create_web_search_tool"):
                with patch("app.api.routes.chatbot.create_fetch_url_tool"):
                    chunks: list[str] = []
                    async for ch in _generate_web_search_stream(req, qi, cfg):
                        chunks.append(ch)

    assert any("stream iterate fail" in c or "iterate fail" in c for c in chunks)


@pytest.mark.asyncio
async def test_internal_search_outer_except_after_first_yield():
    from app.api.routes.chatbot import _generate_internal_search_stream

    class BrokenQI:
        modelKey = None
        modelName = None
        chatMode = "internal_search"
        mode = "json"
        query = "hello"
        limit = 50
        filters = None
        conversationId = None

        @property
        def attachments(self):  # type: ignore[override]
            raise RuntimeError("bad attachments accessor")

        @property
        def previousConversations(self):  # type: ignore[override]
            return []

    retrieval = AsyncMock()
    cfg = AsyncMock()
    graph_p = AsyncMock()
    req = MagicMock()
    req.state.user = {"orgId": "o1", "userId": "u1"}
    req.query_params = {}
    req.app.container.logger.return_value = MagicMock()

    chunks: list[str] = []
    with patch(
        "app.api.routes.chatbot.get_llm_for_chat",
        new_callable=AsyncMock,
        return_value=(
            MagicMock(),
            {"provider": "openai", "isMultimodal": False, "contextLength": 4096},
            {},
        ),
    ):
        async for ch in _generate_internal_search_stream(
            req,
            BrokenQI(),
            retrieval,
            graph_p,
            cfg,
        ):
            chunks.append(ch)

    assert len(chunks) >= 2
    assert any("bad attachments accessor" in c for c in chunks)


@pytest.mark.asyncio
async def test_ask_ai_stream_dispatches_web_search():
    from fastapi.responses import StreamingResponse

    from app.api.routes.chatbot import askAIStream

    async def fake_web(**kwargs):

        _ = kwargs

        yield "sse"

    rr = MagicMock()
    rr.json = AsyncMock(return_value={"query": "q", "chatMode": "web_search"})

    with patch(
        "app.api.routes.chatbot._generate_web_search_stream",
        side_effect=fake_web,
    ):
        resp = await askAIStream(rr, AsyncMock(), AsyncMock(), AsyncMock())

    assert isinstance(resp, StreamingResponse)


@pytest.mark.asyncio
async def test_upload_chat_attachments_json_and_payload_errors():

    from app.api.routes.chatbot import upload_chat_attachments

    r1 = MagicMock()
    r1.json = AsyncMock(side_effect=ValueError("bad"))
    with pytest.raises(HTTPException) as exc:
        await upload_chat_attachments(r1, AsyncMock(), AsyncMock())
    assert exc.value.status_code == 400

    r2 = MagicMock()
    r2.json = AsyncMock(return_value={"conversationId": "c"})
    with pytest.raises(HTTPException) as exc:
        await upload_chat_attachments(r2, AsyncMock(), AsyncMock())
    assert "attachments" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_upload_chat_attachments_missing_org():
    import base64

    from app.api.routes.chatbot import upload_chat_attachments

    png_b64 = base64.b64encode(b"x" * 8).decode("ascii")
    body = MagicMock()
    body.json = AsyncMock(
        return_value={
            "attachments": [
                {
                    "fileName": "x.png",
                    "mimeType": "image/png",
                    "size": 50,
                    "contentBase64": png_b64,
                },
            ],
        },
    )
    body.state.user = {}
    with pytest.raises(HTTPException) as exc:
        await upload_chat_attachments(body, AsyncMock(), AsyncMock())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_png_happy_mocked_sink():
    import base64

    from app.models.blocks import BlocksContainer

    from app.api.routes.chatbot import upload_chat_attachments

    png_b64 = base64.b64encode(b"z" * 32).decode("ascii")
    req = MagicMock()
    req.json = AsyncMock(
        return_value={
            "conversationId": "conv-z",
            "attachments": [
                {
                    "fileName": "f.png",
                    "mimeType": "image/png",
                    "size": 32,
                    "contentBase64": png_b64,
                },
            ],
        },
    )
    req.state.user = {"orgId": "org-x", "userId": "u-x", "isServiceAccount": False}

    gp = AsyncMock()

    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "gk"})

    gp.batch_upsert_nodes = AsyncMock()
    gp.batch_create_edges = AsyncMock()

    cont = MagicMock()
    req.app.container = cont
    cont.logger.return_value = MagicMock()

    with patch(
        "app.api.routes.chatbot.BlobStorage",
    ) as mock_bs_cls:

        bs_inst = AsyncMock()

        bs_inst.save_binary_to_storage = AsyncMock(return_value=("ext", None))

        mock_bs_cls.return_value = bs_inst

        with patch(
            "app.api.routes.chatbot.GraphDBTransformer",
            return_value=MagicMock(),
        ):
            orch = AsyncMock()

            orch.apply = AsyncMock()

            fake_rec = MagicMock()

            fake_rec.block_containers = BlocksContainer(blocks=[], block_groups=[])

            with patch(
                "app.api.routes.chatbot.SinkOrchestrator",
                return_value=orch,
            ):
                with patch(
                    "app.api.routes.chatbot.convert_record_dict_to_record",
                    return_value=fake_rec,
                ):
                    with patch(
                        "app.api.routes.chatbot.TransformContext",
                        MagicMock(side_effect=lambda **_: MagicMock()),
                    ):
                        out = await upload_chat_attachments(req, gp, AsyncMock())

    assert out["conversationId"] == "conv-z"
    assert len(out["attachments"]) == 1
    orch.index.assert_awaited()


@pytest.mark.asyncio
async def test_grant_revoke_attachment_permissions():

    import app.api.routes.chatbot as cr

    from app.api.routes.chatbot import grant_attachment_permissions, revoke_attachment_permissions

    req_g = MagicMock()

    req_g.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["rec"]})

    gp = AsyncMock()

    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "k1"})

    gp.batch_create_edges = AsyncMock()

    gp.batch_delete_edges = AsyncMock()

    with patch.object(cr.logger, "warning", MagicMock()):

        r1 = await grant_attachment_permissions(req_g, gp)
        assert r1["granted"] == 1

    req_none = MagicMock()

    req_none.json = AsyncMock(return_value={"userIds": [], "recordIds": []})

    r2 = await grant_attachment_permissions(req_none, gp)
    assert r2["granted"] == 0

    gp.get_user_by_user_id = AsyncMock(return_value=None)
    req_skip = MagicMock()

    req_skip.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["rec"]})

    with patch.object(cr.logger, "warning", MagicMock()):

        r3 = await grant_attachment_permissions(req_skip, gp)

    assert r3["granted"] == 0

    req_r = MagicMock()

    req_r.json = AsyncMock(return_value={"userIds": ["x"], "recordIds": ["y"]})

    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "kk"})

    out_r = await revoke_attachment_permissions(req_r, gp)
    gp.batch_delete_edges.assert_awaited()
    assert out_r["revoked"] >= 1


@pytest.mark.asyncio
async def test_delete_chat_attachment_paths():
    from app.api.routes.chatbot import delete_chat_attachment

    r = MagicMock()
    r.state.user = {}
    with pytest.raises(HTTPException) as ex:
        await delete_chat_attachment("rid", r, AsyncMock())
    assert ex.value.status_code == 400

    gp = AsyncMock()

    gp.get_document = AsyncMock(return_value={"orgId": "other"})
    rr = MagicMock()
    rr.state.user = {"orgId": "mine"}
    with pytest.raises(HTTPException) as ex:

        await delete_chat_attachment("rid", rr, gp)

    assert ex.value.status_code == 403


@pytest.mark.asyncio
async def test_internal_stream_sql_deferred_execute_tool():
    from types import SimpleNamespace

    from app.api.routes.chatbot import _generate_internal_search_stream

    captured: dict[str, object] = {}

    qi = SimpleNamespace(
        attachments=[{"virtualRecordId": "vx", "mimeType": "application/pdf"}],
        previousConversations=[],


        chatMode="internal_search",


        mode="simple",

        query="q",

        modelKey=None,

        modelName=None,

        limit=None,

        filters=None,

        conversationId=None,
    )


    def stream_effect(*args, **kwargs):

        captured["defer_name"] = kwargs.get("defer_tool_until_called_name")


        deferred = kwargs.get("deferred_tool")


        captured["defer_len"] = len(deferred) if deferred else 0

        captured["max_hops"] = kwargs.get("max_hops")

        async def _gen():

            yield {"event": "done", "data": {}}

        return _gen()

    req = MagicMock()

    req.state.user = {"orgId": "o", "userId": "u"}
    req.query_params = {}

    req.app.container.logger.return_value = MagicMock()




    retrieval = AsyncMock()


    gp = AsyncMock()


    cfg = AsyncMock()


    with patch(
        "app.api.routes.chatbot.stream_llm_response_with_tools",
        side_effect=stream_effect,
    ):
        with patch(
            "app.api.routes.chatbot._build_attachment_llm_messages",

            new_callable=AsyncMock,
            return_value=(
                [{"role": "system", "content": "s"}, {"role": "user", "content": []}],
                MagicMock(),
            ),

        ):
            with patch(
                "app.api.routes.chatbot.create_internal_search_tool",
                return_value=MagicMock(),
            ):
                with patch(
                    "app.api.routes.chatbot.create_fetch_full_record_tool",

                    return_value=MagicMock(),

                ):
                    with patch(
                        "app.api.routes.chatbot.create_execute_query_tool",
                        return_value=MagicMock(),

                    ):
                        with patch(
                            "app.api.routes.chatbot.has_sql_connector_configured",
                            new_callable=AsyncMock,
                            return_value=True,
                        ):
                            with patch(
                                "app.api.routes.chatbot.BlobStorage",
                                return_value=MagicMock(),
                            ):
                                with patch(
                                    "app.api.routes.chatbot.get_llm_for_chat",

                                    new_callable=AsyncMock,
                                    return_value=(
                                        MagicMock(),
                                        {"provider": "openai",
                                         "isMultimodal": False,
                                         "contextLength": 4096},
                                        {},

                                    ),
                                ):
                                    with patch(
                                        "app.api.routes.chatbot._build_llm_user_context_string",
                                        new_callable=AsyncMock,
                                        return_value="",
                                    ):
                                        async for _chunk in _generate_internal_search_stream(
                                            req,
                                            qi,
                                            retrieval,

                                            gp,


                                            cfg,
                                        ):
                                            pass




    assert captured["defer_name"] == "search_internal_knowledge"


    assert captured["defer_len"] == 2




    assert captured["max_hops"] == 3


@pytest.mark.asyncio
async def test_upload_missing_user_context():
    import base64

    from app.api.routes.chatbot import upload_chat_attachments

    rr = MagicMock()
    rr.json = AsyncMock(
        return_value={
            "attachments": [
                {
                    "fileName": "a.png",
                    "mimeType": "image/png",
                    "size": 4,
                    "contentBase64": base64.b64encode(b"abcd").decode(),
                },
            ],
        },
    )
    rr.state.user = {"orgId": "org"}
    rr.app.container.logger.return_value = MagicMock()
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "k"})
    with pytest.raises(HTTPException) as ex:
        await upload_chat_attachments(rr, gp, AsyncMock())
    assert ex.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_user_resolve_failures():
    import base64

    from app.api.routes.chatbot import upload_chat_attachments

    att = {
        "fileName": "a.png",
        "mimeType": "image/png",
        "size": 8,
        "contentBase64": base64.b64encode(b"12345678").decode(),
    }

    rr = MagicMock()

    rr.json = AsyncMock(return_value={"attachments": [att]})


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": False}




    rr.app.container.logger.return_value = MagicMock()



    gp1 = AsyncMock()



    gp1.get_user_by_user_id = AsyncMock(return_value=None)



    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp1, AsyncMock())


    assert ex.value.status_code == 404



    gp2 = AsyncMock()


    gp2.get_user_by_user_id = AsyncMock(return_value={"noKeys": True})


    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp2, AsyncMock())


    assert ex.value.status_code == 500




@pytest.mark.asyncio
async def test_upload_validation_helpers():
    import base64



    from app.api.routes.chatbot import upload_chat_attachments




    user = {"orgId": "o", "userId": "u", "isServiceAccount": True}




    rr = MagicMock()


    rr.state.user = user


    rr.app.container.logger.return_value = MagicMock()



    rr.json = AsyncMock(

        return_value={

            "attachments": [{"fileName": "x.gif", "mimeType": "image/gif", "size": 8,

                             "contentBase64": base64.b64encode(b"zzzzzzzz").decode()}],

        },




    )


    gp = AsyncMock()


    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert ex.value.status_code == 400















    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "n.png",




                                                      "mimeType": "image/png",

                                                      "size": 0,




                                                      "contentBase64": base64.b64encode(b"z").decode(),



                                                     }]},




                    )



    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert "positive" in str(ex.value.detail).lower()

















    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "n.png",




                                                      "mimeType": "image/png",




                                                      "size": 2,




                                                      "contentBase64": "bad!!!",

                                                      }]},


                    )



    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert "Invalid base64" in str(ex.value.detail)





@pytest.mark.asyncio

async def test_upload_image_block_failure():






    import base64


    from app.api.routes.chatbot import upload_chat_attachments








    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr.app.container.logger.return_value = MagicMock()


    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "i.png",




                                                      "mimeType": "image/png",

                                                      "size": 8,

                                                      "contentBase64":




                                                      base64.b64encode(b"hhhhhhhh").decode(),



                                                      }]},




                    )


    gp = AsyncMock()


    gp.batch_upsert_nodes = AsyncMock()


    gp.batch_create_edges = AsyncMock()






    orch = AsyncMock()


    orch.apply = AsyncMock()


    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bs = AsyncMock()


        bs.save_binary_to_storage = AsyncMock(return_value=("x", None))


        bscl.return_value = bs


        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot._build_image_blocks", side_effect=RuntimeError("x")):


                with pytest.raises(HTTPException) as ex:


                    await upload_chat_attachments(rr, gp, AsyncMock())


                assert ex.value.status_code == 400















@pytest.mark.asyncio


async def test_upload_pdf_regular_and_scan_cap():




    import base64




    from app.models.blocks import BlocksContainer




    from app.api.routes.chatbot import OCR_IMAGE_PAGE_CAP, upload_chat_attachments

















    pdf_b64 = base64.b64encode(b"%PDF-xx").decode()








    pym = MagicMock()


    pym.parse_document = AsyncMock(return_value={"chunks": True})


    pym.create_blocks = AsyncMock(return_value=BlocksContainer(blocks=[], block_groups=[]))





    orch_ok = AsyncMock()


    orch_ok.apply = AsyncMock()





    fakerec = MagicMock()


    fakerec.block_containers = BlocksContainer(blocks=[], block_groups=[])






    rr_ok = MagicMock()


    rr_ok.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr_ok.app.container.logger.return_value = MagicMock()


    rr_ok.json = AsyncMock(return_value={"attachments": [{"fileName": "f.pdf",


                                                           "mimeType": "application/pdf",


                                                           "size": 8,




                                                           "contentBase64": pdf_b64,




                                                           }]},




                    )








    gp_ok = AsyncMock()


    gp_ok.batch_upsert_nodes = AsyncMock()


    gp_ok.batch_create_edges = AsyncMock()












    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bins = AsyncMock()


        bins.save_binary_to_storage = AsyncMock(return_value=("id", None))


        bscl.return_value = bins




        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):


                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=False):


                    with patch(


                        "app.api.routes.chatbot.convert_record_dict_to_record",





                        return_value=fakerec,





                    ):


                        with patch(




                            "app.api.routes.chatbot.TransformContext",





                            MagicMock(side_effect=lambda **_: MagicMock()),





                        ):


                            with patch(




                                "app.api.routes.chatbot.SinkOrchestrator",





                                return_value=orch_ok,





                            ):


                                await upload_chat_attachments(rr_ok, gp_ok, AsyncMock())














    pym.parse_document.assert_awaited()


    orch_ok.index.assert_awaited()



















    orch2 = AsyncMock()



    orch2.apply = AsyncMock()



    rr_bad = MagicMock()



    rr_bad.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}



    rr_bad.app.container.logger.return_value = rr_ok.app.container.logger.return_value



    rr_bad.json = rr_ok.json






    gp_bad = AsyncMock()





    gp_bad.batch_upsert_nodes = AsyncMock()





    gp_bad.batch_create_edges = AsyncMock()

















    with patch("app.api.routes.chatbot.BlobStorage") as bscl:








        bins = AsyncMock()


        bins.save_binary_to_storage = AsyncMock(return_value=("id", None))




        bscl.return_value = bins







        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):



            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):




                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=True):




                    with patch(

                        "app.api.routes.chatbot._pdf_page_count",

                        return_value=OCR_IMAGE_PAGE_CAP + 1,

                    ):


                        with pytest.raises(HTTPException) as exc:





                            await upload_chat_attachments(rr_bad, gp_bad, AsyncMock())





                        assert "Scanned attachment page cap" in str(exc.value.detail)





@pytest.mark.asyncio


async def test_grant_and_revoke_permissions_json_noise():






    """1408-1466"""


    import app.api.routes.chatbot as cr




    from app.api.routes.chatbot import grant_attachment_permissions, revoke_attachment_permissions






    req_g_json = MagicMock()


    req_g_json.json = AsyncMock(side_effect=RuntimeError("boom"))






    with pytest.raises(HTTPException) as exc:


        await grant_attachment_permissions(req_g_json, AsyncMock())






    assert exc.value.status_code == 400

















    rk = MagicMock()


    rk.json = AsyncMock(return_value={})








    with pytest.raises(HTTPException) as exc:


        await grant_attachment_permissions(rk, AsyncMock())





    assert exc.value.status_code == 400








    gp_miss = AsyncMock()


    gp_miss.get_user_by_user_id = AsyncMock(return_value={"n": True})


    rk2 = MagicMock()


    rk2.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["rid"]})








    with patch.object(cr.logger, "warning", MagicMock()):





        grant_out = await grant_attachment_permissions(rk2, gp_miss)







    assert grant_out["granted"] == 0











    rq = MagicMock()




    rq.json = AsyncMock(side_effect=RuntimeError("boom"))















    with pytest.raises(HTTPException) as exc:


        await revoke_attachment_permissions(rq, AsyncMock())


    assert exc.value.status_code == 400












@pytest.mark.asyncio


async def test_delete_chat_attachment_early_and_full():





    """1534 and 1540-1545."""





    from app.api.routes.chatbot import delete_chat_attachment








    rr = MagicMock()




    rr.state.user = {"orgId": "o1"}




    gp = AsyncMock()




    gp.get_document = AsyncMock(return_value=None)




    await delete_chat_attachment("rid", rr, gp)
















    gp.get_document.return_value = {"orgId": "o1"}




    gp.delete_nodes_and_edges = AsyncMock()





    gp.delete_nodes = AsyncMock()








    await delete_chat_attachment("rid", rr, gp)






    gp.delete_nodes_and_edges.assert_awaited()

    gp.delete_nodes.assert_awaited()



@pytest.mark.asyncio


async def test_append_history_pdf_plain_user_text():






    """427-428: PDF history merges with plain-string user content."""






    from app.api.routes.chatbot import CitationRefMapper, _append_conversation_history






    bs = AsyncMock()



    bs.get_record_from_storage = AsyncMock(return_value={"record": True})








    conv = [{"role": "user_query", "content": "prior text",


             "attachments": [{"virtualRecordId": "pv",


                                "mimeType": "application/pdf"}]}]


    msgs: list = [{"role": "system", "content": "-"}]


    cmap: dict[str, object] = {}


    fm = MagicMock()


    with patch("app.api.routes.chatbot.record_to_message_content") as rtc:


        rtc.return_value = ([{"type": "text", "text": "pdf"}], fm)


        await _append_conversation_history(


            msgs,

            conv,




            blob_store=bs,




            org_id="o",




            ref_mapper=CitationRefMapper(),

            virtual_record_id_to_result=cmap,

            logger=None,




            is_multimodal_llm=False,


        )





    merged = msgs[1]["content"]


    assert isinstance(merged, list)


    flat = "".join(


        p.get("text", "") if isinstance(p, dict) and p.get("type") == "text" else ""




        for p in merged




    )


    assert "prior text" in flat


    assert "Attached documents" in flat












@pytest.mark.asyncio


async def test_attachment_messages_pdf_warnings():






    from app.api.routes.chatbot import ChatQuery, _build_attachment_llm_messages






    log = MagicMock()


    bs = AsyncMock()


    bs.get_record_from_storage = AsyncMock(return_value=None)








    cq = ChatQuery(


        query="q",

        attachments=[{"virtualRecordId": "", "mimeType": "application/pdf"}],


    )








    await _build_attachment_llm_messages(


        cq,


        {"customSystemPrompt": "CUSTOM"},




        "",


        log,

        is_multimodal_llm=False,

        blob_store=bs,

        org_id="o",




        has_attachments=True,


        virtual_record_id_to_result={},


    )








    assert log.debug.called






    log2 = MagicMock()


    bs2 = AsyncMock()


    bs2.get_record_from_storage = AsyncMock(side_effect=RuntimeError("db"))




    cq2 = ChatQuery(




        query="q",


        attachments=[{"virtualRecordId": "z", "mimeType": "application/pdf"}],




    )


    with patch("app.api.routes.chatbot.record_to_message_content"):


        await _build_attachment_llm_messages(


            cq2,


            {},




            "",




            log2,




            is_multimodal_llm=False,




            blob_store=bs2,




            org_id="o",




            has_attachments=True,




            virtual_record_id_to_result={},


        )








    log2.warning.assert_called()





@pytest.mark.asyncio


async def test_upload_no_attachments_in_body():






    """1195."""








    import base64


    from app.api.routes.chatbot import upload_chat_attachments









    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}




    rr.app.container.logger.return_value = MagicMock()




    rr.json = AsyncMock(return_value={"attachments": []})






    gp = AsyncMock()








    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert ex.value.status_code == 400











@pytest.mark.asyncio


async def test_upload_pdf_ocr_success_small_doc():






    """1297-1298."""








    import base64









    from app.models.blocks import BlocksContainer









    from app.api.routes.chatbot import upload_chat_attachments









    pym = MagicMock()


    orch = AsyncMock()


    orch.apply = AsyncMock()


    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr.app.container.logger.return_value = MagicMock()


    pdf_raw = base64.b64encode(b"%PDF-xx").decode()


    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "s.pdf",

                                                        "mimeType": "application/pdf",

                                                        "size": 16,

                                                        "contentBase64": pdf_raw}]})


    gp = AsyncMock()


    gp.batch_upsert_nodes = AsyncMock()


    gp.batch_create_edges = AsyncMock()


    fakerec = MagicMock()


    fakerec.block_containers = BlocksContainer(blocks=[], block_groups=[])






    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bs = AsyncMock()


        bs.save_binary_to_storage = AsyncMock(return_value=("id", None))


        bscl.return_value = bs


        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):


                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=True):


                    with patch("app.api.routes.chatbot._pdf_page_count", return_value=5):


                        with patch(


                            "app.api.routes.chatbot._build_pdf_image_blocks",






                            return_value=BlocksContainer(blocks=[], block_groups=[]),





                        ):


                            with patch("app.api.routes.chatbot.convert_record_dict_to_record",

                                      return_value=fakerec):


                                with patch("app.api.routes.chatbot.TransformContext",


                                           MagicMock(side_effect=lambda **_: MagicMock())):


                                    with patch("app.api.routes.chatbot.SinkOrchestrator",


                                               return_value=orch):


                                        await upload_chat_attachments(rr, gp, AsyncMock())


    orch.index.assert_awaited()





@pytest.mark.asyncio


async def test_upload_pdf_parse_generic_failure():






    """1305-1306."""








    import base64










    from app.api.routes.chatbot import upload_chat_attachments

















    pym = MagicMock()


    pym.parse_document = AsyncMock(side_effect=RuntimeError("parse"))




    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr.app.container.logger.return_value = MagicMock()


    pdf_raw = base64.b64encode(b"x" * 8).decode()


    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "p.pdf",

                                                        "mimeType": "application/pdf",

                                                        "size": 16,

                                                        "contentBase64": pdf_raw}]})


    gp = AsyncMock()


    gp.batch_upsert_nodes = AsyncMock()


    gp.batch_create_edges = AsyncMock()








    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bs = AsyncMock()


        bs.save_binary_to_storage = AsyncMock(return_value=("id", None))


        bscl.return_value = bs


        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):


                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=False):


                    with pytest.raises(HTTPException) as ex:


                        await upload_chat_attachments(rr, gp, AsyncMock())


                    assert ex.value.status_code == 400












@pytest.mark.asyncio


async def test_revoke_invalid_payload_and_bad_user():





    import app.api.routes.chatbot as cr


    from app.api.routes.chatbot import revoke_attachment_permissions









    rk = MagicMock()


    rk.json = AsyncMock(return_value={})








    with pytest.raises(HTTPException) as ex:


        await revoke_attachment_permissions(rk, AsyncMock())





    assert ex.value.status_code == 400











    rp = AsyncMock()




    rp.get_user_by_user_id = AsyncMock(return_value={"n": True})


    rk2 = MagicMock()


    rk2.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["x"]})








    with patch.object(cr.logger, "warning", MagicMock()):


        out = await revoke_attachment_permissions(rk2, rp)








    assert out["revoked"] == 0

@pytest.mark.asyncio


async def test_internal_search_standard_path_appends_execute_query_tool_when_sql_configured():
    # Standard retrieval adds execute_query when SQL is configured.
    from types import SimpleNamespace

    from app.api.routes.chatbot import _generate_internal_search_stream


    tools_passed: list = []

    async def fake_stream_llm(**kwargs):
        tools_passed.append(list(kwargs.get("tools") or []))
        yield {"event": "token", "data": {}}
        yield {"event": "done", "data": {}}

    qi = SimpleNamespace(
        attachments=[],
        previousConversations=[],
        chatMode="internal_search",
        mode="simple",
        query="plain",
        modelKey=None,
        modelName=None,
        limit=5,
        filters=None,
        conversationId="c1",
    )

    req = MagicMock()
    req.state.user = {"orgId": "org", "userId": "user"}
    req.query_params = {}
    req.app.container.logger.return_value = MagicMock()

    retrieval = AsyncMock()
    retrieval.search_with_filters = AsyncMock(
        return_value={
            "searchResults": [{}],
            "virtual_to_record_map": {},
            "status_code": 200,
        },
    )

    gp = AsyncMock()
    cfg_exec = AsyncMock()
    eq_tool = MagicMock()
    fetch_tool = MagicMock()

    with patch("app.api.routes.chatbot.stream_llm_response_with_tools", side_effect=fake_stream_llm):
        with patch(
            "app.api.routes.chatbot.has_sql_connector_configured",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch("app.api.routes.chatbot.create_execute_query_tool", return_value=eq_tool):
                with patch(
                    "app.api.routes.chatbot.create_fetch_full_record_tool",
                    return_value=fetch_tool,
                ):
                    with patch(
                        "app.api.routes.chatbot.get_flattened_results",
                        new_callable=AsyncMock,
                        return_value=[],
                    ):
                        with patch(
                            "app.api.routes.chatbot.enrich_virtual_record_id_to_result_with_fk_children",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "app.api.routes.chatbot._build_chat_llm_messages",
                                new_callable=AsyncMock,
                                return_value=([], MagicMock()),
                            ):
                                with patch(
                                    "app.api.routes.chatbot.get_llm_for_chat",
                                    new_callable=AsyncMock,
                                    return_value=(
                                        MagicMock(),
                                        {
                                            "provider": "openai",
                                            "isMultimodal": False,
                                            "contextLength": 8000,
                                        },
                                        {},
                                    ),
                                ):
                                    with patch(
                                        "app.api.routes.chatbot.BlobStorage",
                                        return_value=MagicMock(),
                                    ):
                                        with patch(
                                            "app.api.routes.chatbot._build_llm_user_context_string",
                                            new_callable=AsyncMock,
                                            return_value="",
                                        ):
                                            async for _ in _generate_internal_search_stream(
                                                req,
                                                qi,
                                                retrieval,
                                                gp,
                                                cfg_exec,
                                            ):
                                                pass

    assert tools_passed
    tls = tools_passed[-1]
    assert eq_tool in tls
    assert fetch_tool in tls
    assert tls.index(eq_tool) < tls.index(fetch_tool)
