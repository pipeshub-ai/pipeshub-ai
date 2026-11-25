"""``ModelAccessResolver`` over the org's AI model configuration (E3) and model accounts (P2.4b).

Reads ``/services/aiModels`` through ``ConfigurationService`` as the query
service does (``get_model_config``), builds the configured model the way the
agent route does (``get_generator_model_async``) and reaches it through the
direct transport for its class (``build_direct_transport``), so a gateway call
and an in-product agent call to the same model send the same request.

Unlike ``get_llm_for_chat`` this never falls back: a key that is not
configured, a model name the entry no longer lists, or a provider changed since
the pin is ``route_not_configured``, never the first entry (principle 6).

The key is not taken from the entry (P2.4a): it is resolved through the
credential service by a handle bound to ``model:<modelKey>`` and the run the
call serves, and only the transport built from it holds it. The entry the
resolver digests and caches has no key; a changed key is a new credential
version and rebuilds the transport.

A route pinned to a model account (P2.4b: a user's own key, an org cloud
account) is resolved from that account's secret in the vault instead, by a
handle bound to ``model:<accountId>``. Every call resolves the handle, which
reads the vault, so a revoked account is refused at the next call
(``account_revoked``) and a store outage refuses (``account_unavailable``);
the transport built for a refused account is dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from app.harness.credentials.sources import MODEL_KIND, configured_llm_entries
from app.harness.credentials.vault import VAULT_KIND
from app.harness.placement import deployment_region, model_regions
from harness_core.access.errors import (
    AccountNotPermitted,
    AccountRevoked,
    AccountUnavailable,
    ModelAccessRefused,
    ProviderUnsupported,
    RouteNotConfigured,
)
from harness_core.access.ports import ResolvedRoute
from harness_core.access.types import (
    AccessRoute,
    AccountKind,
    AccountRef,
    ModelApi,
    RouteKind,
    account_owner,
)
from harness_core.clock import SystemClock
from harness_core.credentials.errors import (
    CredentialRefused,
    CredentialRevoked,
    CredentialStoreUnavailable,
)
from harness_core.credentials.leases import CredentialLeases
from harness_core.credentials.types import (
    CredentialPrincipal,
    CredentialPurpose,
    CredentialScope,
    Secret,
)
from harness_core.gateway.priority_gate import upstream_key
from harness_core.logs import HarnessLog
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.anthropic_cloud import (
    anthropic_on_bedrock,
    anthropic_on_vertex,
    bedrock_anthropic_model,
    vertex_anthropic_model,
)
from harness_core.models.transports.prompt_cache import CacheScope, PromptCacheSettings
from harness_core.security.secrets import SecretSet

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from app.config.configuration_service import ConfigurationService
    from harness_core.access.types import ModelNeed, Payer, PinnedRoute
    from harness_core.clock import Clock
    from harness_core.credentials.ports import CredentialService
    from harness_core.models.transports.base import LLMTransport

    TransportFactory = Callable[[str, dict[str, object], str, str], Awaitable["LLMTransport | None"]]

# How the gateway route reaches each configured provider: an API key the org
# configured, or an org cloud account the gateway route may not use yet (A1).
ACCOUNT_KINDS: Mapping[str, AccountKind] = {
    "openAI": AccountKind.ORG_API_KEY,
    "azureOpenAI": AccountKind.ORG_API_KEY,
    "anthropic": AccountKind.ORG_API_KEY,
    "gemini": AccountKind.ORG_API_KEY,
    "openAICompatible": AccountKind.ORG_API_KEY,
    "bedrock": AccountKind.ORG_CLOUD,
    "vertexAI": AccountKind.ORG_CLOUD,
}
# Which credential kind holds the key of each account kind the gateway may use.
ACCOUNT_SECRET_KINDS: Mapping[AccountKind, str] = {
    AccountKind.ORG_API_KEY: MODEL_KIND,
    AccountKind.USER_API_KEY: VAULT_KIND,
    AccountKind.ORG_CLOUD: VAULT_KIND,
}
# Who pays for a call on each account kind; USD is charged to the run's budget either way.
ACCOUNT_PAYERS: Mapping[AccountKind, Payer] = {
    AccountKind.ORG_API_KEY: "org",
    AccountKind.ORG_CLOUD: "org",
    AccountKind.USER_API_KEY: "user",
}
# Entry fields that are credentials: resolved by handle, never digested or cached.
SECRET_FIELDS = ("apiKey",)
# The purpose's run when a caller resolves outside a run.
NO_RUN = "model-access"
# Node's pin for "the org's default model" (run-spec.ts ``grantedModel``).
ORG_DEFAULT = ("pipeshub", "org-default")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class AccountProvider:
    """Which fields of a model account's material the configured-model entry takes.
    ``anthropic_model``: for a cloud that hosts many vendors' models and is reached through
    its Anthropic transport only, the Anthropic model a model id names (None: not served)."""

    fields: tuple[str, ...]
    anthropic_model: Callable[[str], str | None] | None = None


def _bedrock(configuration: Mapping[str, object], model_name: str, model_key: str) -> LLMTransport:
    return anthropic_on_bedrock(
        region=str(configuration["region"]),
        access_key_id=str(configuration["accessKeyId"]),
        secret_access_key=str(configuration["secretAccessKey"]),
        model=model_name,
        model_key=model_key,
    )


def _vertex(configuration: Mapping[str, object], model_name: str, model_key: str) -> LLMTransport:
    return anthropic_on_vertex(
        project_id=str(configuration["projectId"]),
        location=str(configuration["location"]),
        service_account_json=str(configuration["serviceAccountJson"]),
        model=model_name,
        model_key=model_key,
    )


# Providers a model account reaches through the gateway's transports (P2.4b's account fields).
ACCOUNT_PROVIDERS: Mapping[str, AccountProvider] = {
    "openAI": AccountProvider(("apiKey",)),
    "anthropic": AccountProvider(("apiKey",)),
    "gemini": AccountProvider(("apiKey",)),
    "azureOpenAI": AccountProvider(("apiKey", "endpoint", "deploymentName", "apiVersion")),
    "bedrock": AccountProvider(("region", "accessKeyId", "secretAccessKey"), bedrock_anthropic_model),
    "vertexAI": AccountProvider(("projectId", "location", "serviceAccountJson"), vertex_anthropic_model),
}
# Org cloud accounts whose transport is built here from the account's own material, not through a
# LangChain model (the query service has no configured-model class for them).
CLOUD_TRANSPORTS: Mapping[str, Callable[[Mapping[str, object], str, str], LLMTransport]] = {
    "bedrock": _bedrock,
    "vertexAI": _vertex,
}
# How a credential refusal of a model account reaches the gateway; any other is route_not_configured.
ACCOUNT_REFUSALS: tuple[tuple[type[CredentialRefused], type[ModelAccessRefused]], ...] = (
    (CredentialRevoked, AccountRevoked),
    (CredentialStoreUnavailable, AccountUnavailable),
)


def machine_name(value: str) -> str:
    """Node's ``machineName``: how a provider is written into a pinned route."""
    name = value.strip().lower()
    return name if _NAME.match(name) else "unknown"


