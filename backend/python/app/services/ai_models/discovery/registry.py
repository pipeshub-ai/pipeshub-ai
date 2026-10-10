"""Which provider id uses which discovery strategy."""

from __future__ import annotations

from app.services.ai_models.discovery.strategies import DiscoveryStrategy, build_registry


class DiscoveryRegistry:
    def __init__(self, strategies: dict[str, DiscoveryStrategy] | None = None) -> None:
        self._strategies = strategies if strategies is not None else build_registry()

    def get(self, provider_id: str) -> DiscoveryStrategy | None:
        return self._strategies.get(provider_id)

    def describe(self, provider_id: str) -> dict[str, object]:
        strategy = self.get(provider_id)
        if strategy is None or strategy.mode == "manual":
            return {"mode": "manual", "requiredFields": []}
        return {"mode": strategy.mode, "requiredFields": list(strategy.required_fields)}

    def provider_ids(self) -> list[str]:
        return list(self._strategies)


discovery_registry = DiscoveryRegistry()
