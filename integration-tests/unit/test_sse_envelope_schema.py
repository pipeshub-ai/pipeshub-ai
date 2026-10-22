"""SSE envelopes validate against the OpenAPI event schemas as the server writes them.

No network, no services: real frames captured from a running instance, checked
against `pipeshub-openapi.yaml`. The spec types `data` as the JSON-encoded string
on the wire, so the raw envelope is the contract; callers decode `data` themselves.
These tests pin that so the spec and the contract tests can't drift apart again.
"""

from __future__ import annotations

import json

import pytest

from helper.agui_sse import _parse_frame
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
def test_conversation_frames_validate_as_written(raw: str) -> None:
    envelope = _parse_frame(raw)
    assert envelope is not None
    assert isinstance(json.loads(envelope["data"]), dict), "data must be a JSON-encoded object"
    assert_matches_component_schema(envelope, "ConversationStreamSSEEvent")
    # The same envelope shape is shared by every AG-UI stream family.
    assert_matches_component_schema(envelope, "ConversationMessageStreamSSEEvent")
    assert_matches_component_schema(envelope, "AgentStreamSSEEvent")
    assert_matches_component_schema(envelope, "AgentMessageStreamSSEEvent")
    assert_matches_component_schema(envelope, "AgentRegenerateSSEEvent")
    assert_matches_component_schema(envelope, "SSEEvent")


def test_upload_frame_validates_as_written() -> None:
    envelope = _parse_frame(_UPLOAD_FRAME)
    assert envelope is not None
    assert_matches_component_schema(envelope, "UploadStreamSSEEvent")


def test_embedding_download_frame_validates_as_written() -> None:
    """The eighth stream, and the only one whose payload the proxy extends."""
    envelope = _parse_frame(_EMBEDDING_DOWNLOAD_FRAME)
    assert envelope is not None
    payload = json.loads(envelope["data"])
    assert payload["timestamp"] == 1789072618883, "the proxy's added field must survive encoding"
    assert_matches_component_schema(envelope, "EmbeddingDownloadProgressSSEEvent")


def test_decoded_envelope_is_not_the_contract() -> None:
    """`data` is the encoded string, not the object it decodes to.

    If this starts passing, `data` has been typed as an object again, and
    generated SDKs stop handing callers the string they decode.
    """
    envelope = _parse_frame(_CONVERSATION_FRAMES[0])
    assert envelope is not None
    decoded = {"event": envelope["event"], "data": json.loads(envelope["data"])}
    with pytest.raises(AssertionError):
        assert_matches_component_schema(decoded, "ConversationStreamSSEEvent")