# The configured models whose direct transport (OpenAI, Azure OpenAI) honours an engine's cache key and replays
# Responses reasoning. Anthropic's (direct, Bedrock, Vertex) replays its signed thinking (WP56-WIRE); Gemini's carries
# nothing yet and is built as before.
OPENAI_FAMILY_MODELS = frozenset({"AzureChatOpenAI", "ChatOpenAI", "BaseChatOpenAI"})


async def build_configured_transport(
    provider: str, entry: dict[str, object], model_name: str, model_key: str
) -> LLMTransport | None:
    from app.agents.agent_loop.direct_transport import build_direct_transport
    from app.utils.aimodels import get_generator_model_async

    cloud = CLOUD_TRANSPORTS.get(provider)
    if cloud is not None:
        configuration = entry.get("configuration")
        return _thinking_kept(
            cloud(cast("dict[str, object]", configuration) if isinstance(configuration, dict) else {}, model_name, model_key)
        )
    llm = await get_generator_model_async(provider, entry, model_name)  # pyright: ignore[reportArgumentType]
    if type(llm).__name__ not in OPENAI_FAMILY_MODELS:
        return _thinking_kept(build_direct_transport(llm, model_name=model_name, model_key=model_key))
    # WP-62: an engine's prompt cache key and its reasoning state reach OpenAI and Azure. The cache policy is the
    # query service's (hosts, retention, a rejected parameter remembered); the gateway puts each call's own key,
    # scoped to the run's org, in place of this default. Reasoning items are kept on the answer and replayed on
    # the Responses wire: the gateway stores none (the engine holds them), so HARNESS_PROVIDER_STATE, which guards
    # what the durable loop journals, does not apply here.
    settings = PromptCacheSettings.from_env()
    transport = build_direct_transport(
        llm, model_name=model_name, model_key=model_key,
        cache_settings=settings, cache_scope=CacheScope(model_key),
    )
    if transport is not None:
        _ = transport.keep_provider_state()
    return transport


