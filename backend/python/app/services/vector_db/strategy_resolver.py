"""Resolves the collection strategy to use for this deployment.

The strategy is a deployment-level decision, not a per-request one: it is read
from the ``VECTOR_COLLECTION_STRATEGY`` env var exactly once, on the first
startup that finds nothing persisted, and from then on every service (indexing,
query, connectors) reads the **persisted** value. This mirrors how
``VECTOR_DB_TYPE`` and ``MESSAGE_BROKER`` are chosen today, and it is what makes
an accidental env-var edit a startup failure instead of silent data-visibility
loss (see the plan's "Changing the Strategy Safely" section for why an in-place
switch is never safe).

Which makes *how* the value is read and written the load-bearing part.
``ConfigurationService.get_config`` returns its ``default`` on any error, store
outages included, so "the read came back empty" cannot be trusted to mean
"nothing is persisted". Writing the env default on the strength of that reading
would overwrite a live ``per_org`` deployment with ``single`` during a brief KV
outage, and every subsequent read and delete would resolve to a collection
holding no data. So persistence goes through an atomic create-if-absent that
can never overwrite, and a failed create is followed by a read-back: whatever
is actually stored wins, and if that cannot be read either, startup fails
rather than guessing.
"""

import os

# Importing the strategies package registers every built-in strategy
# (currently just "single") with CollectionStrategyFactory as a side effect.
from app.services.vector_db import strategies  # noqa: F401
from app.services.vector_db.strategy import (
    CollectionStrategy,
    CollectionStrategyFactory,
)

STRATEGY_CONFIG_KEY = "/services/vectordb/collection_strategy"
STRATEGY_ENV_VAR = "VECTOR_COLLECTION_STRATEGY"
DEFAULT_STRATEGY_NAME = "single"

# The strategy cannot change without the operator running the rebuild procedure
# (an in-place switch fails fast, by design), so resolving it is a
# once-per-process concern. Memoising it keeps ad-hoc callers off the KV store.
#
# Deliberately unlocked. Indexing runs work on two event loops (see the
# loop-keyed locks in membership.py), and a module-level asyncio.Lock binds to
# the first loop that awaits it and raises on every other — turning a
# cross-loop resolve into a hard error. Two concurrent resolutions are harmless
# now that persistence is create-if-absent: both end up reading or creating the
# same value, and the second assignment overwrites an equal one.
_resolved_strategy: CollectionStrategy | None = None


def reset_strategy_cache() -> None:
    """Drop the memoised strategy. For tests, and after a rebuild re-persists it."""
    global _resolved_strategy
    _resolved_strategy = None


class StrategyConfigurationError(Exception):
    """The configured strategy cannot be established with confidence.

    Covers a persisted value contradicting the env var, an unregistered name,
    and a KV store that will not say what is persisted. All three are
    startup-time problems that must fail loudly rather than silently resolving
    to the wrong collections.
    """


def _validate_registered(name: str) -> str:
    if name not in CollectionStrategyFactory.registered_names():
        raise StrategyConfigurationError(
            f"Unknown collection strategy '{name}'. Registered strategies: "
            f"{CollectionStrategyFactory.registered_names()}. An Enterprise "
            f"strategy must be imported before the strategy is resolved."
        )
    return name


def _assert_env_agrees(persisted_name: str) -> None:
    env_name = os.getenv(STRATEGY_ENV_VAR)
    if env_name and env_name != persisted_name:
        raise StrategyConfigurationError(
            f"{STRATEGY_ENV_VAR}={env_name!r} contradicts the persisted collection "
            f"strategy {persisted_name!r}. Changing the strategy for an existing "
            f"deployment requires the explicit rebuild procedure (see the flexible "
            f"VectorDB collection strategy plan), not an env var edit."
        )


async def resolve_persisted_strategy_name(config_service, logger) -> str:
    """Read the persisted strategy name, persisting the env default on first use.

    Decision tree:
    - Persisted value present -> use it; a contradicting env var fails fast.
    - Nothing read back -> try to *create* the env default. A successful create
      proves the key really was absent. A failed create means someone already
      owns the value, so read it back and use theirs.
    - Still nothing on read-back -> the store is not answering. Fail, rather
      than persist a default over a value we merely could not see.
    """
    persisted_name = await config_service.get_config(STRATEGY_CONFIG_KEY, default=None)
    if persisted_name:
        _assert_env_agrees(persisted_name)
        return persisted_name

    candidate = _validate_registered(
        os.getenv(STRATEGY_ENV_VAR) or DEFAULT_STRATEGY_NAME
    )

    try:
        created = await config_service.create_config_if_absent(
            STRATEGY_CONFIG_KEY, candidate
        )
    except Exception as e:
        raise StrategyConfigurationError(
            f"Could not persist the initial collection strategy: {e}. Refusing to "
            f"start rather than run against an unknown collection layout."
        ) from e

    if created:
        logger.info("Persisted initial collection strategy '%s'", candidate)
        return candidate

    # The key existed after all — the first read was a store hiccup, or another
    # service won the race. Whatever is stored is authoritative.
    persisted_name = await config_service.get_config(STRATEGY_CONFIG_KEY, default=None)
    if not persisted_name:
        raise StrategyConfigurationError(
            "A collection strategy is already persisted but could not be read back. "
            "Refusing to start on a guessed layout — reads and deletes would "
            "silently target collections that hold no data."
        )
    logger.info(
        "Collection strategy '%s' was already persisted; using it", persisted_name
    )
    _assert_env_agrees(persisted_name)
    return persisted_name


async def resolve_strategy(config_service, logger) -> CollectionStrategy:
    """Resolve and instantiate the strategy this deployment should use."""
    global _resolved_strategy
    if _resolved_strategy is not None:
        return _resolved_strategy
    name = await resolve_persisted_strategy_name(config_service, logger)
    _resolved_strategy = CollectionStrategyFactory.create(_validate_registered(name))
    return _resolved_strategy
