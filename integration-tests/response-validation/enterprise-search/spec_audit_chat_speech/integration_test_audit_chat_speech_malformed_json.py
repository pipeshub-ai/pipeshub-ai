"""Every /api/v1/chat speech route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches authentication or the speech service.
"""

from __future__ import annotations

import pytest
from chat_speech_audit_support import (
    CAPABILITIES_ROUTE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    SPEAK_ROUTE,
    TRANSCRIBE_ROUTE,
    ChatSpeechClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "/speech/capabilities", CAPABILITIES_ROUTE),
        ("POST", "/speak", SPEAK_ROUTE),
        ("POST", "/transcribe", TRANSCRIBE_ROUTE),
    ],
    ids=["capabilities", "speak", "transcribe"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    chat_speech_client: ChatSpeechClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    send = chat_speech_client.get if method == "GET" else chat_speech_client.post
    resp = send(sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
