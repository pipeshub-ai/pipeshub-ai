"""Run a job's work once per organisation, as ``docs/org-context.md`` prescribes: enumerate under ``system``, act under ``for_org``."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.org_context import for_org, system

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


async def for_each_org(
    graph_provider: IGraphDBProvider, job: str, work: Callable[[str], Awaitable[None]], logger: logging.Logger, *, active: bool = False,
    raise_on_error: bool = False,
) -> list[str]:
    """``await work(org_id)`` for every internal org (only the active ones when ``active``), each under ``for_org(org_id, job=job)``.

    A failed org list propagates: the caller decides what that means. Arango and Neo4j log a failed list and answer no orgs unless
    ``raise_on_error``, which a one-time job must pass so that it does not take the failure as done. One org's failure is one WARNING,
    and the later orgs still run. Returns the ids of the orgs whose work raised, so a one-time job can stay pending until every org ran.
    """
    with system(job):
        orgs = await graph_provider.get_all_orgs(active=active, **({"raise_on_error": True} if raise_on_error else {}))
    failed: list[str] = []
    for org in orgs or []:
        org_id = org.get("_key") or org.get("id")
        if not org_id:
            continue
        try:
            with for_org(str(org_id), job=job):
                await work(str(org_id))
        except Exception as exc:
            failed.append(str(org_id))
            # The class only: an exception's text can carry user data.
            logger.warning("%s failed org=%s error=%s", job, org_id, type(exc).__name__)
    return failed


def of_org(docs: list[dict] | None, org_id: str) -> list[dict]:
    """The bound org's docs: Arango and Neo4j ignore the bound org and answer for every org.

    A doc with no orgId is kept: on Postgres it is in the bound org's partition (a
    legacy App nothing backfilled), and elsewhere the first org's pass handles it,
    so the work done on it must be idempotent.
    """
    return [d for d in docs or [] if d.get("orgId") in (org_id, None)]
