from __future__ import annotations

import re

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

"""`coding_sandbox_safety`: a PRE_TOOL_USE middleware layer of static,
deterministic checks over `run_code`/`install_packages` input — registered
on `/toolsets/coding_sandbox/**`, following the same pattern as
`hooks/middleware/builtin/tool_safety.py`'s `enforce_tool_safety`.

Explicitly DEFENSE-IN-DEPTH: kernel confinement (`sandbox/confinement.py`)
plus `setrlimit` (`sandbox/coding/executor.py`) are the real security
boundary — file writes scoped to the sandbox dir, network denied during
execution, resource limits capping CPU/memory/processes. This middleware
only catches obvious accidents/abuse cheaply and early, before a sandbox
is even provisioned; it must never be treated as the reason the sandbox is
trusted. `process.exit`/`sys.exit` are deliberately NOT blocked — harmless
inside a confined subprocess.

Concurrency/lifetime limits are NOT this middleware's concern — they live
in `SandboxManager` (need manager state middleware doesn't have). Network
policy is enforced in `CodeExecutor` (network denied during execution
regardless of what the call asks for, unless config permits).
"""

__all__ = ["coding_sandbox_safety"]

_DANGEROUS_CODE_PATTERNS = [
    re.compile(r"rm\s+-[a-z]*r[a-z]*f?\s+/(\s|$)"),          # rm -rf / (and -fr permutations)
    re.compile(r"rm\s+-[a-z]*f[a-z]*r?\s+/(\s|$)"),
    re.compile(r"shutil\.rmtree\(\s*['\"]/['\"]"),            # shutil.rmtree("/")
    re.compile(r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;\s*:"),     # classic shell fork bomb
    re.compile(r"/dev/tcp/"),                                  # bash reverse-shell idiom
    re.compile(r"\bnc\s+-e\s+/bin/(sh|bash)\b"),               # netcat reverse shell
    re.compile(r"stratum\+tcp://"),                            # crypto-mining pool protocol
]

# Package specs that look like arbitrary code fetches rather than a
# registry package name — VCS URLs, local file paths, or any URL scheme.
_DANGEROUS_PACKAGE_SPEC_PATTERNS = [
    re.compile(r"^git\+", re.IGNORECASE),
    re.compile(r"^file:", re.IGNORECASE),
    re.compile(r"://"),
]


def coding_sandbox_safety(
    max_code_size: int = 50_000,
    blocked_patterns: list[str] | None = None,
    allow_url_packages: bool = False,
):
    """PRE_TOOL_USE middleware factory for the coding sandbox toolset.

    Args:
        max_code_size: deny `code` longer than this many characters.
        blocked_patterns: extra regex patterns (in addition to the builtin
            denylist) to deny `code` against — config-driven extensibility.
        allow_url_packages: when True, skip the URL/VCS/local-path package
            spec check (the sandbox's own `EnvironmentManager` package-name
            validator still rejects them independently at install time).
    """
    extra_patterns = [re.compile(p) for p in (blocked_patterns or [])]

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        code = ctx.tool_input.get("code")
        if isinstance(code, str):
            if len(code) > max_code_size:
                ctx.deny(f"code exceeds the maximum allowed size ({max_code_size} characters)")
                return
            for pattern in (*_DANGEROUS_CODE_PATTERNS, *extra_patterns):
                if pattern.search(code):
                    ctx.deny("code matched a denylisted destructive/malicious pattern")
                    return

        if not allow_url_packages:
            packages = ctx.tool_input.get("packages")
            if isinstance(packages, list):
                for spec in packages:
                    if not isinstance(spec, str):
                        continue
                    for pattern in _DANGEROUS_PACKAGE_SPEC_PATTERNS:
                        if pattern.search(spec):
                            ctx.deny(
                                f"package spec {spec!r} looks like a URL/VCS/local-path install, which is not allowed"
                            )
                            return

        await next_fn()

    return _middleware
