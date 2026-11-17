"""How a call site reaches the cache: the process's cache, and the key of the record in flight.

A key needs an org and a record. Both come from the ambient model-call context, which the record's message
handler binds from the verified envelope (and the parsing service from its verified token claims): never from
a request body. A call with no such context (a chat upload, a query) has no key, so it computes as before.
"""

import logging
import threading
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, TypeVar

from app.core.org_context import spawn_detached
from app.services.model_identity.call_context import peek_call_context
from app.services.paid_cache.cache import (
    NotCached,
    NullPaidResultCache,
    PaidResultCache,
    enabled,
)
from app.services.paid_cache.keys import (
    CacheKey,
    llm_params,
    model_identity,
    params_digest,
    prompt_digest,
    sha256_hex,
    valid_record_id,
)
from app.services.paid_cache.locks import InProcessLocks, LockBackend, RedisLocks
from app.services.paid_cache.retention import sweep_record
from app.services.paid_cache.run import RunScope, begin_run, current_run
from app.services.paid_cache.store import NodePaidResultStore
from app.utils.loop_local import LoopLocal

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

_lock = threading.Lock()
_caches: dict[int, PaidResultCache] = {}
_refused_ids: set[str] = set()


def _refuse_record_id(record_id: str) -> None:
    """Once per id (bounded): a record the cache cannot hold is indexed as before, uncached."""
    with _lock:
        if record_id in _refused_ids or len(_refused_ids) >= 1000:
            return
        _refused_ids.add(record_id)
    logging.getLogger(__name__).warning(
        "Paid-result cache: record id %.40r is not a valid storage id (1-128 of A-Z a-z 0-9 _ -); "
        "its model calls are not cached", record_id,
    )


def ambient_identity() -> tuple[str, str] | None:
    """``(org_id, record_id)`` of the record this task is working on, or None."""
    ctx = peek_call_context()
    if ctx is None or not ctx.record_id:
        return None
    org_id = ctx.principal.org_id
    if not org_id:
        return None
    if not valid_record_id(ctx.record_id):
        _refuse_record_id(ctx.record_id)
        return None
    return org_id, ctx.record_id


async def _redis_locks_of(config_service: "ConfigurationService") -> RedisLocks:
    """Locks on a client of the messaging provider, keyed under the ``<ns>:`` of its ``REDIS_KEY_NAMESPACE``."""
    from app.services.redis.config import ClientOptions, RedisConnectionConfig
    from app.services.redis.connection_provider_factory import get_redis_provider

    provider = get_redis_provider(RedisConnectionConfig.from_redis_config(await config_service.get_redis_config()))
    return RedisLocks(provider.create_client(ClientOptions(decode_responses=True)), provider.key_namespace)


def _redis_locks(config_service: "ConfigurationService", logger: logging.Logger) -> Any:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
    clients: LoopLocal[Any] = LoopLocal(lambda: {})
    fallback = InProcessLocks()

    async def backend() -> LockBackend:
        holder = clients.get()
        locks = holder.get("locks")
        if locks is None:
            try:
                locks = await _redis_locks_of(config_service)
            except Exception as exc:
                logger.warning("Paid-result locks use this process only: Redis unavailable (%s)", type(exc).__name__)
                return fallback
            holder["locks"] = locks
        return locks  # type: ignore[no-any-return]

    return backend


def paid_cache_for(
    config_service: "ConfigurationService", logger: logging.Logger | None = None,
) -> "PaidResultCache | NullPaidResultCache":
    """The process's cache over ``config_service``'s storage. One per configuration service."""
    log = logger or logging.getLogger(__name__)
    with _lock:
        cache = _caches.get(id(config_service))
        if cache is None:
            from app.modules.transformers.blob_storage import BlobStorage

            cache = PaidResultCache(
                NodePaidResultStore(BlobStorage(log, config_service), log),  # type: ignore[arg-type]
                _redis_locks(config_service, log),
                logger=log,
            )
            _caches[id(config_service)] = cache
        return cache


def cache_key(
    kind: str, *, llm: object, prompt: str, input_digest: str, kind_version: int = 1,
    params: dict[str, Any] | None = None,
) -> CacheKey | None:
    """The key of one call of the record in flight, or None when there is no record to scope it to."""
    identity = ambient_identity()
    if identity is None:
        return None
    return CacheKey(
        org_id=identity[0],
        record_id=identity[1],
        kind=kind,
        kind_version=kind_version,
        model_identity=model_identity(llm),
        prompt_digest=prompt_digest(prompt),
        params_digest=params_digest(llm_params(llm, **(params or {}))),
        input_digest=input_digest,
    )


_ambient: ContextVar["PaidResultCache | NullPaidResultCache | None"] = ContextVar("paid_cache_ambient", default=None)


