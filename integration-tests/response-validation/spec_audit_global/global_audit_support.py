"""Operations and constants for the audit of behaviours the Node app applies to every route.

The operations come from the spec itself, so a newly documented operation is covered
without editing these tests.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pytest
import requests
from openapi_schema_validator import load_openapi_document
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI

API_PREFIX = "/api/v1"
METHODS = ("get", "post", "put", "patch", "delete")
JSON = "application/json"
FORM = "application/x-www-form-urlencoded"
JSON_HEADERS = {"Content-Type": JSON}
FORM_HEADERS = {"Content-Type": FORM}

MALFORMED_JSON_BODY = "{not json"
# express.json({ limit: '10mb' }) refuses one byte more than 10 MiB from Content-Length alone.
JSON_LIMIT_BYTES = 10 * 1024 * 1024
OVERSIZED_JSON_BODY = b'{"a":"' + b"x" * (JSON_LIMIT_BYTES - 7) + b'"}'
# express.urlencoded keeps qs's default parameterLimit of 1000.
FORM_PARAMETER_LIMIT = 1000

XSS_VALUE = "<b>spec-audit</b>"
XSS_MESSAGE = (
    "HTML tags, scripts, and XSS content are not allowed. Please remove any HTML tags and try again."
)
XSS_QUERY_PARAMETER = "specAuditMarkup"
INTERNAL_ERROR = "INTERNAL_ERROR"

DUMMY_OBJECT_ID = "0123456789abcdef01234567"
DUMMY_SEGMENT = "spec-audit-dummy"

# What each shared spec entry must say, so every operation describes the behaviour the same way.
XSS_DESCRIPTION_MARKER = "HTML tags, scripts, and XSS content are not allowed"
MALFORMED_JSON_DESCRIPTION = re.compile(r"(?i)does not parse|malformed json|not valid json|unparseable")
OVERSIZED_JSON_DESCRIPTION = re.compile(r"10 ?MB")
INSUFFICIENT_SCOPE_DESCRIPTION = re.compile(r"(?i)scope")
NO_ROUTE_PAGE_MARKER = "Cannot <METHOD> <path>"
TRIM_MARKER = "trims"

# xssSanitizationMiddleware skips these (path prefix, methods); every other route is checked.
XSS_EXEMPT: tuple[tuple[str, frozenset[str]], ...] = (
    ("/api/v1/agents/", frozenset({"POST", "PUT"})),
    ("/api/v1/conversations/", frozenset({"POST", "PUT"})),
    ("/api/v1/connectors", frozenset({"POST", "PUT"})),
    ("/api/v1/skills/", frozenset({"POST", "PUT", "PATCH"})),
)
XSS_EXEMPT_EXACT: tuple[tuple[str, frozenset[str]], ...] = (
    ("/api/v1/skills", frozenset({"POST", "PUT", "PATCH"})),
)

# The scopes requireScopes checks, where the oauth2 entries do not list exactly those.
# Upload: a later step needs kb:read too (the connector service's knowledge base lookup).
# The rest are SDK operations, which may carry only one oauth2 entry (Speakeasy refuses more); the
# alternative scope is named in their description instead.
GATE_SCOPES: dict[str, tuple[str, ...]] = {
    "POST /knowledgeBase/{kbId}/upload": ("kb:upload",),
    "GET /connectors/navigate": ("kb:read", "connector:read"),
    "GET /connectors/record/lookup": ("kb:read", "connector:read"),
    "GET /artifacts": ("kb:read", "connector:read"),
    "GET /artifacts/{artifactId}": ("kb:read", "connector:read"),
    "GET /artifacts/{artifactId}/versions": ("kb:read", "connector:read"),
}


@dataclass(frozen=True)
class Operation:
    method: str
    spec_path: str
    url_path: str
    operation: dict[str, Any]

    @property
    def id(self) -> str:
        return f"{self.method.upper()} {self.spec_path}"

    @property
    def xss_exempt(self) -> bool:
        method = self.method.upper()
        return any(self.url_path.startswith(p) and method in m for p, m in XSS_EXEMPT) or any(
            self.url_path == p and method in m for p, m in XSS_EXEMPT_EXACT
        )

    def response(self, status: str) -> dict[str, Any] | None:
        found = (self.operation.get("responses") or {}).get(status)
        return resolve(found) if isinstance(found, dict) else None

    def request_body(self) -> dict[str, Any]:
        body = self.operation.get("requestBody")
        return resolve(body) if isinstance(body, dict) else {}

    @property
    def media_types(self) -> list[str]:
        return [m.lower() for m in self.request_body().get("content") or {}]

    @property
    def oauth_scopes(self) -> list[str]:
        """Every scope named by any of the operation's oauth2 entries, in order, once each."""
        listed = [s for entry in self.operation.get("security") or [] for s in entry.get("oauth2") or []]
        return list(dict.fromkeys(listed))

    @property
    def oauth_entries(self) -> list[list[str]]:
        return [list(entry["oauth2"]) for entry in self.operation.get("security") or [] if "oauth2" in entry]

    @property
    def in_sdk(self) -> bool:
        return self.operation.get("x-pipeshub-sdk") is True

    @property
    def gate_scopes(self) -> list[str]:
        """The scopes requireScopes checks; any one of them passes it."""
        return list(GATE_SCOPES.get(self.id, self.oauth_scopes))


@lru_cache(maxsize=1)
def spec() -> dict[str, Any]:
    return load_openapi_document()


def resolve(node: dict[str, Any]) -> dict[str, Any]:
    while isinstance(node.get("$ref"), str):
        target: Any = spec()
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        node = target
    return node


_TEXT, _NON_TEXT, _NEUTRAL = "text", "non-text", "neutral"


