"""Fake Anthropic-on-Bedrock and Anthropic-on-Vertex endpoints (E3c), refusing what the real ones refuse.

``FakeBedrock`` serves ``/model/{id}/invoke`` and ``/invoke-with-response-stream``
(AWS event-stream frames, as the Bedrock runtime sends them) and refuses a request
without a SigV4 ``Authorization`` for the expected key id, region and service. Given
the account's secret, it recomputes the whole signature, and it also serves the
Converse API (``/converse``, ``/converse-stream``, P4.3) from scripted replies.
``FakeVertex`` is Google's token endpoint (a JWT-bearer assertion, verified against
the service account's public key) and Vertex's ``:rawPredict`` /
``:streamRawPredict``, refusing a bearer it did not issue. Both record what they saw.
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

ACCESS_TOKEN = "ya29.fake-vertex-access-token"
GOOGLE_AUDIENCE = "https://oauth2.googleapis.com/token"
_JSON = "application/json"


def message_events(model: str, text: str = "hello from the cloud") -> list[dict[str, Any]]:
    """One text answer as the Messages API streams it."""
    return [
        {"type": "message_start", "message": {"id": "msg_cloud", "type": "message", "role": "assistant", "model": model,
                                              "content": [], "stop_reason": None, "stop_sequence": None,
                                              "usage": {"input_tokens": 12, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]


def message_body(model: str, text: str = "hello from the cloud") -> dict[str, Any]:
    return {"id": "msg_cloud", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 7}}


def _header(name: str, value: str) -> bytes:
    raw_name, raw_value = name.encode(), value.encode()
    return struct.pack("B", len(raw_name)) + raw_name + struct.pack("!BH", 7, len(raw_value)) + raw_value


def eventstream_frame(payload: bytes, event_type: str = "chunk", *, message_type: str = "event") -> bytes:
    """One AWS event-stream message (prelude, CRCs, string headers), as ``botocore.eventstream`` reads it.
    An ``exception`` message names its type in ``:exception-type``, as the Bedrock runtime sends it."""
    kind = ":exception-type" if message_type == "exception" else ":event-type"
    headers = _header(kind, event_type) + _header(":content-type", _JSON) + _header(":message-type", message_type)
    total = 12 + len(headers) + len(payload) + 4
    prelude = struct.pack("!II", total, len(headers))
    prelude += struct.pack("!I", binascii.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + payload
    return message + struct.pack("!I", binascii.crc32(message) & 0xFFFFFFFF)


def bedrock_stream(events: list[dict[str, Any]]) -> bytes:
    return b"".join(
        eventstream_frame(json.dumps({"bytes": base64.b64encode(json.dumps(e).encode()).decode()}).encode())
        for e in events
    )


@dataclass
class Seen:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass(frozen=True)
class ConverseReply:
    """One scripted Converse answer: a whole ``converse`` body, or ``converse-stream`` events
    (``(event type, payload)``) optionally cut by an exception frame, or an HTTP error."""

    body: dict[str, Any] | None = None
    events: tuple[tuple[str, dict[str, Any]], ...] = ()
    exception: tuple[str, str] | None = None
    status: int = 200
    error_type: str = ""


def converse_body(text: str = "hello from converse", *, stop: str = "end_turn", content: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content if content is not None else [{"text": text}]}},
        "stopReason": stop,
        "usage": {"inputTokens": 12, "outputTokens": 7, "totalTokens": 19, "cacheReadInputTokens": 3, "cacheWriteInputTokens": 0},
        "metrics": {"latencyMs": 5},
    }


def converse_stream(events: tuple[tuple[str, dict[str, Any]], ...], exception: tuple[str, str] | None = None) -> bytes:
    frames = b"".join(eventstream_frame(json.dumps(payload).encode(), event_type) for event_type, payload in events)
    if exception is not None:
        kind, message = exception
        frames += eventstream_frame(json.dumps({"message": message}).encode(), kind, message_type="exception")
    return frames


_INVALID_SIGNATURE = {"message": "The request signature we calculated does not match the signature you provided."}


@dataclass
class FakeBedrock:
    access_key_id: str
    region: str
    seen: list[Seen] = field(default_factory=list[Seen])
    # Set: the whole SigV4 signature is recomputed with the account's secret and must match.
    secret_access_key: str | None = None
    converse: list[ConverseReply] = field(default_factory=list[ConverseReply])

    def _signature_matches(self, request: web.Request, headers: dict[str, str], raw: bytes) -> bool:
        from datetime import UTC, datetime

        from harness_core.models.sigv4 import AwsCredentials, sign

        auth = headers.get("authorization", "")
        signed = auth.partition("SignedHeaders=")[2].partition(",")[0].split(";")
        try:
            now = datetime.strptime(headers.get("x-amz-date", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            return False
        expected = sign(
            request.method,
            f"{request.scheme}://{request.host}{request.raw_path}",
            {name: headers.get(name, "") for name in signed if name != "x-amz-date"},
            raw,
            credentials=AwsCredentials(self.access_key_id, self.secret_access_key or ""),
            region=self.region,
            service="bedrock",
            now=now,
        )
        return expected["authorization"] == auth

    async def _invoke(self, request: web.Request) -> web.StreamResponse:
        headers = {k.lower(): v for k, v in request.headers.items()}
        raw = await request.read()
        body = json.loads(raw)
        self.seen.append(Seen(request.path, headers, body))
        scope = f"Credential={self.access_key_id}/"
        auth = headers.get("authorization", "")
        if not auth.startswith("AWS4-HMAC-SHA256 ") or scope not in auth or f"/{self.region}/bedrock/aws4_request" not in auth:
            return web.json_response({"message": "The security token included in the request is invalid."}, status=403)
        if self.secret_access_key is not None and not self._signature_matches(request, headers, raw):
            return web.json_response(_INVALID_SIGNATURE, status=403, headers={"x-amzn-ErrorType": "InvalidSignatureException"})
        action = request.match_info["action"]
        if action in ("converse", "converse-stream"):
            return self._converse(action)
        model = request.match_info["model"]
        if request.match_info["action"] == "invoke-with-response-stream":
            return web.Response(body=bedrock_stream(message_events(model)), content_type="application/vnd.amazon.eventstream")
        return web.json_response(message_body(model))

    def _converse(self, action: str) -> web.StreamResponse:
        reply = self.converse.pop(0) if self.converse else ConverseReply(body=converse_body())
        if reply.status != 200:
            return web.json_response(
                {"message": f"scripted {reply.error_type or 'error'}"},
                status=reply.status,
                headers={"x-amzn-ErrorType": reply.error_type} if reply.error_type else None,
            )
        if action == "converse-stream":
            return web.Response(
                body=converse_stream(reply.events, reply.exception), content_type="application/vnd.amazon.eventstream"
            )
        return web.json_response(reply.body or converse_body())

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/model/{model}/{action}", self._invoke)
        return app


@dataclass(frozen=True)
class ServiceAccount:
    """A generated service account: its JSON (as an admin uploads it) and the public key Google would hold."""

    json: str
    public_key_pem: bytes
    client_email: str


def service_account(token_uri: str, *, project_id: str = "proj-e3c-test") -> ServiceAccount:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    email = f"gateway@{project_id}.iam.gserviceaccount.com"
    info = {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "0123456789abcdef0123456789abcdef01234567",
        "private_key": private,
        "client_email": email,
        "client_id": "100000000000000000000",
        "token_uri": token_uri,
    }
    return ServiceAccount(json.dumps(info), public, email)


@dataclass
class FakeVertex:
    public_key_pem: bytes
    issuer: str
    token_uri: str = ""
    token_requests: int = 0
    seen: list[Seen] = field(default_factory=list[Seen])

    async def _token(self, request: web.Request) -> web.Response:
        import jwt

        self.token_requests += 1
        form = await request.post()
        if form.get("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer":
            return web.json_response({"error": "unsupported_grant_type"}, status=400)
        try:
            # google-auth addresses every assertion to Google's token endpoint, wherever it posts it.
            claims = jwt.decode(
                str(form.get("assertion")), self.public_key_pem, algorithms=["RS256"], audience=GOOGLE_AUDIENCE
            )
        except jwt.PyJWTError:
            return web.json_response({"error": "invalid_grant"}, status=400)
        if claims.get("iss") != self.issuer:
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": ACCESS_TOKEN, "expires_in": 3600, "token_type": "Bearer"})

    async def _predict(self, request: web.Request) -> web.StreamResponse:
        headers = {k.lower(): v for k, v in request.headers.items()}
        body = await request.json()
        self.seen.append(Seen(request.path, headers, body))
        if headers.get("authorization") != f"Bearer {ACCESS_TOKEN}":
            return web.json_response({"error": {"code": 401, "status": "UNAUTHENTICATED"}}, status=401)
        model, _, action = request.match_info["tail"].rpartition("/")[2].partition(":")
        if action == "streamRawPredict":
            payload = b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in message_events(model))
            return web.Response(body=payload, content_type="text/event-stream")
        return web.json_response(message_body(model))

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/token", self._token)
        app.router.add_post("/v1/projects/{tail:.*}", self._predict)
        return app


__all__ = [
    "ACCESS_TOKEN",
    "ConverseReply",
    "FakeBedrock",
    "FakeVertex",
    "Seen",
    "ServiceAccount",
    "bedrock_stream",
    "converse_body",
    "converse_stream",
    "eventstream_frame",
    "message_body",
    "message_events",
    "service_account",
]
