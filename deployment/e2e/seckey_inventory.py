"""R31-SECKEY: every kind of credential the stack can hold, and the canary seckey.py plants for each.

A canary is a value nobody but this lane knows, stored where the product keeps that kind of credential. The lane then
looks for every one of them inside every engine's sandbox (seckey.py), and on the fake upstreams for the ones a job
uses, so a sandbox that holds none of them cannot pass because none was used.

    live    the job uses it: its upstream (the scripted model, the fake SaaS, its SearXNG) must receive it
    stored  kept where the product keeps it, used by nothing the job does: it must stay out of the sandbox all the same
    none    the kind holds no credential (an MCP server without sign-in, a keyless search, a toolset with no auth)
    unused  a kind the code names that nothing takes yet (a toolset sign-in no registered toolset offers): the cohesion
            test fails the day something takes it, until it has a canary here

The tables below are explicit on purpose: tests/cohesion/test_r31seckey_canary_inventory.py holds them to the code's
own enumerations (LLMProvider, EmbeddingProvider, MCPAuthMode, the web search PROVIDERS, the toolset AuthType) and
to the credential fields the providers read, so a new kind or field fails there until it has a canary here.

Standard library only: seckey.py runs on the host, seckey_config.py in the app container.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass

CANARY_PREFIX = "pipeshub-canary-"
LIVE, STORED, NONE, UNUSED = "live", "stored", "none", "unused"
# The tag on everything the lane stores, so a rerun replaces its own entries and nothing else.
TAG = "seckey"

# Model providers (app/utils/aimodels.py LLMProvider, EmbeddingProvider). Every provider takes an apiKey field (some
# only optionally); these read more credential fields than that.
EXTRA_MODEL_FIELDS: dict[str, tuple[str, ...]] = {
    "bedrock": ("awsAccessKeyId", "awsAccessSecretKey"),
    "vertexAI": ("serviceAccountJson",),
}
LLM_KINDS: tuple[str, ...] = (
    "anthropic", "bedrock", "azureAI", "azureOpenAI", "cohere", "fireworks", "gemini", "groq", "litellmProxy",
    "lmStudio", "minimax", "mistral", "ollama", "openAI", "openAICompatible", "openRouter", "together", "vertexAI", "xai",
)
EMBEDDING_KINDS: tuple[str, ...] = (
    "anthropic", "bedrock", "azureAI", "azureOpenAI", "cohere", "default", "fireworks", "gemini", "huggingFace",
    "jinaAI", "litellmProxy", "lmStudio", "mistral", "ollama", "openAI", "openAICompatible", "openRouter",
    "sentenceTransformers", "together", "vertexAI", "voyage",
)
# The canary's shape for a provider whose keys have a recognisable one (harness_core token vectors), so the sandbox scan
# finds a copy by its shape too, as a scan of a real deployment would.
SHAPES: dict[str, str] = {"anthropic": "anthropic", "openAI": "openai", "gemini": "google", "vertexAI": "google",
                          "awsAccessKeyId": "aws"}

# MCP sign-in modes (app/agents/mcp/models.py MCPAuthMode): each live one is its own server on the fake SaaS.
MCP_MODES: dict[str, tuple[str, tuple[str, ...]]] = {
    "none": (NONE, ()),
    "api_token": (LIVE, ("apiToken",)),
    "headers": (LIVE, ("headerValue",)),
    "oauth": (LIVE, ("accessToken", "refreshToken", "clientSecret")),
}

# Web search providers (app/web/search PROVIDERS). SearXNG is the live one: an admin points it at a server of their
# own, here the fake SaaS. A keyed provider is stored with its key and never asked (SearXNG answers first).
SEARCH_PROVIDERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "duckduckgo": (NONE, ()),
    "ddgs": (NONE, ()),
    "searxng": (LIVE, ("apiKey",)),
    "serper": (STORED, ("apiKey",)),
    "tavily": (STORED, ("apiKey",)),
    "exa": (STORED, ("apiKey",)),
    "brave": (STORED, ("apiKey",)),
    "firecrawl": (STORED, ("apiKey",)),
    "keenable": (STORED, ("apiKey",)),
}

# Toolset sign-ins: the AuthType enum (app/agents/constants/toolset_constants.py) and every kind a registered toolset
# offers (its supported_auth_types). API_TOKEN is the live Jira Data Center toolset (its token is the stack's
# E2E_JIRA_TOKEN, which the fake Jira checks); the others are stored on a toolset of a type that takes them, as the
# sign-in would leave them.
TOOLSET_AUTH: dict[str, tuple[str, tuple[str, ...]]] = {
    "API_TOKEN": (LIVE, ("apiToken",)),
    "OAUTH": (STORED, ("access_token", "refresh_token", "clientSecret")),
    "BASIC_AUTH": (STORED, ("password",)),
    "BEARER_TOKEN": (UNUSED, ()),
    "USERNAME_PASSWORD": (UNUSED, ()),
    "NONE": (NONE, ()),
}

# Values the stack itself made that are credentials too: the org's model key (the scripted model accepts only it), the
# Jira token (the fake Jira accepts only it), the GitHub toolset sign-in seed_config.py writes, and every secret the
# stack's env file and the app container's environment hold (seckey.py reads them in-process, never prints them).
STACK_LIVE: dict[str, str] = {
    "llm.openAICompatible.live": "E2E_LLM_KEY",
    "toolset.API_TOKEN.apiToken": "E2E_JIRA_TOKEN",
}
GITHUB_TOKEN = "pxh-fake-github-token"
SECRET_NAME_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "CONTROL", "PRIVATE")


# R31SECKEY-FU-2: the platform's secrets that live in the config store, not in an environment: the worker credential
# (current and previous), Node's attempt- and gateway-token signing keys, and the secretKeys document. Their live
# values are canaries too; seckey_config.py reads them in the app container and hands them to seckey.py on a pipe.
CONFIG_SECRETS: dict[str, str] = {
    "workerCredential": "/services/agentHarness/workerCredential",
    "attemptSigningKeys": "/services/agentHarness/attemptSigningKeys",
    "gatewaySigningKeys": "/services/agentHarness/gatewaySigningKeys",
    "secretKeys": "/services/secretKeys",
    # SEC-C-10 (ADR-CRED-01): each machine role's own credential set, shaped as the worker's.
    "roleCredentialScrape": "/services/agentHarness/roleCredentials/scrape",
    "roleCredentialEgressProxy": "/services/agentHarness/roleCredentials/egressProxy",
    "roleCredentialSandboxBroker": "/services/agentHarness/roleCredentials/sandboxBroker",
}
_PEM_FENCE = "-----"


def _pem_body(pem: str) -> str:
    """A PEM key's base64 body on one line: the scan's patterns are lines, and a header line is no secret."""
    return "".join(line.strip() for line in pem.splitlines() if line.strip() and not line.startswith(_PEM_FENCE))


def config_secret_values(name: str, document: object) -> dict[str, str]:
    """The secret values of one CONFIG_SECRETS document, by a name that never holds the value."""
    if not isinstance(document, dict):
        return {}
    out: dict[str, str] = {}
    if name == "workerCredential" or name.startswith("roleCredential"):
        for field in ("current", "previous"):
            value = document.get(field)
            if isinstance(value, str) and value:
                out[f"config.{name}.{field}"] = value
    elif name.endswith("SigningKeys"):
        for key in document.get("keys") or []:
            private = key.get("privateKey") if isinstance(key, dict) else None
            if isinstance(private, str) and private:
                out[f"config.{name}.{key.get('kid')}.privateKey"] = _pem_body(private)
    else:
        for field, value in document.items():
            if isinstance(value, str) and len(value) >= 16:
                out[f"config.{name}.{field}"] = value
    return {k: v for k, v in out.items() if v and "\n" not in v}


@dataclass(frozen=True)
class Canary:
    name: str  # family.kind.field: what a finding names, never the value
    value: str
    mode: str


def canary(shape: str | None = None) -> str:
    """A unique value: ``pipeshub-canary-<uuid>``, or one with a vendor's shape that still names this lane."""
    tail = uuid.uuid4().hex
    if shape == "anthropic":
        return f"sk-ant-api03-pxhcanary{tail}"
    if shape == "openai":
        return f"sk-proj-pxhcanary{tail}"
    if shape == "google":
        return "AIza" + (f"PxhCanary{tail}")[:35]
    if shape == "aws":
        return "AKIA" + secrets.token_hex(8).upper()
    if shape == "github":
        return f"ghp_PxhCanary{tail}"
    return f"{CANARY_PREFIX}{tail}"


