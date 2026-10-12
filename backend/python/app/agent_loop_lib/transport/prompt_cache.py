"""Provider prompt-caching policy shared by the LangChain and direct-SDK transports.

What to ask a provider to cache, and where it is safe to ask. Kept free of
LangChain and SDK types, so both transport families apply the same rules:

- Anthropic caches the prefix ``tools -> system -> messages`` up to explicit
  ``cache_control`` breakpoints (at most four per request) and, through a
  top-level ``cache_control``, up to the request's last block. Only the hosts in
  ``ANTHROPIC_TOP_LEVEL_HOSTS`` are known to accept the top-level form; elsewhere
  only block markers are sent.
- OpenAI and Azure OpenAI cache automatically above 1,024 tokens.
  ``prompt_cache_key`` routes requests that share a prefix to the same cache, and
  ``prompt_cache_retention="24h"`` keeps it beyond the in-memory default. Both are
  sent only to OpenAI's and Azure's own hosts, and retention only to model
  families that document it: an argument a host does not know is a 400.
- A host that rejects one of these anyway is remembered for the process
  (``RejectedCacheParams``), so the next request is sent without it.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import zlib
from dataclasses import dataclass
from urllib.parse import urlparse

from app.utils.env_utils import env_bool, env_int

ANTHROPIC_TTLS = frozenset({"5m", "1h"})
ANTHROPIC_TOP_LEVEL_HOSTS = ("api.anthropic.com", ".services.ai.azure.com")
OPENAI_CACHE_HOSTS = ("api.openai.com", ".openai.azure.com", ".cognitiveservices.azure.com")
# Azure AI Foundry hosts OpenAI models next to Llama, Mistral, DeepSeek, ...;
# only OpenAI's own model names get OpenAI's cache parameters there.
FOUNDRY_HOSTS = (".services.ai.azure.com", ".models.ai.azure.com")
_OPENAI_MODEL = re.compile(r"^(gpt-|o\d)", re.IGNORECASE)
# Families whose docs list prompt_cache_retention="24h" (OpenAI and Azure). A
# family matches its own name and dash variants ("gpt-5" covers "gpt-5-codex",
# never "gpt-5.6", where retention is deprecated).
DEFAULT_RETENTION_FAMILIES = ("gpt-5.5", "gpt-5.4", "gpt-5.3", "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-4.1")
# Parameter names as they appear in a provider's error message.
CACHE_PARAM_NAMES = ("cache_control", "prompt_cache_key", "prompt_cache_retention", "cachepoint")


@dataclass(frozen=True)
class PromptCacheSettings:
    enabled: bool = True
    anthropic_ttl: str = "5m"
    openai_retention: str | None = "24h"
    routing_shards: int = 8
    retention_families: tuple[str, ...] = DEFAULT_RETENTION_FAMILIES

    @classmethod
    def from_env(cls) -> PromptCacheSettings:
        ttl = (_env_str("PIPESHUB_PROMPT_CACHE_TTL") or "5m").lower()
        retention = _env_str("PIPESHUB_PROMPT_CACHE_RETENTION", "24h")
        return cls(
            enabled=env_bool("PIPESHUB_PROMPT_CACHE", True),
            anthropic_ttl=ttl if ttl in ANTHROPIC_TTLS else "5m",
            openai_retention=None if retention.lower() in {"", "off", "none"} else retention,
            routing_shards=env_int("PIPESHUB_PROMPT_CACHE_SHARDS", 8, lo=1, hi=1024) or 8,
        )

    @property
    def breakpoint(self) -> dict[str, str]:
        return anthropic_breakpoint(self.anthropic_ttl)


def anthropic_breakpoint(ttl: str = "5m") -> dict[str, str]:
    """The ``cache_control`` value for every Anthropic marker of a request; one
    TTL throughout, so markers can never be out of TTL order."""
    if ttl == "5m":
        return {"type": "ephemeral"}
    return {"type": "ephemeral", "ttl": ttl}


def routing_key(org_id: str, conversation_id: str | None, shards: int) -> str:
    """``prompt_cache_key`` for one conversation: the org (hashed, so no id
    leaves the process) and a shard of the conversation, so one org's traffic
    spreads over a few cache keys instead of saturating one. Stable across
    processes, unlike ``hash()``."""
    org = hashlib.sha256((org_id or "").encode()).hexdigest()[:12]
    shard = zlib.crc32((conversation_id or "").encode()) % max(1, shards)
    return f"pipeshub:{org}:{shard}"


def anthropic_top_level_allowed(base_url: str | None) -> bool:
    """Whether the top-level ``cache_control`` may be sent: Anthropic's API (no
    base URL means its default) or Azure AI Foundry."""
    host = _host(base_url)
    return host is None or _matches(host, ANTHROPIC_TOP_LEVEL_HOSTS)


def openai_cache_kwargs(
    *, model: str | None, base_url: str | None, settings: PromptCacheSettings, key: str,
) -> dict[str, str]:
    """``prompt_cache_key`` (and ``prompt_cache_retention`` where documented) for
    a request to OpenAI or Azure OpenAI; nothing for any other host or model."""
    if not settings.enabled or not _openai_cache_target(model, base_url):
        return {}
    kwargs = {"prompt_cache_key": key}
    if settings.openai_retention and _supports_retention(model, settings.retention_families):
        kwargs["prompt_cache_retention"] = settings.openai_retention
    return kwargs


def _openai_cache_target(model: str | None, base_url: str | None) -> bool:
    host = _host(base_url)
    if host is None or _matches(host, OPENAI_CACHE_HOSTS):
        return True
    return _matches(host, FOUNDRY_HOSTS) and bool(model and _OPENAI_MODEL.match(model))


def _supports_retention(model: str | None, families: tuple[str, ...]) -> bool:
    name = (model or "").lower()
    return any(name == family or name.startswith(f"{family}-") for family in families)


class RejectedCacheParams:
    """``(model, host)`` pairs whose provider rejected a cache parameter, for the
    life of the process: the transport asks once, falls back, and stops asking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rejected: set[tuple[str, str]] = set()

    def record(self, model: str | None, base_url: str | None) -> None:
        with self._lock:
            self._rejected.add(_identity(model, base_url))

    def rejected(self, model: str | None, base_url: str | None) -> bool:
        with self._lock:
            return _identity(model, base_url) in self._rejected

    def clear(self) -> None:
        with self._lock:
            self._rejected.clear()


REJECTED_CACHE_PARAMS = RejectedCacheParams()


def _identity(model: str | None, base_url: str | None) -> tuple[str, str]:
    return ((model or "").lower(), _host(base_url) or "")


def _host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    return (parsed.hostname or "").lower() or None


def _matches(host: str, patterns: tuple[str, ...]) -> bool:
    return any(host == p.lstrip(".") or (p.startswith(".") and host.endswith(p)) for p in patterns)


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()