def text_only_problem(schema: Any) -> str | None:
    """Where a body schema admits a value that is not a string, or None when every leaf is a string.

    A form body arrives as flat strings: qs makes a list only for a repeated key and an object only for
    bracketed keys, and cannot send an empty list, so only a body of flat text fields describes one.
    """
    verdict, where = _text_verdict(schema, overlay=False, at="body")
    return None if verdict == _TEXT else (where or "body: no typed value")


def _text_verdict(schema: Any, *, overlay: bool, at: str) -> tuple[str, str | None]:
    # overlay: a branch of allOf/anyOf/oneOf refining a typed base, so an untyped member is only a constraint.
    if isinstance(schema, dict):
        schema = resolve(schema)
    if schema is True or schema == {}:
        return (_NEUTRAL, None) if overlay else (_NON_TEXT, f"{at}: any value")
    if not isinstance(schema, dict):
        return _NON_TEXT, f"{at}: not a schema"
    declared = schema.get("type")
    types = set(declared) if isinstance(declared, list) else ({declared} if declared else set())
    types.discard("null")
    if not types and ("properties" in schema or "additionalProperties" in schema):
        types = {"object"}
    if not types and "items" in schema:
        types = {"array"}
    if types - {"string", "object", "array"}:
        return _NON_TEXT, f"{at}: {sorted(types)}"
    if any(not isinstance(v, str) for v in schema.get("enum") or [] if v is not None):
        return _NON_TEXT, f"{at}: enum with a value that is not a string"
    found = "string" in types or "enum" in schema
    # qs (express.urlencoded extended) gives a list only for a repeated key and an object only for
    # bracketed keys, and cannot send an empty list: only flat text fields describe a form body.
    if "array" in types:
        return _NON_TEXT, f"{at}: array"
    if "object" in types and not re.fullmatch(r"body(<[^>]+>)*", at):
        return _NON_TEXT, f"{at}: nested object"
    if "object" in types:
        properties = schema.get("properties") or {}
        for name, sub in properties.items():
            verdict, where = _text_verdict(sub, overlay=overlay, at=f"{at}.{name}")
            if verdict == _NON_TEXT or (verdict == _NEUTRAL and not overlay):
                return _NON_TEXT, where or f"{at}.{name}: any value"
        extra = schema.get("additionalProperties")
        combined = any(k in schema for k in ("oneOf", "anyOf", "allOf"))
        if isinstance(extra, dict):
            verdict, where = _text_verdict(extra, overlay=False, at=f"{at}.*")
            if verdict != _TEXT:
                return _NON_TEXT, where or f"{at}.*: any value"
        elif extra is True or (extra is None and not properties and not overlay and not combined):
            return _NON_TEXT, f"{at}: free-form object"
        found = True
    if "array" in types:
        verdict, where = _text_verdict(schema.get("items", {}), overlay=overlay, at=f"{at}[]")
        if verdict == _NON_TEXT or (verdict == _NEUTRAL and not overlay):
            return _NON_TEXT, where or f"{at}[]: any value"
        found = True
    for keyword in ("oneOf", "anyOf", "allOf"):
        for i, sub in enumerate(schema.get(keyword) or []):
            verdict, where = _text_verdict(sub, overlay=overlay or found, at=f"{at}<{keyword}[{i}]>")
            if verdict == _NON_TEXT:
                return _NON_TEXT, where
            found = found or verdict == _TEXT
    return (_TEXT, None) if found else (_NEUTRAL, None)


def _dummy(param: dict[str, Any]) -> str:
    schema = resolve(param.get("schema") or {})
    if schema.get("enum"):
        return str(schema["enum"][0])
    name = param["name"]
    if name == "version":
        return "1"
    if name.lower().endswith("id") or name == "id":
        return DUMMY_OBJECT_ID
    return DUMMY_SEGMENT


def _is_root(operation: dict[str, Any]) -> bool:
    return any(s.get("url") in ("/", "{instance_url}") for s in operation.get("servers") or [])


@lru_cache(maxsize=1)
def operations() -> tuple[Operation, ...]:
    out = []
    for spec_path, item in spec()["paths"].items():
        for method in METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            params = [resolve(p) for p in (item.get("parameters") or []) + (operation.get("parameters") or [])]
            values = {p["name"]: _dummy(p) for p in params if p.get("in") == "path"}
            url_path = re.sub(r"\{([^}/]+)\}", lambda m: values.get(m.group(1), DUMMY_SEGMENT), spec_path)
            if not _is_root(operation):
                url_path = API_PREFIX + url_path
            out.append(Operation(method, spec_path, url_path, operation))
    return tuple(out)


def find(method: str, spec_path: str) -> Operation:
    return next(op for op in operations() if op.method == method and op.spec_path == spec_path)


def params_for(ops: list[Operation] | tuple[Operation, ...]) -> list[Any]:
    return [pytest.param(op, id=op.id) for op in ops]


def call(
    base_url: str, op: Operation, *, timeout: float, headers: dict[str, str] | None = None, **kwargs: Any
) -> requests.Response:
    return requests.request(op.method.upper(), f"{base_url}{op.url_path}", headers=headers or {}, timeout=timeout, **kwargs)


def error_of(resp: requests.Response) -> dict[str, Any]:
    body = resp.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), resp.text[:500]
    return body["error"]


def mint_token(base_url: str, timeout: float, scope: str) -> str:
    """A client-credentials token of the suite's own OAuth client asking for ``scope``.

    The token endpoint drops `openid` for a client-credentials grant, so `openid` alone grants the empty set.
    """
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": scope,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a token for {scope!r}: HTTP {resp.status_code}"
    granted = resp.json().get("scope")
    expected = "" if scope == "openid" else scope
    assert granted == expected, f"asked for {scope!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
