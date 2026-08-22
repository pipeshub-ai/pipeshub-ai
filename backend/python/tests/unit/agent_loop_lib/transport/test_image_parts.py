"""Images must reach the provider, on every transport.

`shape_image_injection` (a PRE_MODEL hook) replaces a text placeholder with real
image data precisely so the model can see an attachment. Every OpenAI-family
formatter used to reduce `list[Part]` with `" ".join(getattr(p, "text", ""))`,
and an ImagePart has no `.text` -- so the image became an empty string and the
model answered about a picture it never received. Anthropic was the only
transport that carried it.
"""

from __future__ import annotations

import base64

import pytest

from app.agent_loop_lib.core.messages import (
    ImagePart,
    ImageSource,
    TextPart,
    UserMessage,
)
from app.agent_loop_lib.transport.gemini import GeminiTransport
from app.agent_loop_lib.transport.openai import OpenAITransport
from app.agent_loop_lib.transport.openai_responses import format_responses_input

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def _message_with_image(source: ImageSource) -> UserMessage:
    return UserMessage(content=[
        TextPart(text="what is in this image?"),
        ImagePart(source=source),
    ])


class TestChatCompletions:
    def test_base64_image_becomes_a_data_url_block(self) -> None:
        t = OpenAITransport(api_key="k")
        out = t._format_message(
            _message_with_image(ImageSource(type="base64", media_type="image/png", data=PNG))
        )
        blocks = out["content"]
        assert isinstance(blocks, list), "an image forces the array content form"
        image = next(b for b in blocks if b["type"] == "image_url")
        assert image["image_url"]["url"].startswith("data:image/png;base64,")
        assert any(b["type"] == "text" for b in blocks), "the question must survive"

    def test_url_image_is_passed_through(self) -> None:
        t = OpenAITransport(api_key="k")
        out = t._format_message(
            _message_with_image(ImageSource(type="url", data="https://x/y.png"))
        )
        image = next(b for b in out["content"] if b["type"] == "image_url")
        assert image["image_url"]["url"] == "https://x/y.png"

    def test_text_only_message_stays_a_plain_string(self) -> None:
        """Only an image should force the array form -- prompt caching and every
        existing expectation depend on text staying a string."""
        t = OpenAITransport(api_key="k")
        out = t._format_message(UserMessage(content=[TextPart(text="hello")]))
        assert out["content"] == "hello"


class TestResponsesApi:
    def test_image_becomes_an_input_image_block(self) -> None:
        items = format_responses_input(
            [_message_with_image(ImageSource(type="base64", media_type="image/png", data=PNG))],
            None,
        )
        blocks = items[0]["content"]
        assert isinstance(blocks, list)
        image = next(b for b in blocks if b["type"] == "input_image")
        # the Responses API takes a bare string here, unlike Chat Completions
        assert isinstance(image["image_url"], str)
        assert image["image_url"].startswith("data:image/png;base64,")
        assert any(b["type"] == "input_text" for b in blocks)

    def test_text_only_stays_a_string(self) -> None:
        items = format_responses_input([UserMessage(content=[TextPart(text="hi")])], None)
        assert items[0]["content"] == "hi"


class TestGemini:
    def test_base64_image_is_decoded_to_bytes(self) -> None:
        """Gemini takes raw bytes with a mime type, not a data: URL."""
        t = GeminiTransport(api_key="k")
        contents = t._format_contents(
            [_message_with_image(ImageSource(type="base64", media_type="image/png", data=PNG))]
        )
        parts = contents[0].parts
        blob = next(p.inline_data for p in parts if p.inline_data is not None)
        assert blob.mime_type == "image/png"
        assert blob.data == base64.b64decode(PNG)
        assert any(p.text for p in parts), "the question must survive"

    def test_url_image_becomes_file_data(self) -> None:
        t = GeminiTransport(api_key="k")
        contents = t._format_contents(
            [_message_with_image(ImageSource(type="url", media_type="image/png",
                                             data="https://x/y.png"))]
        )
        file_data = next(
            p.file_data for p in contents[0].parts if p.file_data is not None
        )
        assert file_data.file_uri == "https://x/y.png"

    def test_undecodable_image_does_not_take_the_turn_down(self) -> None:
        t = GeminiTransport(api_key="k")
        contents = t._format_contents(
            [_message_with_image(ImageSource(type="base64", media_type="image/png",
                                             data="!!!not base64!!!"))]
        )
        # the text still goes, the bad attachment is dropped
        assert any(p.text for p in contents[0].parts)


@pytest.mark.parametrize("source_type", ["base64", "url"])
def test_no_transport_silently_drops_an_image(source_type: str) -> None:
    """The regression this file exists for: an image that reaches none of the
    provider payloads is invisible -- no error, just a worse answer."""
    source = (ImageSource(type="base64", media_type="image/png", data=PNG)
              if source_type == "base64"
              else ImageSource(type="url", media_type="image/png", data="https://x/y.png"))
    msg = _message_with_image(source)

    chat = OpenAITransport(api_key="k")._format_message(msg)
    assert any(b["type"] == "image_url" for b in chat["content"])

    responses = format_responses_input([msg], None)[0]["content"]
    assert any(b["type"] == "input_image" for b in responses)

    gem = GeminiTransport(api_key="k")._format_contents([msg])[0].parts
    assert any(p.inline_data is not None or p.file_data is not None for p in gem)
