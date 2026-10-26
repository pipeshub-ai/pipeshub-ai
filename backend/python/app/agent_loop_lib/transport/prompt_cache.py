"""Provider prompt-caching policy shared by the LangChain and direct-SDK transports.

What to ask a provider to cache, and where it is safe to ask. Kept free of
LangChain and SDK types, so both transport families apply the same rules:

- Anthropic caches the prefix ``tools -> system -> messages`` up to explicit
  ``cache_control`` breakpoints (at most four per request) and, through a
  top-level ``cache_control``, up to the request's last block. Only the hosts in
  ``ANTHROPIC_TOP_LEVEL_HOSTS`` are known to accept the top-level form; elsewhere
  only block markers are sent.
- OpenAI and Azure OpenAI cache automatically above 1,024 tokens, but where the
  cache is written depends on the model. GPT-5.6 and later write only at
  breakpoints: one at the end of the latest message, plus any explicit
  ``prompt_cache_breakpoint``. Without one after the stable system block, a new
  conversation never reuses the tools and instructions every conversation
  shares. Earlier models write at fixed intervals and reject the field.
- ``prompt_cache_key`` names the cache a request may use. Models before GPT-5.6,
  and every model on Azure, also route by it, so one org's traffic is spread
  over a few keys; GPT-5.6 and later on OpenAI route by prefix, where more keys
  would only split the cache.
- ``prompt_cache_retention="24h"`` goes only to the models whose docs list it.
  Each parameter goes only to the hosts named here: an argument a host does
  not know is a 400.
- OpenRouter forwards a ``prompt_cache_breakpoint`` to GPT-5.6+, turns it into
  a ``cache_control`` for Claude and drops it for upstreams without one, and
  routes by ``prompt_cache_key``. Claude also needs one on the last message,
  or no turn reads the history before it.
- xAI caches every request automatically and routes by ``prompt_cache_key``
  (its ``x-grok-conv-id``); that key is all it gets.
- A parameter a host rejects anyway is remembered for the process
  (``RejectedCacheParams``), and later requests go without it.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.utils.env_utils import env_bool, env_int

if TYPE_CHECKING:
    from collections.abc import Iterable

ANTHROPIC_TTLS = frozenset({"5m", "1h"})
ANTHROPIC_TOP_LEVEL_HOSTS = ("api.anthropic.com", ".services.ai.azure.com")
OPENAI_HOSTS = ("api.openai.com",)
AZURE_OPENAI_HOSTS = (".openai.azure.com", ".cognitiveservices.azure.com")
# Azure AI Foundry hosts OpenAI models next to Llama, Mistral, DeepSeek, ...;
# only OpenAI's own model names get OpenAI's cache parameters there.
FOUNDRY_HOSTS = (".services.ai.azure.com", ".models.ai.azure.com")
OPENROUTER_HOSTS = ("openrouter.ai",)
KEY_ROUTED_HOSTS = ("api.x.ai",)
_OPENAI_MODEL = re.compile(r"^(gpt-|o\d)", re.IGNORECASE)
_GPT_VERSION = re.compile(r"^gpt-(\d+)(?:\.(\d+))?(?:-|$)", re.IGNORECASE)
_FIRST_BREAKPOINT_VERSION = (5, 6)
# The models each provider's docs list for prompt_cache_retention="24h". A
# dated snapshot of a listed model matches ("gpt-4.1-2025-04-14"); a sibling
# does not ("gpt-4.1-mini").
OPENAI_RETENTION_MODELS = frozenset({
    "gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.2", "gpt-5.1-codex-max", "gpt-5.1", "gpt-5.1-codex",
    "gpt-5.1-codex-mini", "gpt-5.1-chat-latest", "gpt-5", "gpt-5-codex", "gpt-4.1",
})
AZURE_RETENTION_MODELS = frozenset({
    "gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.2", "gpt-5.1-codex-max", "gpt-5.1", "gpt-5.1-codex",
    "gpt-5.1-codex-mini", "gpt-5.1-chat", "gpt-5", "gpt-5-codex", "gpt-4.1",
})
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")
OPENAI_KEY_PARAM = "prompt_cache_key"
OPENAI_RETENTION_PARAM = "prompt_cache_retention"
OPENAI_BREAKPOINT_PARAM = "prompt_cache_breakpoint"
OPENAI_BREAKPOINT = {"mode": "explicit"}
ALL_CACHE_PARAMS = frozenset({"*"})


@dataclass(frozen=True)
class PromptCacheSettings:
    enabled: bool = True
    anthropic_ttl: str = "5m"
    openai_retention: str | None = "24h"
    routing_shards: int = 8

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


@dataclass(frozen=True)
class CacheScope:
    """Whose requests may share a provider cache: one org, split by
    ``shard_key`` (a conversation, or a record) where a provider routes by key.
    ``family`` names a fixed prompt (an indexing prompt), so each one warms
    keys of its own."""

    org_id: str
    shard_key: str | None = None
    family: str = ""

    def key(self, shards: int) -> str:
        return routing_key(self.org_id, self.shard_key, shards, family=self.family)


@dataclass(frozen=True)
class OpenAICachePlan:
    """What one request in OpenAI's format asks to cache: the request kwargs,
    whether the stable system block ends in an explicit breakpoint, and whether
    the last message does too."""

    kwargs: dict[str, str] = field(default_factory=dict)
    breakpoint: bool = False
    last_message: bool = False

    @property
    def sent_params(self) -> frozenset[str]:
        marked = self.breakpoint or self.last_message
        return frozenset(self.kwargs) | ({OPENAI_BREAKPOINT_PARAM} if marked else frozenset())

    @property
    def empty(self) -> bool:
        return not self.sent_params

    def without(self, refused: frozenset[str]) -> OpenAICachePlan:
        """This plan less what a provider refused (``"*"``: everything)."""
        if "*" in refused:
            return OpenAICachePlan()
        marked = OPENAI_BREAKPOINT_PARAM not in refused
        kwargs = {name: value for name, value in self.kwargs.items() if name not in refused}
        return OpenAICachePlan(kwargs, self.breakpoint and marked, self.last_message and marked)


def split_system_prompt(system: str | None, system_blocks: list[str] | None) -> tuple[str, str]:
    """``(stable, volatile)``: the agent's blocks when given (the first is the
    stable one), else the whole ``system`` as one stable block. The LangChain
    and direct OpenAI transports both split here, so they mark the same text."""
    if system_blocks:
        return system_blocks[0] or "", "\n\n".join(block for block in system_blocks[1:] if block)
    return system or "", ""


def anthropic_breakpoint(ttl: str = "5m") -> dict[str, str]:
    """The ``cache_control`` value for every Anthropic marker of a request; one
    TTL throughout, so markers can never be out of TTL order."""
    if ttl == "5m":
        return {"type": "ephemeral"}
    return {"type": "ephemeral", "ttl": ttl}


def routing_key(org_id: str, conversation_id: str | None, shards: int, *, family: str = "") -> str:
    """``prompt_cache_key`` for one conversation: the org (hashed, so no id
    leaves the process) and a shard of the conversation. Stable across
    processes, unlike ``hash()``."""
    org = hashlib.sha256((org_id or "").encode()).hexdigest()[:12]
    shard = zlib.crc32((conversation_id or "").encode()) % max(1, shards)
    return f"pipeshub:{org}:{family}:{shard}" if family else f"pipeshub:{org}:{shard}"


def anthropic_top_level_allowed(base_url: str | None) -> bool:
    """Whether the top-level ``cache_control`` may be sent: Anthropic's API (no
    base URL means its default) or Azure AI Foundry."""
    host = _host(base_url)
    return host is None or _matches(host, ANTHROPIC_TOP_LEVEL_HOSTS)


def explicit_breakpoints(model: str | None) -> bool:
    """Whether ``model`` takes ``prompt_cache_breakpoint`` (GPT-5.6 and later).
    Earlier models answer a 400, so a name that is not recognised gets none."""
    match = _GPT_VERSION.match(model or "")
    return bool(match) and (int(match[1]), int(match[2] or 0)) >= _FIRST_BREAKPOINT_VERSION


def openai_cache_plan(
    *, model: str | None, base_url: str | None, settings: PromptCacheSettings, scope: CacheScope,
    refused: frozenset[str] = frozenset(),
) -> OpenAICachePlan | None:
    """The cache parameters of a request in OpenAI's format (OpenAI, Azure
    OpenAI, OpenRouter, xAI), less any in ``refused``; None for any other host
    or model, or when nothing is left."""
    host = _host(base_url)
    if not settings.enabled:
        return None
    if host is not None and _matches(host, OPENROUTER_HOSTS):
        return _openrouter_plan(model, settings, scope, refused)
    if host is not None and _matches(host, KEY_ROUTED_HOSTS):
        plan = OpenAICachePlan({OPENAI_KEY_PARAM: scope.key(settings.routing_shards)}).without(refused)
        return None if plan.empty else plan
    if not _openai_cache_target(model, host):
        return None
    breakpoints = explicit_breakpoints(model)
    on_openai = host is None or _matches(host, OPENAI_HOSTS)
    kwargs = {OPENAI_KEY_PARAM: scope.key(1 if breakpoints and on_openai else settings.routing_shards)}
    retention_models = OPENAI_RETENTION_MODELS if on_openai else AZURE_RETENTION_MODELS
    if settings.openai_retention and _listed(model, retention_models):
        kwargs[OPENAI_RETENTION_PARAM] = settings.openai_retention
    plan = OpenAICachePlan(kwargs, breakpoints).without(refused)
    return None if plan.empty else plan


def _openrouter_plan(
    model: str | None, settings: PromptCacheSettings, scope: CacheScope, refused: frozenset[str],
) -> OpenAICachePlan | None:
    vendor, _, name = (model or "").lower().partition("/")
    claude = vendor == "anthropic"
    marked = claude or (vendor == "openai" and explicit_breakpoints(name))
    plan = OpenAICachePlan({OPENAI_KEY_PARAM: scope.key(settings.routing_shards)}, marked, claude).without(refused)
    return None if plan.empty else plan


def _openai_cache_target(model: str | None, host: str | None) -> bool:
    if host is None or _matches(host, OPENAI_HOSTS + AZURE_OPENAI_HOSTS):
        return True
    return _matches(host, FOUNDRY_HOSTS) and bool(model and _OPENAI_MODEL.match(model))


def _listed(model: str | None, models: frozenset[str]) -> bool:
    name = (model or "").lower()
    return name in models or _SNAPSHOT.sub("", name) in models


class RejectedCacheParams:
    """The cache parameters each ``(model, host)`` had refused, for the life of
    the process: the transport asks once, falls back, and stops asking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._refused: dict[tuple[str, str], frozenset[str]] = {}

    def record(self, model: str | None, base_url: str | None, params: Iterable[str] | None = None) -> None:
        """``params`` names what was refused; None refuses every cache parameter."""
        names = ALL_CACHE_PARAMS if params is None else frozenset(name.lower() for name in params)
        if not names:
            return
        identity = _identity(model, base_url)
        with self._lock:
            self._refused[identity] = self._refused.get(identity, frozenset()) | names

    def refused(self, model: str | None, base_url: str | None) -> frozenset[str]:
        with self._lock:
            return self._refused.get(_identity(model, base_url), frozenset())

    def rejected(self, model: str | None, base_url: str | None) -> bool:
        """Whether every cache parameter is refused for this model and host."""
        return "*" in self.refused(model, base_url)

    def clear(self) -> None:
        with self._lock:
            self._refused.clear()


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
