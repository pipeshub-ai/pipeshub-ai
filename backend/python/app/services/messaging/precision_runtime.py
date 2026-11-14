"""The running side of the weight-precision check: where the verdict gets its facts and where it runs."""

from __future__ import annotations  # noqa: I001

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import openai

from app.core.org_context import spawn_detached, system

from app.services.messaging.precision_check import (
    POLL_SECONDS,
    VERDICT_KEY,
    VERDICT_TTL_SECONDS,
    IndexState,
    PrecisionFilter,
    PrecisionVerdict,
    ServerState,
    Verdict,
    set_active_verdict,
)
from app.services.vector_db.collection_pin import CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from app.utils.embedding_server_client import (
    _EMBEDDING_SERVER_API_KEY,
    _embedding_server_base_url,
)

if TYPE_CHECKING:
    from logging import Logger


def _state_of(item: object) -> ServerState:
    extra = getattr(item, "model_extra", None) or {}
    announced = "precision_intent" in extra or hasattr(item, "precision_intent")
    precision = getattr(item, "precision", None)
    if not announced:
        # A server older than the precision fields cannot be serving int8.
        return ServerState("fp32", None, False)
    return ServerState(
        precision if isinstance(precision, str) and precision else None,
        getattr(item, "precision_intent", None),
        bool(getattr(item, "precision_fallback", False)),
        getattr(item, "precision_next", None),
    )


async def fetch_server_states() -> dict[str, ServerState]:
    client = openai.AsyncOpenAI(base_url=_embedding_server_base_url(), api_key=_EMBEDDING_SERVER_API_KEY, max_retries=0, timeout=5.0)
    try:
        page = await client.models.list()
    finally:
        await client.close()
    return {item.id: _state_of(item) for item in page.data}


def build_precision_verdict(config_service, collection_registry, vector_db_service, graph_provider, logger: Logger) -> PrecisionVerdict:  # noqa: ANN001
    guard = PrecisionGuard(CollectionPinStore(config_service, logger), vector_db_service, logger)
    from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL

    async def indexes() -> list[IndexState]:
        # Derived for every organisation of the deployment, each bound for its own reads and under a statement
        # timeout; a derivation that times out is "no derivation" for that index (the claim and the per-batch
        # check still guard every write). Not being able to list the organisations fails the recomputation.
        with system("precision_verdict_orgs"):
            orgs = await graph_provider.get_all_orgs(active=True, raise_on_error=True) or []
        org_ids = [str(o.get("_key") or o.get("id")) for o in orgs if o.get("_key") or o.get("id")]
        found: list[IndexState] = []
        for entry in await collection_registry.list_managed_collections():
            for org in org_ids:
                standing = await guard.standing_for_read(entry.name, org)
                found.append(IndexState(entry.name, entry.embedding_model or DEFAULT_EMBEDDING_MODEL, standing.precision))
        return found

    async def publish(verdict: Verdict) -> None:
        await config_service.set_config(
            VERDICT_KEY,
            {"mismatch": verdict.mismatch, "reasons": verdict.reasons[:5], "holds": verdict.holds, "at": int(time.time() * 1000)},
            ttl_seconds=VERDICT_TTL_SECONDS,
            keep_in_cache=False,
        )

    return PrecisionVerdict(fetch_states=fetch_server_states, indexes=indexes, publish=publish, logger=logger)


async def start_precision_check(container, logger: Logger) -> tuple[PrecisionFilter, asyncio.Task]:  # noqa: ANN001
    """The filter to hand to the indexing consumers, and the task that keeps its verdict current."""
    verdict = build_precision_verdict(
        container.config_service(),
        await container.collection_registry(),
        await container.vector_db_service(),
        await container.graph_provider(),
        logger,
    )
    # The first poll is awaited, so the first message read is already judged against what the indexes hold.
    with contextlib.suppress(Exception):
        await verdict.refresh()

    async def run() -> None:
        while True:
            with contextlib.suppress(Exception):
                await verdict.refresh()
            await asyncio.sleep(POLL_SECONDS)

    set_active_verdict(verdict)
    return PrecisionFilter(verdict, logger), spawn_detached(run(), name="precision-verdict")
