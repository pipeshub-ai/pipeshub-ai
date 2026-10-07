"""Constants and helpers for the strict OpenAPI audit of /api/v1/configurationManager."""

from __future__ import annotations

import copy
import hashlib
import os
import uuid
from functools import lru_cache
from typing import Any, Callable

import pytest
import redis
import requests
import strict_openapi
from openapi_schema_validator import (
    _make_registry,
    adapt_openapi_nullable,
    load_openapi_document,
)
from pymongo import MongoClient
from referencing import Registry

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.second_user import SecondUser

CONFIGURATION_MANAGER_BASE = "/api/v1/configurationManager"

# Slack bot config ids and web search provider keys are server-made UUIDs; the
# validators only require a non-empty string, so no id is "malformed" for them.
MISSING_SLACK_BOT_CONFIG_ID = "00000000-0000-4000-8000-000000000000"
MISSING_WEB_SEARCH_PROVIDER_KEY = "00000000-0000-4000-8000-000000000001"
# The built-in provider: always listed, never stored, accepted by PUT /web-search/default/:providerKey.
BUILTIN_WEB_SEARCH_PROVIDER_KEY = "duckduckgo"

MISSING_AI_MODEL_KEY = "00000000-0000-4000-8000-000000000002"

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

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# Where the controller keeps each config no route can delete (configuration_manager/paths/paths.ts).
# The connector ones are per organization: the org id is appended as one more path segment.
KV_STORAGE = "/services/storage"
KV_SMTP = "/services/smtp"
KV_AUTH_AZURE_AD = "/services/auth/azureAd"
KV_AUTH_MICROSOFT = "/services/auth/microsoft"
KV_AUTH_GOOGLE = "/services/auth/google"
KV_AUTH_SSO = "/services/auth/sso"
KV_AUTH_OAUTH = "/services/auth/oauth"
KV_CONNECTOR_ATLASSIAN = "/services/connectors/atlassian/config"
KV_CONNECTOR_ONEDRIVE = "/services/connectors/onedrive/config"
KV_CONNECTOR_SHAREPOINT = "/services/connectors/sharepoint/config"
KV_GOOGLE_WORKSPACE_OAUTH = "/services/connectors/googleWorkspace/oauth/config"
# The organization id is appended as one more path segment.
KV_GOOGLE_WORKSPACE_BUSINESS = "/services/connectors/googleWorkspace/credentials/business"
KV_SYSTEM_PROMPTS = "/services/systemPrompts"
# Encrypted JSON; GET /metricsCollection returns it decrypted.
KV_METRICS_COLLECTION = "/services/metricsCollection"
# Plain JSON shared by the frontend and connector public URLs (and other endpoints).
KV_ENDPOINTS = "/services/endpoints"
# Encrypted JSON of the stored web search providers and settings.
KV_WEB_SEARCH = "/services/webSearch"

_KV_INVALIDATION_CHANNEL = "pipeshub:cache:invalidate"

# Not a real certificate: the route stores the text and builds the SAML strategy without parsing it.
SSO_CERTIFICATE_BODY = "MIIBspecAuditNotARealCertificate0123456789+/AAAA"
SSO_CERTIFICATE_PEM = (
    f"-----BEGIN CERTIFICATE-----\n{SSO_CERTIFICATE_BODY[:24]}\n{SSO_CERTIFICATE_BODY[24:]}\n-----END CERTIFICATE-----\n"
)
# Port 9 (discard) and the "spec-audit-dummy" marker: a leftover is recognisable and leads nowhere.
SSO_VALID_BODY: dict[str, Any] = {
    "entryPoint": "http://127.0.0.1:9/spec-audit-dummy-idp/sso",
    "certificate": SSO_CERTIFICATE_PEM,
    "emailKey": "email",
    "enableJit": False,
    "samlPlatform": "spec-audit-dummy",
}
# GET /authConfig/sso adds this to whatever is stored; POST does not take it.
SSO_DERIVED_FIELDS = ("spEntityId",)

