"""`PrincipalView` over any `IGraphDBProvider`: the gate and the batch access check the provider already has.

Nothing here re-states the access rule. `get_knowledge_hub_access_context_v2` says who the viewer is and which
apps they pass the gate for; `check_access` (with the viewer's grants resolved once) says which nodes they may read.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError

from ..errors import ViewerScopesUnavailable
from ..ports import Principal

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

CHUNK = 2000  # node ids per check_access call
PARALLEL = 6  # check_access calls in flight for one viewer
CHECK_TIMEOUT_SECONDS = 10.0  # one check_access call; longer is "unavailable", not a wait without end


def _strs(value: object) -> list[str]:
    return [str(x) for x in cast("list[object]", value) if x] if isinstance(value, list) else []


class GraphPrincipalView:
    def __init__(
        self, provider: IGraphDBProvider, chunk: int = CHUNK, parallel: int = PARALLEL,
        check_timeout_seconds: float = CHECK_TIMEOUT_SECONDS,
    ) -> None:
        self._provider = provider
        self._chunk = chunk
        self._parallel = parallel
        self._check_timeout = check_timeout_seconds

    async def principal(self, org_id: str, user_key: str) -> Principal:
        try:
            ctx = await self._provider.get_knowledge_hub_access_v3(user_key, org_id)
        except Exception as exc:
            raise ViewerScopesUnavailable("graph_error") from exc
        grantees = _strs(ctx.get("grantee_ids"))
        if not grantees:
            # A user always holds themself; none means the lookup found no such user, which is not "reaches nothing".
            raise ViewerScopesUnavailable("user_not_found")
        tokens = frozenset({f"u:{user_key}", *(f"g:{g}" for g in grantees if g != user_key)})
        by_connector = ctx.get("by_connector")
        buckets = cast("dict[str, object]", by_connector).values() if isinstance(by_connector, dict) else ()
        granted = frozenset(i for ids in buckets for i in _strs(ids))
        # The access is handed back to `readable`, so the gate and the grants are read once per miss.
        return Principal(tokens, frozenset(_strs(ctx.get("gated_app_ids"))), granted, context=(user_key, ctx))

    async def readable(self, org_id: str, who: Principal, node_ids: frozenset[str]) -> frozenset[str]:
        if not node_ids:
            return frozenset()
        user_key, access = cast("tuple[str, dict[str, object]]", who.context)
        ids = sorted(node_ids)
        try:
            gate = asyncio.Semaphore(self._parallel)

            async def check(chunk: list[str]) -> frozenset[str]:
                async with gate:
                    found = await asyncio.wait_for(
                        self._provider.check_access(user_key, org_id, node_ids=chunk, access=access), self._check_timeout)
                    # A placeholder stub is listed so a browse can open it; it is navigable, not readable content.
                    return found.node_ids - found.placeholder_ids

            tasks = [asyncio.ensure_future(check(ids[i:i + self._chunk])) for i in range(0, len(ids), self._chunk)]
            try:
                checks = await asyncio.gather(*tasks)
            finally:
                # One failed chunk must not leave its siblings running past the caller's slot.
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            found = frozenset[str]().union(*checks)
        except TimeoutError as exc:
            raise ViewerScopesUnavailable("check_timeout") from exc
        except (PermissionVerificationUnavailableError, NotImplementedError) as exc:
            raise ViewerScopesUnavailable(f"check_unavailable:{type(exc).__name__}") from exc
        except Exception as exc:
            raise ViewerScopesUnavailable("graph_error") from exc
        return frozenset(found)
