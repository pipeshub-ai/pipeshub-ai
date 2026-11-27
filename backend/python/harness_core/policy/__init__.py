"""The generic policy engine (R2 CP3): one dimension table, a closed set of orders and one resolver.

Product-neutral and Loom-embeddable: it imports nothing from ``app``. Node's ``domain/policy`` is the twin.
"""

from harness_core.policy.orders import (
    UNDEFINED,
    InvalidPolicyValue,
    Order,
    Widened,
    order_of,
)
from harness_core.policy.ports import PolicyResolver
from harness_core.policy.registry import (
    ENGINE_PROFILE,
    Dimension,
    DimensionRegistry,
    InvalidDimensionTable,
    policy_registry,
    policy_table,
)
from harness_core.policy.resolver import LIVE, DimensionPolicyResolver, scoped
from harness_core.policy.types import (
    EMPTY_PREFIX,
    FoldedPrefix,
    PolicyRefusal,
    Resolved,
    ScopedLayer,
)

__all__ = [
    "EMPTY_PREFIX",
    "ENGINE_PROFILE",
    "LIVE",
    "UNDEFINED",
    "Dimension",
    "DimensionPolicyResolver",
    "DimensionRegistry",
    "FoldedPrefix",
    "InvalidDimensionTable",
    "InvalidPolicyValue",
    "Order",
    "PolicyRefusal",
    "PolicyResolver",
    "Resolved",
    "ScopedLayer",
    "Widened",
    "order_of",
    "policy_registry",
    "policy_table",
    "scoped",
]
