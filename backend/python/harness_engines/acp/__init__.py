"""Generic ACP (Agent Client Protocol) driver: any ACP coding agent, described by a signed profile, as an engine."""

from typing import TYPE_CHECKING

from harness_core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from harness_engines.acp.adapter import AcpAdapter, acp_adapter
    from harness_engines.acp.profiles import AcpAgentProfile

# Each name's submodule, imported when the name is first read (R25OPS-FU-1).
_EXPORTS = {
    "AcpAdapter": "adapter",
    "acp_adapter": "adapter",
    "AcpAgentProfile": "profiles",
}
__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)

__all__ = ["AcpAdapter", "AcpAgentProfile", "acp_adapter"]