def model_fields(kind: str) -> tuple[str, ...]:
    return ("apiKey", *EXTRA_MODEL_FIELDS.get(kind, ()))


def plan() -> list[Canary]:
    """A fresh canary for every credential field of every kind that holds one (the stack-made ones are read apart)."""
    out: list[Canary] = []
    for family, kinds in (("llm", LLM_KINDS), ("embedding", EMBEDDING_KINDS)):
        for kind in kinds:
            for name in model_fields(kind):
                out.append(Canary(f"{family}.{kind}.{name}", canary(SHAPES.get(name) or SHAPES.get(kind)), STORED))
    for family, table in (("mcp", MCP_MODES), ("search", SEARCH_PROVIDERS), ("toolset", TOOLSET_AUTH)):
        for kind, (mode, fields) in table.items():
            for name in fields:
                if f"{family}.{kind}.{name}" in STACK_LIVE:
                    continue
                shape = "github" if family == "toolset" and kind == "OAUTH" and name == "access_token" else None
                out.append(Canary(f"{family}.{kind}.{name}", canary(shape), mode))
    return out


def model_entries(canaries: dict[str, str], fake_llm: str) -> dict[str, list[dict[str, object]]]:
    """The AI models document's entries: one per provider kind and family, each pointed at the scripted model, every
    credential field its canary. The embedding the scripted model serves goes first and is the default."""
    out: dict[str, list[dict[str, object]]] = {"llm": [], "embedding": []}
    for family, kinds in (("llm", LLM_KINDS), ("embedding", EMBEDDING_KINDS)):
        for kind in kinds:
            configuration: dict[str, object] = {"model": f"{TAG}-{kind}", "endpoint": fake_llm}
            for name in model_fields(kind):
                value = canaries[f"{family}.{kind}.{name}"]
                configuration[name] = (json.dumps({"type": "service_account", "private_key": value})
                                       if name == "serviceAccountJson" else value)
            out[family].append({"provider": kind, "configuration": configuration,
                                "modelKey": f"{TAG}-{family}-{kind}", "isMultimodal": False, "isDefault": False,
                                "isReasoning": False, "contextLength": None})
    live = next(e for e in out["embedding"] if e["provider"] == "openAICompatible")
    live["isDefault"] = True
    live["configuration"]["model"] = "e2e-embedding"  # type: ignore[index]
    out["embedding"] = [live, *(e for e in out["embedding"] if e is not live)]
    return out


