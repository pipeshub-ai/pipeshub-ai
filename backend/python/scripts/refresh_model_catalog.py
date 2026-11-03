#!/usr/bin/env python3
"""Refresh the pruned LiteLLM model catalog shipped with discovery.

The upstream file is MIT licensed. We keep only the fields the classifier
reads so the snapshot stays usable offline.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from pathlib import Path

_URLS = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
    "https://raw.githubusercontent.com/BerriAI/litellm/main/litellm/model_prices_and_context_window.json",
)
_KEEP = (
    "mode",
    "litellm_provider",
    "max_input_tokens",
    "max_output_tokens",
    "supports_vision",
    "supports_function_calling",
    "supports_reasoning",
    "supports_response_schema",
    "deprecation_date",
)
_OUT = Path(__file__).resolve().parents[1] / "app/services/ai_models/discovery/data/model_catalog.json"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _download() -> dict:
    last_error: Exception | None = None
    for url in _URLS:
        try:
            with urllib.request.urlopen(url, timeout=60, context=_ssl_context()) as response:
                payload = json.load(response)
            if isinstance(payload, dict):
                return payload
        except Exception as exc:  # noqa: BLE001 — try the next known path
            last_error = exc
    raise SystemExit(f"Could not download the LiteLLM catalog: {last_error}")


def main() -> None:
    raw = _download()
    pruned: dict[str, dict] = {}
    for key, value in raw.items():
        if not isinstance(value, dict) or key == "sample_spec":
            continue
        kept = {name: value[name] for name in _KEEP if name in value}
        if kept:
            pruned[key] = kept
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(pruned, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(pruned)} models to {_OUT}")


if __name__ == "__main__":
    main()
