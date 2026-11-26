"""The name a person sees for a configured model (P2.2).

One rule with Node's ``modelDisplayName`` (``contracts/harness/vectors/model-display-names.json``):
the admin's display name, qualified by the model when the entry lists several; else the model
name; ``Unavailable model`` when the entry is gone. The model key, a deployment name and keys are
never read, so they can never be shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

UNAVAILABLE_MODEL = "Unavailable model"


def _model_names(entry: Mapping[str, Any]) -> list[str]:
    configuration = entry.get("configuration")
    raw = cast("Mapping[str, object]", configuration).get("model") if isinstance(configuration, Mapping) else None
    return [name.strip() for name in raw.split(",") if name.strip()] if isinstance(raw, str) else []


def model_display_name(entry: Mapping[str, Any] | None, model_name: str | None) -> str:
    if entry is None:
        return UNAVAILABLE_MODEL
    names = _model_names(entry)
    friendly = entry.get("modelFriendlyName")
    friendly = friendly.strip() if isinstance(friendly, str) else ""
    if friendly:
        return f"{friendly} ({model_name})" if len(names) > 1 and model_name else friendly
    return model_name or (names[0] if names else UNAVAILABLE_MODEL)


__all__ = ["UNAVAILABLE_MODEL", "model_display_name"]
