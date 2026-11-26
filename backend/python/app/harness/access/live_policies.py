"""``OrgAccessPolicies`` over the org's live layer (CPD-6, MAC-G2): the model gateway's narrowing without a
document read per call.

The layer's ``access`` is the org's model-access rows as Node serves them on the live route; a row an admin
switches off reaches a running run's next model call within the live layer's bound (NFR-23). A layer that could not
be reloaded for longer than its time to live refuses, as an unreadable document does: an unknown narrowing is never
read as "nothing narrowed".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.access.vendor_rules import DEFAULT_ORG_POLICY

if TYPE_CHECKING:
    from app.control_plane.runtime.ports import LiveLayerCache
    from harness_core.access.vendor_rules import OrgAccessPolicy


class LiveOrgAccessPolicies:
    def __init__(self, live: LiveLayerCache) -> None:
        self._live = live

    async def policy(self, tenant: str) -> OrgAccessPolicy:
        layer = await self._live.current(tenant)
        if layer.stale:
            raise AccessPolicyUnavailable("the org's live layer is stale")
        return layer.access if layer.access is not None else DEFAULT_ORG_POLICY


__all__ = ["LiveOrgAccessPolicies"]
