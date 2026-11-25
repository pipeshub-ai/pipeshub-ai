import ctypes
import importlib.util
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


def use_bundled_tiktoken_encodings(environ: MutableMapping[str, str] = os.environ) -> None:
    """Point tiktoken at the encodings LiteLLM ships, keeping an operator's explicit ``TIKTOKEN_CACHE_DIR``.

    Without it tiktoken downloads cl100k_base from openaipublic.blob.core.windows.net into $TMPDIR; LiteLLM ≤ 1.97
    set this on import, newer releases only once its token counter loads, so egress-restricted installs fell back
    to estimated token counts.
    """
    if "TIKTOKEN_CACHE_DIR" in environ:
        return
    spec = importlib.util.find_spec("litellm")
    bundled = (
        os.path.join(os.path.dirname(spec.origin), "litellm_core_utils", "tokenizers") if spec and spec.origin else None
    )
    if bundled and os.path.isdir(bundled):
        environ["TIKTOKEN_CACHE_DIR"] = bundled
    else:
        logger.warning(
            "LiteLLM's bundled tiktoken encodings not found; set TIKTOKEN_CACHE_DIR to a folder holding cl100k_base "
            "or tiktoken will download it at first use"
        )


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
