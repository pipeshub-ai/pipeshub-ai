"""Bring a fresh PipesHub stack to the state a benchmark run expects.

One idempotent step instead of a manual onboarding: the org and admin exist,
onboarding is marked done, the benchmark's platform flags are set, and the
configured embedding model is the default (registered if missing, verified if
present). Re-running it against a configured stack changes nothing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping

import requests

from ai_models_setup import seed_explicit_embedding
from benchmarks.harness.config import EmbeddingSelector
from benchmarks.harness.credentials import Credentials
from benchmarks.harness.errors import ConfigError
from benchmarks.harness.llm.providers import provider_spec
from benchmarks.harness.systems.pipeshub.seed import verify_indexing_embedding
from benchmarks.harness.systems.pipeshub.session import UserSession

logger = logging.getLogger(__name__)

# Held fixed for every benchmark run. User and org context would add the
# admin's name, email and org to every system prompt: tokens no other system
# pays for, and nothing a corpus question can use.
BENCHMARK_FEATURE_FLAGS: Mapping[str, bool] = {"ENABLE_USER_CONTEXT": False}

_HEALTH_PATHS = ("/api/v1/health", "/api/v1/health/services")
_SETTINGS_PATH = "/api/v1/configurationManager/platform/settings"
_EMBEDDING_PATH = "/api/v1/configurationManager/ai-models/embedding"
_PROMPTS_PATH = "/api/v1/configurationManager/prompts/system"


def wait_until_healthy(
    base_url: str,
    *,
    timeout_s: float = 900,
    poll_s: float = 5,
    get: Callable[..., requests.Response] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """The app container starts its services one after another; the stack is
    usable only once the aggregate health reports every service healthy."""
    deadline = time.monotonic() + timeout_s
    for path in _HEALTH_PATHS:
        while True:
            try:
                response = get(base_url.rstrip("/") + path, timeout=10)
                if response.ok and (response.json() or {}).get("status") == "healthy":
                    logger.info("healthy: %s", path)
                    break
            except (requests.RequestException, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise ConfigError(f"PipesHub at {base_url} is not healthy after {timeout_s:.0f}s ({path})")
            sleep(poll_s)


def ensure_org(
    base_url: str, email: str, password: str, *, post: Callable[..., requests.Response] = requests.post,
) -> bool:
    """Create the org with the benchmark user as its admin. Returns whether it
    was created; an existing org is fine (a 4xx here means it already exists)."""
    response = post(
        base_url.rstrip("/") + "/api/v1/org",
        json={
            "accountType": "business", "adminFullName": "Benchmark Admin", "contactEmail": email,
            "password": password, "confirmPassword": password,
            "registeredName": "Benchmark Org", "shortName": "BENCH",
        },
        timeout=60,
    )
    if response.status_code >= 500:
        raise ConfigError(f"creating the org failed: HTTP {response.status_code}")
    created = response.status_code < 400
    logger.info("org %s", "created" if created else f"already exists (HTTP {response.status_code})")
    return created


def apply_feature_flags(session: UserSession, flags: Mapping[str, bool]) -> dict[str, bool]:
    """Merge `flags` into the org's platform settings, keeping every other flag."""
    response = session.request("GET", _SETTINGS_PATH)
    settings = (response.json() or {}) if response.status_code < 400 else {}
    merged = {**dict(settings.get("featureFlags") or {}), **flags}
    body: dict[str, object] = {"featureFlags": merged}
    if settings.get("fileUploadMaxSizeBytes"):
        body["fileUploadMaxSizeBytes"] = settings["fileUploadMaxSizeBytes"]
    response = session.request("POST", _SETTINGS_PATH, json=body)
    if response.status_code >= 400:
        raise ConfigError(f"setting platform flags failed: HTTP {response.status_code}")
    logger.info("platform flags: %s", {name: merged[name] for name in flags})
    return merged


def ensure_embedding(session: UserSession, selector: EmbeddingSelector, credentials: Credentials) -> None:
    """Register the configured embedding model as the default, or verify the
    one already there is it: indexing and every RAG baseline's queries must
    share one vector space."""
    response = session.request("GET", _EMBEDDING_PATH)
    if response.status_code < 400 and (response.json() or {}).get("models"):
        verify_indexing_embedding(session, selector)
        logger.info("embedding already configured: %s:%s", selector.pipeshub_provider or selector.provider, selector.model)
        return
    provider = selector.pipeshub_provider or selector.provider
    seeded = seed_explicit_embedding(
        session,
        provider=provider,
        model_name=selector.model,
        api_key=credentials.first_of(provider_spec(provider).key_envs),
        deployment_name=selector.deployment,
    )
    logger.info("registered embedding %s:%s as %s", seeded.provider, seeded.model_name, seeded.model_key)


def apply_custom_instructions(session: UserSession, text: str | None) -> None:
    """Search mode's custom instructions are exactly `text`; the other modes'
    are left as they are."""
    response = session.request("GET", _PROMPTS_PATH)
    if response.status_code >= 400:
        raise ConfigError(f"reading custom instructions failed: HTTP {response.status_code}")
    prompts = {**(response.json() or {}), "customSystemPrompt": text or ""}
    response = session.request("PUT", _PROMPTS_PATH, json=prompts)
    if response.status_code >= 400:
        raise ConfigError(f"setting custom instructions failed: HTTP {response.status_code}")
    logger.info("search-mode custom instructions: %s", repr(text) if text else "none")


def setup_pipeshub(
    base_url: str, selector: EmbeddingSelector, credentials: Credentials, *, custom_instructions: str | None = None,
) -> None:
    if not credentials.user_email or not credentials.user_password:
        raise ConfigError("PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD must be set")
    wait_until_healthy(base_url)
    ensure_org(base_url, credentials.user_email, credentials.user_password)
    session = UserSession(base_url, email=credentials.user_email, password=credentials.user_password)
    response = session.request("PUT", "/api/v1/org/onboarding-status", json={"status": "configured"})
    if response.status_code >= 400:
        raise ConfigError(f"marking onboarding done failed: HTTP {response.status_code}")
    apply_feature_flags(session, BENCHMARK_FEATURE_FLAGS)
    apply_custom_instructions(session, custom_instructions)
    ensure_embedding(session, selector, credentials)
