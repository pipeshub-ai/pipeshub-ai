import ctypes
import logging
import os
import sys
from collections.abc import MutableMapping

logger = logging.getLogger(__name__)

_PR_SET_DUMPABLE = 4

# onnxruntime 1.30 on Linux starts Microsoft's 1DS uploader when its native module loads and posts ProcessInfo
# (executable path, OS, CPU, hashed machine id) to mobile.events.data.microsoft.com on the first session. Only this
# variable, read once at that load, stops it; disable_telemetry_events() still lets ProcessInfo out.
VENDOR_TELEMETRY_OPT_OUTS: dict[str, str] = {"ORT_DISABLE_TELEMETRY": "1"}


def disable_vendor_telemetry(environ: MutableMapping[str, str] = os.environ) -> None:
    """Opt this process and its children out of third-party telemetry, keeping an operator's explicit value."""
    for name, value in VENDOR_TELEMETRY_OPT_OUTS.items():
        _ = environ.setdefault(name, value)


def mark_process_non_dumpable() -> bool:
    """Best-effort ``prctl(PR_SET_DUMPABLE, 0)`` on Linux.

    Processes run by this service (STDIO MCP servers, tools) share its uid, and a same-uid
    process can read ``/proc/<pid>/environ`` — every DB password and the JWT secret — or
    ptrace it, unless the target is non-dumpable. Side effects: no core dumps, and same-uid
    debuggers such as ``py-spy`` can no longer attach. Reset by ``execve``, so call it in the
    process that serves requests.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.warning("prctl(PR_SET_DUMPABLE, 0) failed: errno %s", ctypes.get_errno())
            return False
    except (OSError, AttributeError) as e:
        logger.warning("prctl(PR_SET_DUMPABLE, 0) unavailable: %s", e)
        return False
    return True
