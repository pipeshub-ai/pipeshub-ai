"""The small model the coverage loop plans and checks with, and a JSON call on it.

The ``query`` role's model (else the default model) at low effort, the same one
grep generation uses: these calls classify short texts and must not run at the
chat's own, often high, reasoning effort.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, cast

from cachetools import TTLCache
from langchain_core.messages import HumanMessage, SystemMessage

from app.utils.aimodels import coerce_message_content_to_text
from app.utils.llm import get_llm_for_role

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

_ROLE = "query"
_LOOKUP_TIMEOUT_S = 5.0
_llm_cache: TTLCache[str, Any] = TTLCache[str, Any](maxsize=256, ttl=300.0)


async def coverage_llm(config_service: Any, org_id: str, fallback: Any = None) -> Any:  # noqa: ANN401
    """The planning model for this org, cached; ``fallback`` when none can be built."""
    cached: Any = _llm_cache.get(org_id)
    if cached is not None:
        return cached
    if config_service is None:
        return fallback
    try:
        llm, _config = await asyncio.wait_for(
            get_llm_for_role(config_service, _ROLE, reasoning_effort="low"), timeout=_LOOKUP_TIMEOUT_S,
        )
    except Exception as exc:
        logger.info("coverage_loop: no planning model, using the chat's: %r", exc)
        return fallback
    _llm_cache[org_id] = llm
    return llm


def parse_json_object(text: str | None) -> dict[str, Any] | None:
    """The outermost JSON object in a model reply (fences and prose around it
    are ignored), or None."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if not 0 <= start < end:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


async def complete_json(
    llm: "BaseChatModel | None",
    system: str,
    user: str,
    *,
    timeout: float,
    purpose: str,
    on_usage: "Callable[[BaseMessage], None] | None" = None,
) -> dict[str, Any] | None:
    """One bounded call; the reply's JSON object, or None on any failure."""
    if llm is None:
        return None
    try:
        response = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)]), timeout=timeout,
        )
    except Exception as exc:
        logger.warning("coverage_loop: %s call failed: %r", purpose, exc)
        return None
    if on_usage is not None:
        try:
            on_usage(response)
        except Exception:
            logger.debug("coverage_loop: usage report failed", exc_info=True)
    parsed = parse_json_object(coerce_message_content_to_text(getattr(response, "content", "")))
    if parsed is None:
        logger.info("coverage_loop: %s reply was not a JSON object", purpose)
    return parsed


__all__ = ["complete_json", "coverage_llm", "parse_json_object"]
