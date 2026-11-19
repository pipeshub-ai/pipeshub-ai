"""``PIPESHUB_OFFLINE=1``: run on a network with no route to the internet.

Import-free on purpose, like ``app.edition``: telemetry and the package
``__init__`` read it before anything else loads. ``deployment/offline-env.sh``
applies the same switches for processes the entrypoint starts; this module covers
a service started without it. An operator's explicit value always wins.
"""

import os
from collections.abc import MutableMapping

OFFLINE_FLAG = "PIPESHUB_OFFLINE"

# Keep in step with deployment/offline-env.sh (a test compares the two).
OFFLINE_ENV_DEFAULTS: dict[str, str] = {
    "TELEMETRY_COLLECTION_FLAG": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "DO_NOT_TRACK": "1",
}

_TRUE = ("1", "true", "yes", "on")


def offline_enabled(environ: MutableMapping[str, str] = os.environ) -> bool:
    return environ.get(OFFLINE_FLAG, "").strip().lower() in _TRUE


def apply_offline_env(environ: MutableMapping[str, str] = os.environ) -> list[str]:
    """Set the offline switches that the operator has not set; return the names changed."""
    if not offline_enabled(environ):
        return []
    changed = []
    for name, value in OFFLINE_ENV_DEFAULTS.items():
        if not environ.get(name, "").strip():
            environ[name] = value
            changed.append(name)
    return changed
