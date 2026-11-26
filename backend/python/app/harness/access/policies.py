"""An org's narrowing of the vendor rules (A1) as Node stores it::

    {"disabledRules": [<row id>], "consumerPlans": false, "updatedAt": <ms>, "updatedBy": "p_<user>"}

plus the members the bound ``ModelRegions`` names for a pin (``pin_fields``; none by default). An entry that is not
exactly this shape refuses (``access_policy_unavailable``): an unreadable narrowing is never read as "nothing
narrowed".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from app.harness.placement import model_regions
from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.access.vendor_rules import OrgAccessPolicy
from harness_core.models.placement import PinInvalid

_ENTRY_FIELDS = frozenset({"disabledRules", "consumerPlans", "updatedAt", "updatedBy"})


def policy_from_entry(entry: object) -> OrgAccessPolicy:
    regions = model_regions()
    if not isinstance(entry, Mapping) or set(cast("Mapping[str, object]", entry)) - _ENTRY_FIELDS - regions.pin_fields:
        raise AccessPolicyUnavailable("an org's access policy is not an access policy")
    fields = cast("Mapping[str, object]", entry)
    disabled, consumer = fields.get("disabledRules", []), fields.get("consumerPlans", False)
    if not isinstance(disabled, list) or not all(isinstance(r, str) for r in cast("list[object]", disabled)):
        raise AccessPolicyUnavailable("an org's disabled rules are not a list of row ids")
    if not isinstance(consumer, bool):
        raise AccessPolicyUnavailable("an org's consumer-plan choice is not a boolean")
    try:
        pin = regions.pin_of(fields)
    except PinInvalid as invalid:
        raise AccessPolicyUnavailable(f"an org's region pin is not one: {invalid}") from None
    return OrgAccessPolicy(frozenset(cast("list[str]", disabled)), consumer, (pin,) if pin else ())


__all__ = ["policy_from_entry"]
