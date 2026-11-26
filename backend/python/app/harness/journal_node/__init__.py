"""The durable journal as Node serves it: the Python side of ``/internal/harness``."""

from typing import TYPE_CHECKING

from harness_core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from app.harness.journal_node.client import (
        HARNESS_V1_RETIRED,
        AttemptRoute,
        ClaimRole,
        NodeClaim,
        NodeJournal,
        error_code,
        transition_wire,
    )

# Each name's submodule, imported when the name is first read (R25OPS-FU-1).
_EXPORTS = {
    "HARNESS_V1_RETIRED": "client",
    "AttemptRoute": "client",
    "ClaimRole": "client",
    "NodeClaim": "client",
    "NodeJournal": "client",
    "error_code": "client",
    "transition_wire": "client",
}
__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)

__all__ = [
    "HARNESS_V1_RETIRED",
    "AttemptRoute",
    "ClaimRole",
    "NodeClaim",
    "NodeJournal",
    "error_code",
    "transition_wire",
]
