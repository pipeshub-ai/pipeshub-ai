"""AI model provider registry — decorator, builder, and in-memory registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config.ai_models.types import AIModelField, ModelCapability


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def AIModelProvider(
    name: str,
    provider_id: str,
    *,
    description: str = "",
    notice: str = "",
    capabilities: list[ModelCapability] | None = None,
    icon_path: str = "/assets/icons/ai-models/default.svg",
    color: str = "#888888",
    is_popular: bool = False,
    fields: dict[str, list[AIModelField]] | None = None,
) -> Callable[[type], type]:
    """Decorator that attaches ``_provider_metadata`` to a class."""

    if not name:
        raise ValueError("Provider name is required")
    if not provider_id:
        raise ValueError("Provider id is required")
    caps = capabilities or []
    if not caps:
        raise ValueError("At least one capability is required")

    def decorator(cls: type) -> type:
        cls._provider_metadata = {  # type: ignore[attr-defined]
            "name": name,
            "providerId": provider_id,
            "description": description,
            "notice": notice,
            "capabilities": [c.value if isinstance(c, ModelCapability) else c for c in caps],
            "iconPath": icon_path,
            "color": color,
            "isPopular": is_popular,
            "fields": _fields_to_dict(fields or {}),
        }
        cls._is_ai_model_provider = True  # type: ignore[attr-defined]
        return cls

    return decorator


def _fields_to_dict(fields: dict[str, list[AIModelField]]) -> dict[str, list[dict[str, Any]]]:
    return {cap: [f.to_dict() for f in flist] for cap, flist in fields.items()}


# ---------------------------------------------------------------------------
# Builder (fluent API)
# ---------------------------------------------------------------------------

class AIModelProviderBuilder:
    """Fluent builder that produces an ``@AIModelProvider`` decorator."""

    def __init__(self, name: str, provider_id: str | None = None) -> None:
        self._name = name
        self._provider_id = provider_id or _default_provider_id(name)
        self._description = ""
        self._notice = ""
        self._capabilities: list[ModelCapability] = []
        self._icon_path = "/assets/icons/ai-models/default.svg"
        self._color = "#888888"
        self._is_popular = False
        self._shared_fields: list[AIModelField] = []
        self._capability_fields: dict[str, list[AIModelField]] = {}

    # -- metadata setters ---------------------------------------------------

    def with_description(self, desc: str) -> AIModelProviderBuilder:
        self._description = desc
        return self

    def with_notice(self, text: str) -> AIModelProviderBuilder:
        """Short operational warning shown prominently in the UI (e.g. performance)."""
        self._notice = text
        return self

    def with_capabilities(self, caps: list[ModelCapability]) -> AIModelProviderBuilder:
        self._capabilities = list(caps)
        return self

    def with_icon(self, path: str) -> AIModelProviderBuilder:
        self._icon_path = path
        return self

    def with_color(self, color: str) -> AIModelProviderBuilder:
        self._color = color
        return self

    def popular(self, val: bool = True) -> AIModelProviderBuilder:
        self._is_popular = val
        return self

    # -- field setters ------------------------------------------------------

    def add_field(
        self,
        field: AIModelField,
        capability: ModelCapability | None = None,
    ) -> AIModelProviderBuilder:
        if capability is not None:
            cap_key = capability.value if isinstance(capability, ModelCapability) else capability
            self._capability_fields.setdefault(cap_key, []).append(field)
        else:
            self._shared_fields.append(field)
        return self

    # -- terminal -----------------------------------------------------------

    def _build_fields(self) -> dict[str, list[AIModelField]]:
        """Merge shared and per-capability fields."""
        result: dict[str, list[AIModelField]] = {}
        for cap in self._capabilities:
            cap_key = cap.value if isinstance(cap, ModelCapability) else cap
            merged = list(self._shared_fields)
            merged.extend(self._capability_fields.get(cap_key, []))
            result[cap_key] = merged
        return result

    def build_decorator(self) -> Callable[[type], type]:
        """Return the ``@AIModelProvider(...)`` decorator with accumulated config."""
        return AIModelProvider(
            name=self._name,
            provider_id=self._provider_id,
            description=self._description,
            notice=self._notice,
            capabilities=self._capabilities,
            icon_path=self._icon_path,
            color=self._color,
            is_popular=self._is_popular,
            fields=self._build_fields(),
        )


def _default_provider_id(name: str) -> str:
    """openAI, azureOpenAI, etc. — camelCase the display name."""
    if not name or not name.strip():
        return ""
    parts = name.split()
    return parts[0][0].lower() + parts[0][1:] + "".join(p.capitalize() for p in parts[1:])


# ---------------------------------------------------------------------------
# Registry (singleton-style, populated at import time)
# ---------------------------------------------------------------------------

class AIModelRegistry:
    """In-memory registry of AI model provider metadata."""

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, Any]] = {}

    def register(self, provider_class: type) -> bool:
        if not hasattr(provider_class, "_provider_metadata"):
            return False
        meta: dict[str, Any] = provider_class._provider_metadata
        pid = meta["providerId"]
        self._providers[pid] = meta.copy()
        return True

    def list_providers(self) -> list[dict[str, Any]]:
        return list(self._providers.values())

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return self._providers.get(provider_id)

    def get_provider_schema(
        self,
        provider_id: str,
        capability: str | None = None,
    ) -> dict[str, Any] | None:
        meta = self._providers.get(provider_id)
        if meta is None:
            return None
        fields: dict[str, list[dict[str, Any]]] = meta.get("fields", {})
        if capability:
            return {"fields": {capability: fields.get(capability, [])}}
        return {"fields": fields}

    def get_capabilities(self) -> list[str]:
        return [c.value for c in ModelCapability]

    def filter_by_capability(self, capability: str) -> list[dict[str, Any]]:
        return [p for p in self._providers.values() if capability in p.get("capabilities", [])]

    def search(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        results: list[dict[str, Any]] = []
        for p in self._providers.values():
            haystack = " ".join([
                p.get("name", ""),
                p.get("providerId", ""),
                p.get("description", ""),
                p.get("notice", ""),
                " ".join(p.get("capabilities", [])),
            ]).lower()
            if q in haystack:
                results.append(p)
        return results


# Module-level singleton
ai_model_registry = AIModelRegistry()