def _thinking_kept(transport: LLMTransport | None) -> LLMTransport | None:
    """An Anthropic transport keeps the provider's signed thinking on the answer and sends it back, which the
    gateway hands to the engine and takes back sealed (WP56-WIRE); as for OpenAI, the gateway stores none, so
    HARNESS_PROVIDER_STATE (what the durable loop journals) does not apply."""
    if isinstance(transport, AnthropicTransport):
        _ = transport.keep_provider_state()
    return transport


def models_of(entry: Mapping[str, object]) -> list[str]:
    configuration = entry.get("configuration")
    raw = cast("dict[str, object]", configuration).get("model") if isinstance(configuration, dict) else None
    return [name.strip() for name in raw.split(",") if name.strip()] if isinstance(raw, str) else []


def _digest(entry: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()


def _configuration(entry: Mapping[str, object]) -> Mapping[str, object]:
    configuration = entry.get("configuration")
    return cast("dict[str, object]", configuration) if isinstance(configuration, dict) else {}


def without_secrets(entry: Mapping[str, object]) -> dict[str, object]:
    configuration = entry.get("configuration")
    if not isinstance(configuration, dict):
        return dict(entry)
    public = {k: v for k, v in cast("dict[str, object]", configuration).items() if k not in SECRET_FIELDS}
    return {**entry, "configuration": public}


def with_key(entry: Mapping[str, object], key: object) -> dict[str, object]:
    configuration = cast("dict[str, object]", entry.get("configuration") or {})
    return {**entry, "configuration": {**configuration, SECRET_FIELDS[0]: key}}


def account_refusal(refused: CredentialRefused) -> ModelAccessRefused:
    for credential, access in ACCOUNT_REFUSALS:
        if isinstance(refused, credential):
            return access(f"the pinned model account is refused ({refused.reason_code})")
    return RouteNotConfigured(f"the pinned model account has no usable secret ({refused.reason_code})")


class ConfiguredModelAccess:
    def __init__(
        self,
        config_service: ConfigurationService,
        *,
        transports: TransportFactory = build_configured_transport,
        credentials: CredentialService | None = None,
        clock: Clock | None = None,
        log: HarnessLog | None = None,
    ) -> None:
        from app.harness.credentials.service import credential_service_for

        self._config_service = config_service
        self._transports = transports
        self._log = log or HarnessLog.named("app.harness.access")
        self._leases = CredentialLeases(credentials or credential_service_for(config_service), clock or SystemClock())
        self._cache: dict[tuple[str, str], tuple[str, str, ResolvedRoute]] = {}

    async def _entries(self, *, fresh: bool) -> list[dict[str, object]]:
        return cast("list[dict[str, object]]", await configured_llm_entries(self._config_service, fresh=fresh))

    async def _entry(self, route: PinnedRoute) -> dict[str, object] | None:
        for fresh in (False, True):
            entries = await self._entries(fresh=fresh)
            if route.by_name:
                # GA-15: a role's route, served by the first configured model that lists it (``configured_model_key``).
                found = next(
                    (
                        e
                        for e in entries
                        if machine_name(str(e.get("provider") or "")) == route.provider and route.model in models_of(e)
                    ),
                    None,
                )
            elif (route.provider, route.model) == ORG_DEFAULT and route.config_key is None:
                found = next((e for e in entries if e.get("isDefault") is True), None)
            else:
                found = next((e for e in entries if e.get("modelKey") == route.config_key), None)
            if found is not None:
                return found
        return None

    async def _configured_for_account(self, route: PinnedRoute, model_name: str) -> None:
        """SEC-C-02 (ADR-MA-01): an account only changes who pays, so its model is still one the org configured, as
        Node's pin decided: the entry the pin keys, of the account's provider, lists the name exactly. A pin made
        before Node keyed account routes names no entry; any configured entry of that provider listing it serves."""
        for fresh in (False, True):
            entries = await self._entries(fresh=fresh)
            candidates = (
                [e for e in entries if e.get("modelKey") == route.config_key]
                if route.config_key is not None
                else entries
            )
            if any(
                machine_name(str(e.get("provider") or "")) == route.provider and model_name in models_of(e)
                for e in candidates
            ):
                return
        raise RouteNotConfigured("the model account's model is not one the organization configured for its provider")

    async def _secret(self, tenant: str, kind: AccountKind, name: str, subject: str, run_id: str | None) -> Secret:
        secret_kind = ACCOUNT_SECRET_KINDS.get(kind)
        if secret_kind is None:
            raise AccountNotPermitted("no credential source serves this account kind")
        return await self._leases.secret(
            CredentialPurpose(tenant, f"model:{name}", run_id or NO_RUN),
            CredentialPrincipal(tenant, subject),
            CredentialScope(secret_kind, name),
        )

    async def _key(self, tenant: str, kind: AccountKind, model_key: str, run_id: str | None) -> tuple[str, object]:
        try:
            secret = await self._secret(tenant, kind, model_key, "org", run_id)
        except CredentialRefused as exc:
            raise RouteNotConfigured(f"the configured model has no usable key ({exc.reason_code})") from None
        return secret.version, secret.material

    def _cached(self, key: tuple[str, str], digest: str, version: str) -> ResolvedRoute | None:
        cached = self._cache.get(key)
        return cached[2] if cached is not None and cached[:2] == (digest, version) else None

    async def resolve(
        self, tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: ModelNeed | None = None
    ) -> ResolvedRoute:
        if route.account is not None:
            return await self._resolve_account(tenant, route, route.account, run_id)
        if route.config_key is None and not route.by_name and (route.provider, route.model) != ORG_DEFAULT:
            raise RouteNotConfigured("the pinned route names no configured model")
        entry = await self._entry(route)
        if entry is None:
            raise RouteNotConfigured("the pinned model is not configured")
        provider = str(entry.get("provider") or "")
        key = str(entry.get("modelKey") or "")
        models = models_of(entry)
        model_name = route.config_model or (models[0] if models else "")
        if model_name not in models:
            raise RouteNotConfigured("the configured model no longer lists the pinned model name")
        if route.provider not in ("unknown", ORG_DEFAULT[0]) and machine_name(provider) != route.provider:
            raise RouteNotConfigured("the configured model's provider differs from the pinned route")
        kind = ACCOUNT_KINDS.get(provider)
        if kind is None:
            raise ProviderUnsupported("the gateway has no transport for this provider")
        if kind is not AccountKind.ORG_API_KEY:
            raise AccountNotPermitted("the gateway route reaches this provider only through an org API key")
        public = without_secrets(entry)
        digest = _digest(public)
        key_version, api_key = await self._key(tenant, kind, key, run_id)
        cached = self._cached((key, model_name), digest, key_version)
        if cached is not None:
            return cached
        keyed = with_key(public, api_key)
        transport = await self._transports(provider, keyed, model_name, key)
        if transport is None:
            raise ProviderUnsupported("the configured model has no direct transport")
        resolved = ResolvedRoute(
            access=AccessRoute(
                kind=RouteKind.GATEWAY,
                api=ModelApi.CHAT_COMPLETIONS,
                provider=machine_name(provider),
                model=model_name,
                account=AccountRef(kind, key),
                pays=ACCOUNT_PAYERS[kind],
                region=model_regions().region_of(
                    provider, _configuration(public), model_name, deployment=deployment_region()
                ),
            ),
            transport=transport,
            pricing_model=model_name,
            secrets=SecretSet.of(api_key),
            admission_key=upstream_key(provider, _configuration(keyed)),
        )
        self._cache[key, model_name] = (digest, key_version, resolved)
        self._log.info(
            "model_access.resolved",
            orgId=tenant,
            provider=machine_name(provider),
            modelKey=key,
            transport=transport.provider,
            orgDefault=route.config_key is None,
        )
        return resolved

    async def _resolve_account(
        self, tenant: str, route: PinnedRoute, account: AccountRef, run_id: str | None
    ) -> ResolvedRoute:
        if account.kind not in (AccountKind.USER_API_KEY, AccountKind.ORG_CLOUD):
            raise AccountNotPermitted("only model accounts are pinned by reference")
        owner = account_owner(account)
        model_name = route.config_model or route.model
        try:
            secret = await self._secret(tenant, account.kind, account.account_id, owner, run_id)
        except CredentialRefused as exc:
            for cached in [k for k in self._cache if k[0] == account.account_id]:
                del self._cache[cached]
            raise account_refusal(exc) from None
        material = cast("dict[str, object]", secret.material) if isinstance(secret.material, dict) else {}
        provider = str(material.get("provider") or "")
        if machine_name(provider) != route.provider:
            raise RouteNotConfigured("the pinned model account is for another provider")
        # After the account is read, so a store outage stays the retryable account_unavailable it is.
        await self._configured_for_account(route, model_name)
        spec = ACCOUNT_PROVIDERS.get(provider)
        if spec is None:
            raise ProviderUnsupported("the gateway has no transport for this account's provider")
        pricing_model = model_name
        if spec.anthropic_model is not None:
            named = spec.anthropic_model(model_name)
            if named is None:
                raise ProviderUnsupported("the gateway reaches this cloud's Anthropic models only")
            pricing_model = named
        cached = self._cached((account.account_id, model_name), provider, secret.version)
        if cached is not None:
            return cached
        configuration = {field: material[field] for field in spec.fields if field in material}
        entry: dict[str, object] = {
            "provider": provider,
            "modelKey": account.account_id,
            "configuration": {**configuration, "model": model_name},
        }
        transport = await self._transports(provider, entry, model_name, account.account_id)
        if transport is None:
            raise ProviderUnsupported("the model account has no direct transport")
        resolved = ResolvedRoute(
            access=AccessRoute(
                kind=RouteKind.GATEWAY,
                api=ModelApi.CHAT_COMPLETIONS,
                provider=machine_name(provider),
                model=model_name,
                account=account,
                pays=ACCOUNT_PAYERS[account.kind],
                region=model_regions().region_of(provider, configuration, model_name, deployment=deployment_region()),
            ),
            transport=transport,
            pricing_model=pricing_model,
            secrets=SecretSet.of(material),
            admission_key=upstream_key(provider, configuration),
        )
        self._cache[account.account_id, model_name] = (provider, secret.version, resolved)
        self._log.info(
            "model_access.resolved",
            orgId=tenant,
            provider=machine_name(provider),
            accountKind=account.kind.value,
            accountId=account.account_id,
            version=secret.version,
            transport=transport.provider,
        )
        return resolved


__all__ = [
    "ACCOUNT_KINDS",
    "ACCOUNT_PAYERS",
    "ACCOUNT_PROVIDERS",
    "ACCOUNT_SECRET_KINDS",
    "CLOUD_TRANSPORTS",
    "AccountProvider",
    "ConfiguredModelAccess",
    "account_refusal",
    "build_configured_transport",
    "machine_name",
    "with_key",
    "without_secrets",
]
