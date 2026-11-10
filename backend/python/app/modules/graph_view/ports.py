"""What the graph view needs from a source of nodes and edges."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.agents.actions.code_graph.ops import RecordAccess

    from .models import Candidates, ExpandQuery, ResolveRequest


class GraphSource(Protocol):
    #: The OAuth scope a token needs to read this source; checked per request, by the route.
    scope: str

    async def expand(self, q: ExpandQuery, access: RecordAccess) -> Candidates:
        """One hop from ``q.anchors`` (ids the policy admitted), reading only what ``access`` allows."""
        ...

    async def resolve(
        self, req: ResolveRequest, connector_id: str, group_id: str, access: RecordAccess,
    ) -> str | None:
        """The node id of the symbol, or of a file's first symbol, ``req`` names; the policy still judges it."""
        ...
