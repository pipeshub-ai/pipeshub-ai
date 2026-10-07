"""Operations and constants for the audit of behaviours the Node app applies to every route.

The operations come from the spec itself, so a newly documented operation is covered
without editing these tests.
"""

from __future__ import annotations

import hashlib
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

# Operations whose oauth2 entry lists more scopes than requireScopes checks; a later step needs the
# rest (before an upload, the connector service's knowledge base lookup needs kb:read).
GATE_SCOPES: dict[str, tuple[str, ...]] = {
    "POST /knowledgeBase/{kbId}/upload": ("kb:upload",),
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
        return [s for entry in self.operation.get("security") or [] for s in entry.get("oauth2") or []]

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


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
