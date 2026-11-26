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


def _dotenv_values() -> dict[str, str]:
    """Values in the ``.env`` file the services load later (``ConfigurationService``), without loading it.

    The package ``__init__`` runs before that load, so a switch written only in ``.env`` would be missed.
    ``load_dotenv`` never overrides a variable that is already set, so the process environment still wins.
    """
    try:
        from dotenv import dotenv_values, find_dotenv

        path = find_dotenv(usecwd=True)
        return {k: v for k, v in dotenv_values(path).items() if v is not None} if path else {}
    except Exception:
        return {}


def _setting(name: str, environ: MutableMapping[str, str], dotenv: dict[str, str]) -> str:
    return environ[name] if name in environ else dotenv.get(name, "")


def offline_enabled(environ: MutableMapping[str, str] = os.environ, dotenv: dict[str, str] | None = None) -> bool:
    values = _dotenv_values() if dotenv is None and environ is os.environ and OFFLINE_FLAG not in environ else (dotenv or {})
    return _setting(OFFLINE_FLAG, environ, values).strip().lower() in _TRUE


def apply_offline_env(environ: MutableMapping[str, str] = os.environ, dotenv: dict[str, str] | None = None) -> list[str]:
    """Set the offline switches that the operator has not set; return the names changed.

    "Set" means in the environment or in ``.env``: an explicit ``.env`` value keeps precedence over the default."""
    values = _dotenv_values() if dotenv is None and environ is os.environ else (dotenv or {})
    if not offline_enabled(environ, values):
        return []
    changed = []
    for name, value in OFFLINE_ENV_DEFAULTS.items():
        if environ.get(name, "").strip():
            continue
        explicit = values.get(name, "").strip()
        if explicit:
            # load_dotenv skips a name already in the environment, even a blank one.
            if name in environ:
                environ[name] = explicit
            continue
        environ[name] = value
        changed.append(name)
    return changed
