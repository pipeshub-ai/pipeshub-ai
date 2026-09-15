"""SSE envelopes validate against the OpenAPI event schemas in the order generated SDKs parse them.

No network, no services: real frames captured from a running instance, checked
against `pipeshub-openapi.yaml` the way Speakeasy's runtime does it — the `data:`
line is JSON-decoded first, then the envelope is validated. A schema that types
`data` as a string passes the raw envelope and fails every SDK; this test pins
the decoded shape so the spec and the contract tests can't drift apart again.
"""

from __future__ import annotations

import pytest

from helper.agui_sse import _parse_frame, decode_sse_envelope
from helper.openapi_search_validator import assert_matches_component_schema

pytestmark = pytest.mark.unit

# Frames as the server writes them (PipesHub 0.7.0), one per stream family.
_CONVERSATION_FRAMES = [
    (
        "event: CUSTOM\n"
        'data: {"type":"CUSTOM","name":"conversation_created","value":{"conversationId":"6aa58730c4bc1a33ef5acabf","title":"On-call policy"}}'
    ),
    (
        "event: RUN_STARTED\n"
        'data: {"type": "RUN_STARTED", "threadId": "6aa58730c4bc1a33ef5acabf", "runId": "fb9da315-f0bc-44d9-8343-8dfc8090424e"}'
    ),
    (
        "event: TEXT_MESSAGE_CONTENT\n"
        'data: {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "On-call over a public holiday is voluntary"}'
    ),
    (
        "event: RUN_ERROR\n"
        'data: {"type": "RUN_ERROR", "message": "LLM configuration is missing", "code": "llm_not_configured"}'
    ),
]

_UPLOAD_FRAME = (
    "event: file:succeeded\n"
    'data: {"recordId":"a9dbc392-e907-44ed-8a54-7a23b92e0aa1","fileName":"probe","filePath":"probe.md","extension":"md"}'
)

# As written by the Node proxy for GET /configurationManager/ai-models/download-progress:
# the embedding server's payload with a `timestamp` added on each poll
# (cm_controller.ts, `event: progress`).
_EMBEDDING_DOWNLOAD_FRAME = (
    "event: progress\n"
    'data: {"model":"BAAI/bge-large-en-v1.5","status":"downloading","progress":42.5,'
    '"downloaded_bytes":567000000,"total_bytes":1340000000,"error":null,"timestamp":1789072618883}'
)


@pytest.mark.parametrize("raw", _CONVERSATION_FRAMES, ids=lambda r: r.split("\n", 1)[0].split(": ")[1])
def test_conversation_frames_validate_after_decoding(raw: str) -> None:
    envelope = _parse_frame(raw)
    assert envelope is not None
    decoded = decode_sse_envelope(envelope)
    assert isinstance(decoded["data"], dict), "data must be the decoded object, not the raw line"
    assert_matches_component_schema(decoded, "ConversationStreamSSEEvent")
    # The same envelope shape is shared by every AG-UI stream family.
    assert_matches_component_schema(decoded, "ConversationMessageStreamSSEEvent")
    assert_matches_component_schema(decoded, "AgentStreamSSEEvent")
    assert_matches_component_schema(decoded, "AgentMessageStreamSSEEvent")
    assert_matches_component_schema(decoded, "AgentRegenerateSSEEvent")
    assert_matches_component_schema(decoded, "SSEEvent")


def test_upload_frame_validates_after_decoding() -> None:
    envelope = _parse_frame(_UPLOAD_FRAME)
    assert envelope is not None
    assert_matches_component_schema(decode_sse_envelope(envelope), "UploadStreamSSEEvent")


def test_embedding_download_frame_validates_after_decoding() -> None:
    """The eighth stream, and the only one whose payload the proxy extends."""
    envelope = _parse_frame(_EMBEDDING_DOWNLOAD_FRAME)
    assert envelope is not None
    decoded = decode_sse_envelope(envelope)
    assert isinstance(decoded["data"], dict)
    assert decoded["data"]["timestamp"] == 1789072618883, "the proxy's added field must survive decoding"
    assert_matches_component_schema(decoded, "EmbeddingDownloadProgressSSEEvent")


def test_raw_envelope_is_not_the_contract() -> None:
    """The raw envelope carries `data` as a string; that is not what SDKs validate.

    If this starts passing, `data` has been typed as a string again and every
    generated stream client will fail on its first event.
    """
    envelope = _parse_frame(_CONVERSATION_FRAMES[0])
    assert envelope is not None
    with pytest.raises(AssertionError):
        assert_matches_component_schema(envelope, "ConversationStreamSSEEvent")