def search_entries(canaries: dict[str, str], fake_saas: str) -> list[dict[str, object]]:
    """The web search document's providers: SearXNG on the fake SaaS (the default), every keyed one with its canary."""
    out: list[dict[str, object]] = []
    for kind, (mode, fields) in SEARCH_PROVIDERS.items():
        if not fields:
            continue
        configuration: dict[str, object] = {name: canaries[f"search.{kind}.{name}"] for name in fields}
        if kind in ("searxng", "firecrawl"):
            configuration["baseUrl"] = f"{fake_saas}/searxng" if kind == "searxng" else fake_saas
        out.append({"provider": kind, "providerKey": f"{TAG}-{kind}", "providerLabel": f"{TAG} {kind}",
                    "configuration": configuration, "isDefault": mode == LIVE})
    return out


def secret_named(name: str) -> bool:
    upper = name.upper()
    return any(word in upper for word in SECRET_NAME_WORDS)


__all__ = [
    "CANARY_PREFIX",
    "CONFIG_SECRETS",
    "EMBEDDING_KINDS",
    "EXTRA_MODEL_FIELDS",
    "GITHUB_TOKEN",
    "LIVE",
    "LLM_KINDS",
    "MCP_MODES",
    "NONE",
    "SEARCH_PROVIDERS",
    "STACK_LIVE",
    "STORED",
    "TAG",
    "TOOLSET_AUTH",
    "UNUSED",
    "Canary",
    "canary",
    "config_secret_values",
    "model_entries",
    "model_fields",
    "plan",
    "search_entries",
    "secret_named",
]
