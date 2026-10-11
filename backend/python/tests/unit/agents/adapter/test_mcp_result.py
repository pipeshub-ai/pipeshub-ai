"""Tests for `mcp_result_to_tool_output` — raw MCP `CallToolResult` -> `ToolOutput`."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import mcp.types as mt

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agents.agent_loop.mcp_result import mcp_result_to_tool_output
from app.utils.image_admission import ImageAdmission
from app.utils.image_policy import permissive_policy

# Two distinct 1x1 PNGs.
_PNG_A = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
_PNG_B = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="


def _text(text: str) -> mt.TextContent:
    return mt.TextContent(type="text", text=text)


def _image(data: str = _PNG_A, mime: str = "image/png") -> mt.ImageContent:
    return mt.ImageContent(type="image", data=data, mimeType=mime)


def _result(
    content: list[Any] | None = None,
    structured: dict[str, Any] | None = None,
    *,
    is_error: bool = False,
    meta: dict[str, Any] | None = None,
) -> mt.CallToolResult:
    kwargs: dict[str, Any] = {"content": content or [], "isError": is_error}
    if structured is not None:
        kwargs["structuredContent"] = structured
    if meta is not None:
        kwargs["_meta"] = meta
    return mt.CallToolResult(**kwargs)


def _context(*, multimodal: bool = True, **tool_state: object) -> SimpleNamespace:
    return SimpleNamespace(is_multimodal_llm=multimodal, tool_state=dict(tool_state))


class TestStructuredContent:
    async def test_undeclared_nested_keys_are_returned_verbatim(self) -> None:
        # The `Root(events=[Root(), Root()])` regression: fastmcp's outputSchema-derived
        # dataclasses dropped every key the schema didn't declare.
        events = [{"id": "e1", "actor": "alice", "detail": {"ip": "10.0.0.1"}}, {"id": "e2", "actor": "bob"}]
        output = await mcp_result_to_tool_output(_result(structured={"events": events}))

        assert output.success is True
        assert output.data == {"events": events}

    async def test_keys_clean_tool_result_would_strip_survive(self) -> None:
        payload = {
            "request": {"method": "GET"}, "response": {"status": 200}, "headers": {"x": "1"},
            "history": [1, 2], "properties": {"k": "v"}, "active": True,
            "_id": "abc", "$ref": "#/x", "traceId": "t-1",
        }
        output = await mcp_result_to_tool_output(_result(structured=payload))

        assert output.data == payload

    async def test_stripped_keys_survive_in_a_json_text_block(self) -> None:
        payload = {"request": {"method": "GET"}, "_id": "abc", "history": [1]}
        output = await mcp_result_to_tool_output(_result([_text(json.dumps(payload))]))

        assert json.loads(output.data) == payload

    async def test_empty_structured_dict_is_returned_not_treated_as_absent(self) -> None:
        output = await mcp_result_to_tool_output(_result(structured={}))

        assert output.success is True
        assert output.data == {}

    async def test_duplicate_json_text_block_is_dropped(self) -> None:
        payload = {"count": 2, "items": ["a", "b"]}
        output = await mcp_result_to_tool_output(_result([_text(json.dumps(payload))], payload))

        assert output.data == payload

    async def test_non_duplicate_text_is_kept_alongside_structured(self) -> None:
        payload = {"count": 2}
        output = await mcp_result_to_tool_output(_result([_text("Found two items.")], payload))

        assert isinstance(output.data, str)
        assert "Found two items." in output.data
        assert json.dumps(payload) in output.data

    async def test_fastmcp_wrapped_primitive_is_unwrapped(self) -> None:
        output = await mcp_result_to_tool_output(
            _result([_text("5")], {"result": 5}, meta={"fastmcp": {"wrap_result": True}}),
        )

        assert output.data == 5

    async def test_result_key_without_wrap_meta_is_left_alone(self) -> None:
        output = await mcp_result_to_tool_output(_result(structured={"result": 5}))

        assert output.data == {"result": 5}

    async def test_fastmcp_snake_case_wrapper_is_read_too(self) -> None:
        wrapper = SimpleNamespace(
            content=[], structured_content={"a": 1}, is_error=False, meta=None, data="IGNORED",
        )
        output = await mcp_result_to_tool_output(wrapper)

        assert output.data == {"a": 1}


class TestTextAndEmpty:
    async def test_text_blocks_are_joined_with_newlines(self) -> None:
        output = await mcp_result_to_tool_output(_result([_text("hello"), _text("world")]))

        assert output.data == "hello\nworld"

    async def test_empty_successful_result_is_an_empty_string(self) -> None:
        output = await mcp_result_to_tool_output(_result())

        assert output.success is True
        assert output.data == ""


class TestErrors:
    async def test_error_with_text_returns_the_text(self) -> None:
        output = await mcp_result_to_tool_output(_result([_text("rate limited")], is_error=True))

        assert output.success is False
        assert output.error == "rate limited"

    async def test_error_with_only_structured_content_returns_its_json(self) -> None:
        output = await mcp_result_to_tool_output(_result(structured={"code": 42}, is_error=True))

        assert output.success is False
        assert json.loads(output.error) == {"code": 42}

    async def test_error_with_no_content_has_a_generic_message(self) -> None:
        output = await mcp_result_to_tool_output(_result(is_error=True))

        assert output.success is False
        assert output.error == "MCP tool returned an error without a message."


class TestOtherBlockTypes:
    async def test_embedded_text_resource_includes_uri_and_text(self) -> None:
        block = mt.EmbeddedResource(
            type="resource",
            resource=mt.TextResourceContents(uri="file:///notes.md", mimeType="text/markdown", text="# Notes"),
        )
        output = await mcp_result_to_tool_output(_result([block]))

        assert "file:///notes.md" in output.data
        assert "# Notes" in output.data

    async def test_embedded_non_image_blob_becomes_a_note_without_base64(self) -> None:
        blob = "QUJDREVGRw=="
        block = mt.EmbeddedResource(
            type="resource",
            resource=mt.BlobResourceContents(uri="file:///a.pdf", mimeType="application/pdf", blob=blob),
        )
        output = await mcp_result_to_tool_output(_result([block]))

        assert "file:///a.pdf" in output.data
        assert blob not in output.data

    async def test_embedded_image_blob_is_treated_as_an_image(self) -> None:
        block = mt.EmbeddedResource(
            type="resource",
            resource=mt.BlobResourceContents(uri="file:///a.png", mimeType="image/png", blob=_PNG_A),
        )
        output = await mcp_result_to_tool_output(_result([block]), _context())

        assert any(isinstance(part, ImagePart) for part in output.data)

    async def test_resource_link_becomes_a_note_with_name_and_uri(self) -> None:
        block = mt.ResourceLink(type="resource_link", uri="https://example.com/r/1", name="report")
        output = await mcp_result_to_tool_output(_result([block]))

        assert output.data == "[resource link: report <https://example.com/r/1>]"

    async def test_audio_becomes_a_note_without_base64(self) -> None:
        block = mt.AudioContent(type="audio", data="UklGRgAAAAA=", mimeType="audio/wav")
        output = await mcp_result_to_tool_output(_result([block]))

        assert "audio/wav" in output.data
        assert "UklGRgAAAAA=" not in output.data

    async def test_unknown_block_type_becomes_a_note(self) -> None:
        output = await mcp_result_to_tool_output(SimpleNamespace(
            content=[SimpleNamespace(type="hologram")], structuredContent=None, isError=False, meta=None,
        ))

        assert output.data == "[unsupported MCP content block: hologram]"


class TestImages:
    async def test_multimodal_context_returns_text_and_image_parts(self) -> None:
        output = await mcp_result_to_tool_output(_result([_text("chart"), _image()]), _context())

        assert isinstance(output.data, list)
        assert output.data[0] == TextPart(text="chart")
        image = output.data[1]
        assert isinstance(image, ImagePart)
        assert image.source.type == "base64"
        assert image.source.media_type == "image/png"
        assert image.source.data == _PNG_A

    async def test_text_only_model_gets_a_note_instead_of_pixels(self) -> None:
        output = await mcp_result_to_tool_output(_result([_text("chart"), _image()]), _context(multimodal=False))

        assert isinstance(output.data, str)
        assert output.data.startswith("chart\n")
        assert "does not accept images" in output.data
        assert _PNG_A not in output.data

    async def test_no_context_gets_a_note_instead_of_pixels(self) -> None:
        output = await mcp_result_to_tool_output(_result([_image()]))

        assert output.data == "[image returned by the tool not shown: this model does not accept images]"

    async def test_provider_without_multipart_tool_results_also_gets_pending_images(self) -> None:
        context = _context(supports_multipart_tool_result=False)
        output = await mcp_result_to_tool_output(_result([_image()]), context)

        assert any(isinstance(part, ImagePart) for part in output.data)
        assert context.tool_state["pending_tool_images"] == [
            {"image_url": {"url": f"data:image/png;base64,{_PNG_A}"}},
        ]

    async def test_image_over_the_request_cap_is_degraded_to_a_note(self) -> None:
        context = _context(image_admission=ImageAdmission(permissive_policy(1)))
        output = await mcp_result_to_tool_output(_result([_image(_PNG_A), _image(_PNG_B)]), context)

        assert sum(isinstance(part, ImagePart) for part in output.data) == 1
        notes = [part.text for part in output.data if isinstance(part, TextPart)]
        assert notes == ["[image returned by the tool not shown: over request cap]"]

    async def test_same_image_twice_is_sent_once(self) -> None:
        output = await mcp_result_to_tool_output(_result([_image(), _image()]), _context())

        assert sum(isinstance(part, ImagePart) for part in output.data) == 1
        notes = [part.text for part in output.data if isinstance(part, TextPart)]
        assert notes == ["[image returned by the tool not shown: duplicate]"]


class TestOversizedResults:
    async def test_a_huge_text_result_keeps_both_ends_and_says_how_much_was_cut(self) -> None:
        from app.agents.agent_loop.mcp_result import MAX_RESULT_CHARS

        text = "HEAD" + "x" * (MAX_RESULT_CHARS * 3) + "TAIL: next cursor=abc"

        output = await mcp_result_to_tool_output(_result(content=[_text(text)]))

        assert len(output.data) <= MAX_RESULT_CHARS
        assert output.data.startswith("HEAD")
        assert output.data.endswith("TAIL: next cursor=abc")
        assert "characters not shown" in output.data

    async def test_a_result_within_the_cap_is_untouched(self) -> None:
        output = await mcp_result_to_tool_output(_result(content=[_text("short answer")]))
        assert output.data == "short answer"

    async def test_huge_structured_content_becomes_capped_text(self) -> None:
        from app.agents.agent_loop.mcp_result import MAX_RESULT_CHARS

        structured = {"rows": ["r" * 1000 for _ in range(MAX_RESULT_CHARS // 500)]}

        output = await mcp_result_to_tool_output(_result(structured=structured))

        assert isinstance(output.data, str)
        assert len(output.data) <= MAX_RESULT_CHARS
        assert "characters not shown" in output.data

    async def test_small_structured_content_stays_an_object(self) -> None:
        output = await mcp_result_to_tool_output(_result(structured={"count": 2}))
        assert output.data == {"count": 2}

    async def test_a_huge_error_message_is_capped_too(self) -> None:
        from app.agents.agent_loop.mcp_result import MAX_RESULT_CHARS

        output = await mcp_result_to_tool_output(_result(content=[_text("e" * (MAX_RESULT_CHARS * 2))], is_error=True))

        assert output.success is False
        assert len(output.error) <= MAX_RESULT_CHARS
