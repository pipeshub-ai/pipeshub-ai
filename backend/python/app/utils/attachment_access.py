"""Per-user authorization for the chat attachments a request names.

Attachment refs (``recordId`` + ``virtualRecordId``) arrive in the request
payload for the current turn and inside ``previous_conversations``, and every
resolver downstream reads the blob by ``virtualRecordId`` scoped only by org.
``authorize_query_attachments`` is the single gate both agent-loop entry points
(``run_chat_stream`` and ``run_agent_loop_stream``) apply before anything else
sees the payload, so an id copied from another user's conversation is dropped
before any blob read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from app.services.artifact_registry.models import Actor
from app.services.record_content.authorizer import TieredRecordAuthorizer
from app.services.record_content.models import RecordAccessDeniedError

if TYPE_CHECKING:
    import logging

    from app.agents.agent_loop.cancellation.registry import RunOwner
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["actors_for_run", "authorize_attachments", "authorize_query_attachments"]

# A virtualRecordId is a content identity shared by deduplicated copies; only
# the first few are tried when a ref carries no recordId.
_MAX_RECORDS_PER_VRID = 5

_AttachmentKey = tuple[str, str]


def actors_for_run(user_info: Mapping[str, Any], run_owner: RunOwner | None) -> list[Actor]:
    """Service-account agents run as the agent creator while the attachment was
    uploaded by the authenticated caller (``run_owner``); either may read it."""
    actors: list[Actor] = []
    candidates = [(user_info.get("orgId"), user_info.get("userId"))]
    if run_owner is not None:
        candidates.append((run_owner.org_id, run_owner.user_id))
    for org_id, user_id in candidates:
        if not org_id or not user_id:
            continue
        actor = Actor(org_id=str(org_id), user_id=str(user_id))
        if actor not in actors:
            actors.append(actor)
    return actors


def _key(att: object) -> _AttachmentKey | None:
    if not isinstance(att, Mapping):
        return None
    record_id = str(att.get("recordId") or "")
    vrid = str(att.get("virtualRecordId") or "")
    if not record_id and not vrid:
        return None
    return record_id, vrid


async def _candidate_records(graph_provider: IGraphDBProvider, record_id: str, vrid: str) -> list[Any]:
    if record_id:
        record = await graph_provider.get_record_by_id(record_id)
        if record is None:
            return []
        # Blob reads go by virtualRecordId, so a readable recordId paired with
        # someone else's virtualRecordId must not pass.
        if vrid and getattr(record, "virtual_record_id", None) != vrid:
            return []
        return [record]
    keys = await graph_provider.get_records_by_virtual_record_id(vrid)
    records = []
    for key in (keys or [])[:_MAX_RECORDS_PER_VRID]:
        record = await graph_provider.get_record_by_id(key)
        if record is not None:
            records.append(record)
    return records


async def _is_readable(
    key: _AttachmentKey,
    actors: list[Actor],
    authorizer: TieredRecordAuthorizer,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
) -> bool:
    record_id, vrid = key
    try:
        for record in await _candidate_records(graph_provider, record_id, vrid):
            for actor in actors:
                try:
                    await authorizer.authorize(actor, record)
                    return True
                except RecordAccessDeniedError:
                    continue
    except Exception:
        logger.warning(
            "attachment authorization failed; dropping record=%s vrid=%s",
            record_id, vrid, exc_info=True,
        )
    return False


async def _authorized_keys(
    actors: list[Actor],
    attachments: Iterable[object],
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
) -> set[_AttachmentKey]:
    keys = list(dict.fromkeys(k for k in map(_key, attachments) if k is not None))
    if not keys or not actors:
        return set()
    authorizer = TieredRecordAuthorizer(graph_provider)
    verdicts = await asyncio.gather(
        *(_is_readable(k, actors, authorizer, graph_provider, logger) for k in keys),
    )
    allowed = {k for k, ok in zip(keys, verdicts) if ok}
    denied = [k for k in keys if k not in allowed]
    if denied:
        logger.warning(
            "dropped %d unauthorized attachment(s) for user=%s: %s",
            len(denied), actors[0].user_id,
            ", ".join(f"record={r or '-'} vrid={v or '-'}" for r, v in denied),
        )
    return allowed


def _keep(attachments: list[Any], allowed: set[_AttachmentKey]) -> list[Any]:
    return [att for att in attachments if _key(att) in allowed]


async def authorize_attachments(
    actors: list[Actor],
    attachments: list[Any],
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
) -> list[Any]:
    """Return the attachments some actor may read, in their original order.
    Denials, unknown ids and lookup errors all drop the entry (fail closed)."""
    allowed = await _authorized_keys(actors, attachments, graph_provider, logger)
    return _keep(attachments, allowed)


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


async def authorize_query_attachments(
    query_info: dict[str, Any],
    actors: list[Actor],
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
) -> dict[str, Any]:
    """Filter ``attachments`` and every ``previous_conversations[i].attachments``
    in one pass; returns a new dict and leaves ``query_info`` untouched."""
    current = _as_list(query_info.get("attachments"))
    turns = _as_list(query_info.get("previous_conversations"))
    history = [
        att
        for turn in turns if isinstance(turn, Mapping)
        for att in _as_list(turn.get("attachments"))
    ]
    if not current and not history:
        return query_info

    try:
        allowed = await _authorized_keys(actors, [*current, *history], graph_provider, logger)
    except Exception:
        logger.warning("attachment authorization failed; dropping all attachments", exc_info=True)
        allowed = set()

    filtered = {**query_info, "attachments": _keep(current, allowed)}
    if history:
        filtered["previous_conversations"] = [
            {**turn, "attachments": _keep(_as_list(turn.get("attachments")), allowed)}
            if isinstance(turn, Mapping) and "attachments" in turn
            else turn
            for turn in turns
        ]
    return filtered
