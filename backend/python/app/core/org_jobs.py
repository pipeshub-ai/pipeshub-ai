"""Run a job's work once per organisation, as ``docs/org-context.md`` prescribes: enumerate under ``system``, act under ``for_org``."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.org_context import for_org, system

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


async def for_each_org(
    graph_provider: IGraphDBProvider, job: str, work: Callable[[str], Awaitable[None]], logger: logging.Logger, *, active: bool = False
) -> None:
    """``await work(org_id)`` for every internal org (only the active ones when ``active``), each under ``for_org(org_id, job=job)``.

    A failed org list propagates: the caller decides what that means. One org's failure is one WARNING, and the later orgs still run.
    """
    with system(job):
        orgs = await graph_provider.get_all_orgs(active=active)
    for org in orgs or []:
        org_id = org.get("_key") or org.get("id")
        if not org_id:
            continue
        try:
            with for_org(str(org_id), job=job):
                await work(str(org_id))
        except Exception as exc:
            # The class only: an exception's text can carry user data.
            logger.warning("%s failed org=%s error=%s", job, org_id, type(exc).__name__)
