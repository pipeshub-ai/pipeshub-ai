"""Run a strategy, classify the rows, and cache the successful result."""

from __future__ import annotations

from app.services.ai_models.discovery.cache import DiscoveryCache
from app.services.ai_models.discovery.classifier import classify
from app.services.ai_models.discovery.http import MAX_MODELS, DiscoveryHttp
from app.services.ai_models.discovery.registry import DiscoveryRegistry, discovery_registry
from app.services.ai_models.discovery.types import (
    DiscoveryErrorCode,
    DiscoveryHttpError,
    DiscoveryRequest,
    DiscoveryResult,
)


class ModelDiscoveryService:
    def __init__(
        self,
        http: DiscoveryHttp | None = None,
        cache: DiscoveryCache | None = None,
        registry: DiscoveryRegistry | None = None,
    ) -> None:
        self._http = http or DiscoveryHttp()
        self._cache = cache or DiscoveryCache()
        self._registry = registry or discovery_registry

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        strategy = self._registry.get(request.provider)
        if strategy is None or strategy.mode == "manual":
            return DiscoveryResult(
                supported=False,
                error_code=DiscoveryErrorCode.NOT_SUPPORTED,
                message="This provider has no model list. Enter the model id.",
            )
        missing = [
            name
            for name in strategy.required_fields
            if not str(request.configuration.get(name) or "").strip()
        ]
        if missing:
            return DiscoveryResult(
                supported=True,
                error_code=DiscoveryErrorCode.INVALID_RESPONSE,
                message="Fill in " + ", ".join(missing) + " before fetching models.",
            )
        endpoint = str(request.configuration.get("endpoint") or "")
        credential = str(
            request.configuration.get("apiKey")
            or request.configuration.get("awsAccessSecretKey")
            or ""
        )
        region = str(request.configuration.get("region") or "")
        access_key_id = str(request.configuration.get("awsAccessKeyId") or "")
        query = (request.query or "").strip()
        cache_key = self._cache.key(
            request.provider, endpoint, credential, query, region, access_key_id,
        )
        cached = self._cache.get(cache_key)
        if isinstance(cached, DiscoveryResult):
            return _for_capability(cached, request.capability)
        try:
            raw_models, warnings = await strategy.list_models(
                request.configuration, self._http, query or None
            )
        except DiscoveryHttpError as exc:
            return DiscoveryResult(supported=True, error_code=exc.code, message=exc.message)
        except ValueError as exc:
            return DiscoveryResult(
                supported=True,
                error_code=DiscoveryErrorCode.UNREACHABLE,
                message=str(exc) or "The model endpoint is not allowed.",
            )
        discovered = []
        for raw in raw_models:
            model = classify(request.provider, raw)
            if model is None:
                continue
            discovered.append(model)
        result = DiscoveryResult(supported=True, models=discovered, warnings=warnings)
        self._cache.set(cache_key, result)
        return _for_capability(result, request.capability)


def _for_capability(result: DiscoveryResult, capability: str | None) -> DiscoveryResult:
    """Filter a cached full list. The cached object itself stays unfiltered."""
    models = list(result.models)
    if capability:
        models = [
            model
            for model in models
            if capability in model.capabilities or "other" in model.capabilities
        ]
    warnings = list(result.warnings)
    if len(models) > MAX_MODELS:
        models = models[:MAX_MODELS]
        warnings.append(f"Listing stopped after {MAX_MODELS} models.")
    return DiscoveryResult(supported=True, models=models, warnings=warnings)
