"""Constants and helpers for the strict OpenAPI audit of /api/v1/configurationManager."""

from __future__ import annotations

import copy
import uuid
from functools import lru_cache
from typing import Any, Callable

import requests
import strict_openapi
from openapi_schema_validator import (
    _make_registry,
    adapt_openapi_nullable,
    load_openapi_document,
)
from referencing import Registry

from helper.second_user import SecondUser

CONFIGURATION_MANAGER_BASE = "/api/v1/configurationManager"

# Slack bot config ids and web search provider keys are server-made UUIDs; the
# validators only require a non-empty string, so no id is "malformed" for them.
MISSING_SLACK_BOT_CONFIG_ID = "00000000-0000-4000-8000-000000000000"
MISSING_WEB_SEARCH_PROVIDER_KEY = "00000000-0000-4000-8000-000000000001"
# The built-in provider: always listed, never stored, accepted by PUT /web-search/default/:providerKey.
BUILTIN_WEB_SEARCH_PROVIDER_KEY = "duckduckgo"

UNKNOWN_AI_PROVIDER_ID = "spec-audit-no-such-provider"
# Decodes to "a%b", which guardPathParams(router, 'providerId') refuses.
MALFORMED_AI_PROVIDER_ID = "a%25b"

# Passes MODEL_NAME_PATTERN; nothing by this name exists on the embedding server.
UNKNOWN_EMBEDDING_MODEL = "spec-audit/no-such-model"
MALFORMED_EMBEDDING_MODEL = "not a model name"

# GET /public/desktopFrontendUrl answers 403 without this header.
DESKTOP_CLIENT_HEADERS = {"client-name": "desktop"}
INVALID_BEARER_HEADERS = {"Authorization": "Bearer invalid-token"}

# What every admin-facing read returns in place of a stored secret.
SECRET_PLACEHOLDER = "****************"

SeedSlackBot = Callable[..., dict[str, Any]]
MetricsCollectionConfig = dict[str, Any]

_OPENAPI_ANNOTATION_KEYS = frozenset({"example", "examples", "discriminator", "xml", "externalDocs"})


def slack_bot_body(**overrides: Any) -> dict[str, Any]:
    """A body createSlackBotConfigSchema accepts; no agentId, so it never collides."""
    body: dict[str, Any] = {
        "name": f"spec-audit {uuid.uuid4().hex[:8]}",
        "botToken": f"xoxb-spec-audit-{uuid.uuid4().hex}",
        "signingSecret": uuid.uuid4().hex,
    }
    body.update(overrides)
    return body


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a configurationManager route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = {**user.headers, **(kwargs.pop("headers", None) or {})}
    if kwargs.get("files"):
        # requests must set the multipart boundary itself.
        headers.pop("Content-Type", None)
    return requests.request(
        method,
        f"{user.base_url}{CONFIGURATION_MANAGER_BASE}{path}",
        headers=headers,
        **kwargs,
    )


def _strip_annotations(node: Any, *, field_names: bool = False) -> Any:
    """Drop OpenAPI annotation keys, except where the key is a field name in a ``properties`` map."""
    if isinstance(node, list):
        return [_strip_annotations(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _strip_annotations(value, field_names=key == "properties" and not field_names)
        for key, value in node.items()
        if field_names or key not in _OPENAPI_ANNOTATION_KEYS
    }


@lru_cache(maxsize=1)
def _spec_keeping_field_names() -> tuple[dict[str, Any], Registry]:
    doc = adapt_openapi_nullable(_strip_annotations(copy.deepcopy(load_openapi_document())))
    return doc, _make_registry(doc)


def assert_strict_openapi_response_keeping_field_names(resp: requests.Response, path: str) -> None:
    """``assert_strict_openapi_response`` for a body that has a field literally named ``examples``.

    The shared helper removes every ``examples`` key from the spec before checking, including
    the documented ``AIModelFieldSchema.properties.examples``, and then reports that field as
    undocumented. This runs the same strict check on a spec that keeps field names.
    """
    doc, registry = _spec_keeping_field_names()
    problems = strict_openapi.strict_response_problems(
        doc,
        registry,
        resp.request.method or "",
        path,
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        resp.content,
    )
    if problems:
        raise AssertionError(f"{len(problems)} OpenAPI problem(s):\n" + "\n".join(problems))
