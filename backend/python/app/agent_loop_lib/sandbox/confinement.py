from __future__ import annotations

import logging
import shutil
import subprocess
import sys

"""Best-effort kernel-level confinement of a whole spawned process tree
(Codex pattern) — Seatbelt (`sandbox-exec`) on macOS, bubblewrap (`bwrap`) on
Linux — layered ON TOP of `LocalSandbox`'s existing subprocess isolation, not
a replacement for it. `confine_command()` degrades gracefully (returns the
command unwrapped, with a logged warning) when neither tool is installed or
the platform is unsupported, so agent-loop keeps working everywhere; it just
isn't hardened everywhere.
"""

logger = logging.getLogger(__name__)

_WARNED_PLATFORMS: set[str] = set()

_seatbelt_probe_result: bool | None = None


def _seatbelt_works() -> bool:
    """Probe whether sandbox-exec actually works on this system. On macOS 14+
    (Sonoma/Sequoia), unprivileged processes get 'Operation not permitted' from
    sandbox_apply even though the binary exists at /usr/bin/sandbox-exec."""
    global _seatbelt_probe_result
    if _seatbelt_probe_result is not None:
        return _seatbelt_probe_result

    if not shutil.which("sandbox-exec"):
        _seatbelt_probe_result = False
        return False

    try:
        result = subprocess.run(
            ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True, timeout=5,
        )
        _seatbelt_probe_result = result.returncode == 0
    except Exception:
        _seatbelt_probe_result = False

    if not _seatbelt_probe_result:
        logger.info(
            "sandbox-exec binary exists but cannot apply profiles on this system "
            "(macOS 14+ restricts sandbox-exec to entitled processes) — "
            "falling back to subprocess isolation without kernel confinement."
        )
    return _seatbelt_probe_result


def confinement_available() -> bool:
    """Whether this process can apply kernel-level confinement on this OS."""
    if sys.platform == "darwin":
        return _seatbelt_works()
    if sys.platform.startswith("linux"):
        return shutil.which("bwrap") is not None
    return False


def confine_command(cmd: list[str], working_dir: str, allow_network: bool = False) -> list[str]:
    """Wrap `cmd` for kernel-level confinement when available; otherwise
    return it unchanged (warning once per platform string)."""
    if sys.platform == "darwin" and _seatbelt_works():
        return _seatbelt_wrap(cmd, working_dir, allow_network)
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return _bubblewrap_wrap(cmd, working_dir, allow_network)

    if sys.platform not in _WARNED_PLATFORMS:
        _WARNED_PLATFORMS.add(sys.platform)
        logger.warning(
            "No OS-level sandbox confinement available on this platform (%s) — "
            "OS sandbox commands run with subprocess isolation only, no kernel "
            "confinement.", sys.platform,
        )
    return cmd


def _seatbelt_wrap(cmd: list[str], working_dir: str, allow_network: bool) -> list[str]:
    """macOS Seatbelt: deny-by-default profile allowing exec/read everywhere,
    write ONLY under `working_dir` — the sandboxed command's scratch space IS
    its working directory, deliberately not the shared system temp dir, so
    this is a real confinement guarantee rather than one weakened by a broad
    allowance every process on the machine shares. Network only if
    `allow_network`. Passed inline via `-p` so no profile file needs cleanup.
    """
    # Local Unix-domain sockets are process-local IPC (e.g. `tsx`'s loader
    # hook, Python multiprocessing, many DB drivers talking to a local
    # daemon) — they can't reach outside this machine, so they're always
    # allowed even with allow_network=False. Real network (TCP/UDP/IP)
    # stays gated on the flag. Without this carve-out, tools using local
    # sockets fail with a confusing EPERM on `listen()`/`connect()` that
    # looks like a bug rather than an intentional network deny.
    network_clause = (
        "(allow network*)" if allow_network
        else "(deny network*)(allow network* (local unix))"
    )
    profile = (
        "(version 1)"
        "(deny default)"
        "(allow process-fork)"
        "(allow process-exec)"
        "(allow file-read*)"
        f'(allow file-write* (subpath "{working_dir}"))'
        "(allow sysctl-read)"
        # Needed for TLS certificate validation (pip/npm HTTPS installs
        # verify against the system trust store via `trustd` over Mach
        # IPC) and DNS resolution (`mDNSResponder`) — without this,
        # network* being allowed doesn't actually make HTTPS work; it
        # fails deep inside the TLS handshake with a cryptic
        # `SSLCertVerificationError('OSStatus -26276')` that looks
        # unrelated to sandboxing. Mach lookups are service-name based,
        # not filesystem/network access, so this doesn't weaken the
        # file-write/network confinement above.
        "(allow mach-lookup)"
        f"{network_clause}"
    )
    return ["sandbox-exec", "-p", profile, *cmd]


def _bubblewrap_wrap(cmd: list[str], working_dir: str, allow_network: bool) -> list[str]:
    """Linux bubblewrap: read-only bind of the whole filesystem, read-write
    bind of `working_dir` only, private /tmp, all namespaces unshared (no
    network unless `allow_network`), dies with the parent so nothing leaks
    on a kill."""
    args = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--bind", working_dir, working_dir,
        "--tmpfs", "/tmp",
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-all",
        "--die-with-parent",
    ]
    if allow_network:
        args += ["--share-net"]
    return [*args, "--", *cmd]