# (method, path under the router) of every operation the two request-wide checks below cover:
# the JSON body parser and the HTML filter both run before the router, whatever the route.
PRE_ROUTER_OPERATIONS: list[tuple[str, str]] = [
    ("POST", "/storageConfig"),
    ("GET", "/storageConfig"),
    ("POST", "/smtpConfig"),
    ("GET", "/smtpConfig"),
    ("GET", "/smtpConfig/status"),
    ("GET", "/connectors/atlassian/config"),
    ("POST", "/connectors/atlassian/config"),
    ("GET", "/connectors/onedrive/config"),
    ("POST", "/connectors/onedrive/config"),
    ("GET", "/connectors/sharepoint/config"),
    ("POST", "/connectors/sharepoint/config"),
    ("GET", "/authConfig/azureAd"),
    ("POST", "/authConfig/azureAd"),
    ("GET", "/authConfig/microsoft"),
    ("POST", "/authConfig/microsoft"),
    ("GET", "/authConfig/google"),
    ("POST", "/authConfig/google"),
    ("GET", "/authConfig/sso"),
    ("POST", "/authConfig/sso"),
    ("GET", "/authConfig/oauth"),
    ("POST", "/authConfig/oauth"),
    ("POST", "/platform/settings"),
    ("GET", "/platform/settings"),
    ("GET", "/platform/feature-flags/available"),
    ("GET", "/platform/feature-flags/effective"),
    ("GET", "/slack-bot"),
    ("POST", "/slack-bot"),
    ("PUT", "/slack-bot/:configId"),
    ("DELETE", "/slack-bot/:configId"),
    ("GET", "/prompts/system"),
    ("PUT", "/prompts/system"),
    ("POST", "/connectors/googleWorkspaceCredentials"),
    ("GET", "/connectors/googleWorkspaceCredentials"),
    ("GET", "/connectors/googleWorkspaceOauthConfig"),
    ("POST", "/connectors/googleWorkspaceOauthConfig"),
    ("POST", "/aiModelsConfig"),
    ("GET", "/aiModelsConfig"),
    ("GET", "/ai-models/registry/capabilities"),
    ("GET", "/ai-models/registry/:providerId/schema"),
    ("GET", "/ai-models/registry"),
    ("GET", "/ai-models"),
    ("GET", "/ai-models/roles"),
    ("PUT", "/ai-models/roles"),
    ("GET", "/ai-models/download-progress"),
    ("GET", "/ai-models/:modelType"),
    ("GET", "/ai-models/available/:modelType"),
    ("POST", "/ai-models/providers"),
    ("POST", "/ai-models/prepare-model"),
    ("PUT", "/ai-models/providers/:modelType/:modelKey"),
    ("DELETE", "/ai-models/providers/:modelType/:modelKey"),
    ("PUT", "/ai-models/default/:modelType/:modelKey"),
    ("GET", "/web-search"),
    ("PUT", "/web-search/settings"),
    ("POST", "/web-search/providers"),
    ("PUT", "/web-search/providers/:providerKey"),
    ("DELETE", "/web-search/providers/:providerKey"),
    ("PUT", "/web-search/default/:providerKey"),
    ("GET", "/frontendPublicUrl"),
    ("GET", "/public/desktopFrontendUrl"),
    ("POST", "/frontendPublicUrl"),
    ("GET", "/connectorPublicUrl"),
    ("POST", "/connectorPublicUrl"),
    ("PUT", "/metricsCollection/toggle"),
    ("GET", "/metricsCollection"),
    ("PATCH", "/metricsCollection/pushInterval"),
    ("PATCH", "/metricsCollection/serverUrl"),
]

_PATH_PARAMETER_VALUES = {
    ":configId": MISSING_SLACK_BOT_CONFIG_ID,
    ":providerId": "openAI",
    ":modelType": "llm",
    ":modelKey": MISSING_AI_MODEL_KEY,
    ":providerKey": MISSING_WEB_SEARCH_PROVIDER_KEY,
}


def concrete_path(sub_path: str) -> str:
    """A PRE_ROUTER_OPERATIONS path with its Express parameters filled in."""
    for name, value in _PATH_PARAMETER_VALUES.items():
        sub_path = sub_path.replace(name, value)
    return sub_path


# A scope that is not config:read or config:write, for the requireScopes refusals.
NARROW_SCOPE = "org:read"

SeedSlackBot = Callable[..., dict[str, Any]]
# seed(**overrides) -> details of a stored duckduckgo web search provider (providerKey...).
SeedWebSearchProvider = Callable[..., dict[str, Any]]
# seed(**overrides) -> details of a stored llm entry (modelKey...), made with the run's Azure model.
SeedLlmProvider = Callable[..., dict[str, Any]]

_AZURE_LLM_ENV = (
    "TEST_AZURE_OPENAI_API_KEY",
    "TEST_AZURE_OPENAI_ENDPOINT",
    "TEST_AZURE_OPENAI_DEPLOYMENT_NAME",
    "TEST_AZURE_OPENAI_MODEL",
)
MetricsCollectionConfig = dict[str, Any]
# guard(sub_path, kv_path, derived=()) -> the config GET returned before the test touched it.
GuardSavedConfig = Callable[..., dict[str, Any]]
# guard(kv_path) -> the raw bytes stored there now (None when absent).
GuardStoredValue = Callable[[str], "bytes | None"]

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


