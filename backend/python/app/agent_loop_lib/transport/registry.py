from __future__ import annotations

from collections.abc import Callable

from app.agent_loop_lib.transport.base import LLMTransport


class TransportRegistry:
    """Maps provider names to transport factories. Caches instances after first resolve."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], LLMTransport]] = {}
        self._cache: dict[str, LLMTransport] = {}

    def register(self, provider: str, factory: Callable[[], LLMTransport]) -> None:
        self._factories[provider] = factory
        self._cache.pop(provider, None)  # invalidate cached instance on re-register

    def resolve(self, provider: str) -> LLMTransport:
        from app.agent_loop_lib.core.exceptions import RegistryError
        if provider not in self._factories:
            raise RegistryError(f"No transport registered for provider '{provider}'")
        if provider not in self._cache:
            self._cache[provider] = self._factories[provider]()
        return self._cache[provider]

    def has(self, provider: str) -> bool:
        return provider in self._factories

    def providers(self) -> list[str]:
        return list(self._factories.keys())


class LazyTransport:
    """Thin `complete_structured`-only facade over a `TransportRegistry`
    entry, resolved (and thus instantiated) only on first actual use —
    needed anywhere a feature (e.g. `RubricGrader`, `LLMSkillExtractor`)
    wants "the configured LLM" without the caller paying for a real
    transport client it may never end up using. Shared by `ControlPlane`
    and `PipesHubAgentFactory` rather than each keeping its own copy."""

    def __init__(self, transport_registry: TransportRegistry, provider: str) -> None:
        self._transport_registry = transport_registry
        self._provider = provider

    async def complete_structured(self, *args, **kwargs):
        transport = self._transport_registry.resolve(self._provider)
        return await transport.complete_structured(*args, **kwargs)
