"""An engine's shell and its allowed websites (WP-63): https through the egress proxy, its authority trusted.

A turn whose run has allowed websites gets, before the engine starts, the proxy's certificate added to a bundle of
the image's own authorities (``trust.TRUST_SCRIPT``) and an environment every common tool reads: ``HTTPS_PROXY``
for curl, git, pip, npm, Python and Node (``NODE_USE_ENV_PROXY``), and the bundle for each of their trust stores.
Only https goes through the proxy this way: the gateways stay on the engine's own plain-http route, and every
allowed website is https. The same for every engine (Claude Code, Codex, opencode, ACP agents): they all run the
shell commands they are asked for with the environment they were started with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.engines.errors import EngineRefusal
from harness_core.engines.git_checkout import run_in
from harness_core.sandbox.egress.trust import (
    PROXY_URL,
    TRUST_SCRIPT_ENV,
    trust_env,
    trusted_command,
)

if TYPE_CHECKING:
    from harness_core.sandbox.ports import SandboxHandle

SITES_TRUST_FAILED: Final = "sites_trust_failed"


class SiteTrustFailed(EngineRefusal):
    reason_code = SITES_TRUST_FAILED


def site_env() -> dict[str, str]:
    """The engine's environment for its allowed websites."""
    trust = {key: value for key, value in trust_env().items() if key != TRUST_SCRIPT_ENV}
    return {
        "HTTPS_PROXY": PROXY_URL,
        "https_proxy": PROXY_URL,
        "NODE_USE_ENV_PROXY": "1",
        "GIT_TERMINAL_PROMPT": "0",
        **trust,
    }


async def install_site_trust(sandbox: SandboxHandle) -> None:
    """Writes the trust bundle in the turn's sandbox, or refuses the turn: an engine that cannot verify the proxy's
    certificate would only fail at each site, or be tempted to turn verification off."""
    code, _, err = await run_in(sandbox, trusted_command(["sh", "-c", "exit 0"]), trust_env())
    if code != 0:
        raise SiteTrustFailed(f"the egress proxy's authority could not be installed: {err[-200:]}")


__all__ = ["SITES_TRUST_FAILED", "SiteTrustFailed", "install_site_trust", "site_env"]