def _kv_store(kv_path: str) -> tuple[redis.Redis, str, str]:
    """A client for the deployment's key-value store, the full key of ``kv_path`` and the cache channel."""
    if os.getenv("KV_STORE_TYPE", "redis").strip().lower() != "redis":
        pytest.fail(
            f"cannot reach {kv_path} directly: KV_STORE_TYPE is not redis, and no API removes a saved config"
        )
    namespace = os.getenv("REDIS_KEY_NAMESPACE", "").strip()
    scope = f"{namespace}:" if namespace else ""
    client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("REDIS_DB", "0")),
        socket_timeout=10,
    )
    key = f"{scope}{os.getenv('REDIS_KV_PREFIX', 'pipeshub:kv:')}{kv_path}"
    return client, key, f"{scope}{_KV_INVALIDATION_CHANNEL}"


def read_stored_value(kv_path: str) -> bytes | None:
    """The bytes the deployment holds at ``kv_path`` (None when the key is absent)."""
    client, key, _ = _kv_store(kv_path)
    try:
        return client.get(key)
    finally:
        client.close()


def write_stored_value(kv_path: str, raw: bytes) -> None:
    """Put back bytes read earlier with ``read_stored_value``; never used to invent a value."""
    client, key, channel = _kv_store(kv_path)
    try:
        client.set(key, raw)
        client.publish(channel, kv_path)
    finally:
        client.close()


def forget_stored_config(kv_path: str) -> None:
    """Remove one key from the deployment's key-value store, as KeyValueStoreService.delete does.

    The auth and connector config routes only ever create or replace; this is the
    one way to put back "nothing was saved" after a test saved something.
    """
    client, key, channel = _kv_store(kv_path)
    try:
        client.delete(key)
        # The Python services cache config values and drop them on this message.
        client.publish(channel, kv_path)
    finally:
        client.close()


def assert_validation_error(resp: requests.Response, *fields: str) -> None:
    """A 400 from the Node request validator that names exactly ``fields`` (``body.clientId``...)."""
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    named = sorted(detail["field"] for detail in error["metadata"]["errors"])
    assert named == sorted(fields), resp.text[:500]


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


def mint_narrow_scope_token(base_url: str, timeout: float = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to NARROW_SCOPE."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": NARROW_SCOPE,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {NARROW_SCOPE} token: {resp.status_code} {resp.text[:300]}"
    granted = resp.json().get("scope")
    assert granted == NARROW_SCOPE, f"asked for {NARROW_SCOPE!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()


def retry_while_health_check_times_out(send: Callable[[], requests.Response], attempts: int = 2) -> requests.Response:
    """Repeat a call whose web search health check answered 408; fail the test if it keeps doing so.

    The duckduckgo health check makes a real search: it passes when html.duckduckgo.com answers
    or refuses quickly, and times out (408) when the connection hangs. Every answer, the 408s
    included, still goes through the strict gate.
    """
    resp = send()
    for _ in range(attempts - 1):
        if resp.status_code != 408:
            break
        resp = send()
    if resp.status_code == 408:
        pytest.fail(
            "environment: the duckduckgo web search health check timed out "
            f"{attempts} times (408); html.duckduckgo.com does not answer from this machine: {resp.text[:200]}"
        )
    return resp


def azure_llm_configuration() -> dict[str, str]:
    """The run's own Azure OpenAI chat model, the one the stack is configured with."""
    missing = [name for name in _AZURE_LLM_ENV if not os.getenv(name)]
    if missing:
        pytest.fail(f"environment: an llm entry passes its health check only with real credentials; set {missing}")
    return {
        "endpoint": os.environ["TEST_AZURE_OPENAI_ENDPOINT"],
        "apiKey": os.environ["TEST_AZURE_OPENAI_API_KEY"],
        "deploymentName": os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"],
        "model": os.environ["TEST_AZURE_OPENAI_MODEL"],
    }


def llm_provider_body(**overrides: Any) -> dict[str, Any]:
    """A POST /ai-models/providers body for the run's Azure model, never the default."""
    body: dict[str, Any] = {
        "modelType": "llm",
        "provider": "azureOpenAI",
        "configuration": azure_llm_configuration(),
        "isMultimodal": False,
        "isReasoning": False,
        "isDefault": False,
    }
    body.update(overrides)
    return body
