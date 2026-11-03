"""The scope generation, kept in the shared configuration so that it moves by itself.

State, through `ConfigurationService` (never the store directly), one set for the deployment:

- `/services/scopeFields/enabled`: True while the last service that read the flag had it on, False once one had it off.
- `/services/scopeFields/generation/<n>`: one key per generation, created with `create_config_if_absent`. The current generation is the
  largest `n`. Creating `<n+1>` is the compare-and-set: of any number of processes that try it together exactly one creates it.

At start a service reads the flag (`scope_fields_enabled`):

- **on, and the state says it was not on** (off, or never recorded): try to create the next generation key, whoever wins; then record
  enabled=True. Processes that start together bump once. Processes that start later see enabled=True and bump nothing.
- **on, already on**: only read the generation.
- **off**: record enabled=False when it was True; otherwise write nothing, and read nothing else (a deployment that never enabled the
  fields writes no key and reads no graph).

A crash between the two writes leaves enabled=False, so the next start bumps again: an extra bump only makes older stamps stale. If the
state cannot be read the process uses `UNAVAILABLE_GENERATION`, whose stamps are never trusted. The flag is read at start; a service whose flag
changes must be restarted, which is also how the environment changes. A reader that stays up across a bump should re-read `current_generation`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.scope_fields import (
    UNAVAILABLE_GENERATION,
    env_scope_generation,
    set_resolved_generation,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping

    from app.config.configuration_service import ConfigurationService

ENABLED_KEY = "/services/scopeFields/enabled"
GENERATION_DIR = "/services/scopeFields/generation/"


async def current_generation(config: ConfigurationService) -> int:
    """The largest recorded generation, 0 when none."""
    found = [int(k.rsplit("/", 1)[-1]) for k in await config.list_keys_in_directory(GENERATION_DIR) if k.rsplit("/", 1)[-1].isdigit()]
    return max(found, default=0)


async def _create(config: ConfigurationService, n: int) -> None:
    await config.create_config_if_absent(f"{GENERATION_DIR}{n}", {"generation": n})


async def init_scope_generation(config: ConfigurationService, logger: logging.Logger, env: Mapping[str, str] | None = None) -> int | None:
    """Bring the shared state in line with this process's flag and make the generation current for it. Never raises."""
    try:
        enabled = scope_fields_enabled(env)
        was_on = await config.get_config(ENABLED_KEY, use_cache=False, raise_on_error=True) is True
        if not enabled:
            if was_on and not await config.set_config(ENABLED_KEY, False):
                raise RuntimeError("enabled=False was not recorded")
            return None
        generation = await current_generation(config)
        if not was_on:
            await _create(config, generation + 1)
            if not await config.set_config(ENABLED_KEY, True):
                raise RuntimeError("enabled=True was not recorded")
        floor = env_scope_generation(env)  # optional: forces a bump past the recorded generation
        if floor > generation:
            await _create(config, floor)
        generation = await current_generation(config)
        set_resolved_generation(generation)
        return generation
    except Exception as exc:
        logger.warning("scope generation unavailable (stamps of this process are never trusted): %s", type(exc).__name__)
        set_resolved_generation(UNAVAILABLE_GENERATION)
        return UNAVAILABLE_GENERATION