def remember_cache(config_service: "ConfigurationService", logger: logging.Logger | None = None) -> None:
    """Makes the process's cache the ambient one for this task and the tasks it starts.

    Called where a site already holds its configuration service (the indexing model's resolution), so the
    helpers below that are handed only a model can still reach the cache.
    """
    _ambient.set(paid_cache_for(config_service, logger))


def ambient_cache() -> "PaidResultCache | NullPaidResultCache | None":
    return _ambient.get()


def set_ambient_cache(cache: "PaidResultCache | NullPaidResultCache | None") -> None:
    _ambient.set(cache)


T = TypeVar("T")


async def cached_model_call(
    kind: str,
    *,
    llm: object,
    template: object,
    rendered: str,
    call: Callable[[], Awaitable[T | None]],
    encode: Callable[[T], Any],
    decode: Callable[[Any], T],
    params: dict[str, Any] | None = None,
    cacheable: Callable[[T], bool] = lambda _value: True,
    config_service: "ConfigurationService | None" = None,
) -> T | None:
    """``call()``, reusing the record's stored result for this exact input.

    ``rendered`` is the full prompt as sent (it carries the content), ``template`` the template whose edit
    must change the key. ``None`` and results ``cacheable`` rejects are returned but not stored. With no
    record in flight, or no cache, this is just ``call()``. ``config_service`` names the storage the cache
    uses; without it the ambient cache (``remember_cache``) is used.
    """
    cache = paid_cache_for(config_service) if config_service is not None else ambient_cache()
    key = None if cache is None else cache_key(
        kind, llm=llm, prompt=template_text_of(template), input_digest=sha256_hex(rendered), params=params,
    )
    if cache is None or key is None:
        return await call()

    async def compute() -> Any:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        value = await call()
        if value is None:
            return NotCached(None)
        encoded = encode(value)
        return encoded if cacheable(value) else NotCached(encoded)

    # A stored value that no longer decodes is a miss: recomputed, and replaced.
    stored = await cache.get_or_compute(key, compute, validate=decode)
    return None if stored is None else decode(stored)


def template_text_of(template: object) -> str:
    from app.services.paid_cache.keys import template_text

    return template_text(template)


def start_run(
    content_rev: str | None, writer: str, *, refresh: bool = False, stage_deadline_s: float | None = None,
) -> RunScope | None:
    """Opens the retention manifest of this writer's work on the record in flight.

    A ``refreshPaidOutputs`` run opened earlier for the same record keeps its flag. None when no record is in
    flight or the cache is off. ``refresh`` is a caller's own request (the parsing service is told by the indexing
    side that its run is a refresh).
    """
    identity = ambient_identity()
    if identity is None or not enabled():
        return None
    prior = current_run()
    refresh = refresh or (prior is not None and prior.matches(*identity) and prior.refresh)
    return begin_run(identity[0], identity[1], content_rev, writer, refresh=refresh, stage_deadline_s=stage_deadline_s)


async def complete_run(
    run: RunScope | None, config_service: "ConfigurationService", logger: logging.Logger | None = None,
    *, sweep: bool = False,
) -> None:
    """Writes the run's final manifest; with ``sweep``, queues the retention sweep for the record.

    Best effort: a manifest or sweep that cannot be written is retried by the next completed revision and
    never fails the record.
    """
    if run is None or config_service is None:
        return
    log = logger or logging.getLogger(__name__)
    cache = paid_cache_for(config_service, log)
    if not isinstance(cache, PaidResultCache):
        return
    try:
        await run.finish(cache.store)
    except Exception as exc:
        log.warning("Paid-result manifest not written for record %s (%s)", run.record_id, type(exc).__name__)
        return
    if sweep:
        spawn_detached(_sweep(cache, run, log), name="paid-cache-sweep")


async def abandon_run(
    run: RunScope | None, config_service: "ConfigurationService", logger: logging.Logger | None = None,
) -> None:
    """A run that failed: its open manifest is brought up to date, so what it paid for stays protected.

    Best effort and bounded by the write deadline; never raises (the failure being handled is the one that matters).
    """
    if run is None or config_service is None or run.finished:
        return
    log = logger or logging.getLogger(__name__)
    try:
        cache = paid_cache_for(config_service, log)
        if isinstance(cache, PaidResultCache):
            await run.flush(cache.store)
    except Exception as exc:
        log.warning("Paid-result manifest of a failed run not updated for record %s (%s)", run.record_id, type(exc).__name__)


async def _sweep(cache: PaidResultCache, run: RunScope, log: logging.Logger) -> None:
    try:
        await sweep_record(cache.store, run.org_id, run.record_id, logger=log)
    except Exception as exc:
        log.warning("Paid-result sweep failed for record %s (%s); the next revision retries it", run.record_id, type(exc).__name__)
