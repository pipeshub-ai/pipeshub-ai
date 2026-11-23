from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import re
import shlex
import shutil
import stat
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent_loop_lib.sandbox.base import SandboxInfo
from app.agent_loop_lib.sandbox.coding import egress_firewall as _egress_firewall
from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    CodingLanguage,
    CodingSandboxBackend,
    CodingSandboxError,
    ErrorAnalysis,
    ErrorCategory,
    InstallResult,
    IsolationLevel,
    SandboxCapabilities,
    SandboxContext,
    normalize_sandbox_path,
    validate_entry_file,
)
from app.agent_loop_lib.sandbox.coding.cleanup import (
    register_sandbox_dir,
    unregister_sandbox_dir,
)
from app.agent_loop_lib.sandbox.coding.docker_client import (
    DockerClientProvider,
    get_default_provider,
)
from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    CONTAINER_HARDENING,
    EgressNetworkUnsafe,
    create_sandbox_container,
    deadline_command,
    firewall_unavailable,
    firewalled_container_kwargs,
    new_firewall_token,
    parse_cidrs,
)
from app.agent_loop_lib.sandbox.coding.output_limits import (
    ExtractBudget,
    SandboxRetention,
    extract_tar_stream,
    read_capped_stream,
)
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine
from app.agent_loop_lib.sandbox.coding.validation import (
    canonical_package_key,
    matches_package_set,
    package_name,
    validate_package_spec,
)
from app.core.org_context import spawn_in_request

"""`DockerCodingSandbox`: a `CodingSandboxBackend` implementation that runs
code inside ephemeral Docker containers instead of as a host subprocess
(`LocalCodingSandbox`) or a remote micro-VM (`E2BCodingSandbox`).

Two-phase execution, ported from PipesHub's own stateless
``app/sandbox/docker_executor.py`` (this backend adopts that module's proven
security model, not just its shape):

- By default, user code runs in its own container with ``network_mode="none"``
  AND ``network_disabled=True`` — no routable networking stack at all. When
  the backend is constructed with ``allow_network=True`` AND the individual
  ``CodeRequest.allow_network`` is also set, the run container instead joins
  the SAME dedicated egress bridge used for package installs (below) —
  public internet access for the sandboxed code. The bridge filters nothing
  by address, so every container that joins it installs its own egress
  rules first (``egress_firewall``: private, CGNAT, link-local/metadata and
  bridge addresses are rejected) and then drops to an unprivileged user with
  no capabilities. If the image can't install the rules, the run is retried
  with no network rather than on an unfiltered bridge.
- Packages are installed in a SEPARATE, short-lived container attached to a
  dedicated egress network (outbound internet for pip/npm only) — never the
  caller's default Docker network, so this backend can be dropped into a
  compose deployment without exposing sibling services to sandboxed code.
- No host-path volume mounts anywhere: every file transfer in or out of a
  container goes through ``put_archive``/``get_archive``, so this also works
  under Docker-in-Docker (sibling container) deployments where the host path
  doesn't exist on the daemon's own filesystem.

Persistence model (the state contract `CodingSandboxBackend` requires):
installed dependencies and files written to the sandbox's working directory
persist on the HOST, across `execute()`/`install_packages()` calls on the
same instance, and are re-archived into a fresh container on every call —
including files that only ever reached the HOST side via `upload_file()`
(staged `input_artifacts`, skill resources) rather than a prior `execute()`,
which `_collect_working_dir_inputs()`/`_tar_files()` fold into the same
`/src` archive the entry file gets, so they're visible at the program's cwd
under their staged relative path, not just present on the host and
invisible to the container; interpreter state never persists, since every
call is a brand new container.

This module has no PipesHub-specific imports or literals — image name,
egress network, and registry URLs are all constructor arguments with
neutral defaults, supplied by the adapter layer that wires this backend up.
"""

__all__ = ["DockerCodingSandbox"]

logger = logging.getLogger(__name__)

_ENTRY_FILES: dict[CodingLanguage, str] = {"typescript": "main.ts", "python": "main.py"}
# `npx tsx` (not the local sandbox's bootstrap-and-run-a-binary approach):
# the sandbox image is expected to ship a global Node/npx/tsx toolchain, so
# there is no per-sandbox TypeScript runtime bootstrap step here — matching
# the existing `docker_executor.py`'s behavior exactly.
_RUN_COMMANDS: dict[CodingLanguage, str] = {
    "typescript": "npx tsx {file}",
    "python": "python3 {file}",
}
_LISTING_IGNORED_DIRS = {"node_modules", "deps_python", "__pycache__", ".git"}
# Top-level working-dir subdirectories that are archived into the container
# through their OWN dedicated `put_archive` call (`_src` -> `/src`'s entry
# file, `output` -> `/output`, `deps_python`/`node_modules` -> `/deps` /
# `/node_modules`) — never re-included in the generic "everything else in
# the working dir" sweep below, or they'd be double-archived / land at the
# wrong container path.
_RESERVED_WORKING_DIR_SUBDIRS = frozenset({"_src", "output", "deps_python", "node_modules"})


def _snapshot_mtimes(root: str) -> dict[str, float]:
    """Cheap before/after diff basis, `{relative_path: mtime}` — mirrors
    `LocalCodingSandbox`'s own `_snapshot_mtimes` in `executor.py`. Needed
    here because `output/` is a HOST directory that persists and MERGES
    across calls on the same sandbox instance (`_extract_container_dir`
    never clears it — see `execute()`), so without a diff, call 2 would
    re-list (and the bridge layer would re-download/re-register) every file
    call 1 already delivered."""
    snapshot: dict[str, float] = {}
    if not os.path.isdir(root):
        return snapshot
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                snapshot[os.path.relpath(full, root)] = os.path.getmtime(full)
            except OSError:
                continue
    return snapshot

_NETWORK_UNAVAILABLE_NOTE = (
    "[sandbox] Network access is unavailable for this run: the egress "
    "firewall could not be installed, so the code ran with no network.\n"
)
_INSTALL_FIREWALL_UNAVAILABLE = (
    "[sandbox] Package install refused: the egress firewall could not be "
    "installed for the install container ({reason}). Packages are never "
    "installed on an unfiltered network; use a sandbox image that includes "
    "iptables (deployment/sandbox/Dockerfile).\n"
)


_OOM_NOTE = (
    "[sandbox] The program was killed because it ran out of memory: either it went "
    "over this sandbox's {limit} MB per run, or the server itself was short of memory. "
    "Use less memory (process data in chunks, smaller types) and try again.\n"
)
_INSTALL_TIMEOUT_S = 300
_INSTALL_TIMEOUT_NOTE = "[sandbox] Package installation timed out after {seconds}s.\n"
# The host waits this much longer than the in-container deadline, so the
# deadline is the normal way a run ends at its timeout and the host-side kill
# is only the fallback.
_HOST_WAIT_MARGIN_S = 10
_INSTALL_OOM_NOTE = (
    "[sandbox] Package installation ran out of memory (limit {limit} MB). "
    "Install fewer packages at once, or pick lighter ones.\n"
)


# How long a cancelled call waits for its container to be removed and its
# worker thread to finish before it gives up and lets its slot go.
_CANCEL_CLEANUP_S = 30.0


@dataclass
class _RunHandle:
    """Shared between a container run's worker thread and the event loop,
    so a cancel on the loop can reach a container the thread is driving.

    The thread writes `container_id` and then reads `cancelled`; the loop
    sets `cancelled` and then reads `container_id`. Whichever comes second
    sees the other, so the container is removed at least once (a second
    remove gets a 404, which is ignored).
    """

    container_id: str | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


class _RunCancelled(Exception):
    """Raised in the worker thread when its run was cancelled: skip the
    rest of the run and go straight to removing the container."""


@dataclass
class _RunOutcome:
    """What one run container left behind. `oom_killed` and `ran_s` come
    from inspecting the container after it exited; they are False / None
    when the inspect failed or the daemon did not report them."""

    exit_code: int
    stdout: str
    stderr: str
    oom_killed: bool = False
    ran_s: float | None = None
    # The program ran for (about) its whole timeout: the in-container
    # deadline or the host killed it.
    deadline_hit: bool = False
    # The disk watchdog stopped the run: bytes its writable layer had.
    disk_exceeded_bytes: int | None = None
    # The disk watchdog stopped the run because it could not read its size.
    disk_unchecked: str | None = None
    # What collecting its files left out ("" when nothing).
    collection_note: str = ""


def _parse_docker_time(value: object) -> float | None:
    """Epoch seconds from a Docker RFC 3339 timestamp, which carries
    nanoseconds (more digits than `fromisoformat` takes before 3.11). The
    zero time Docker reports for "never" is `None`."""
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _, rest = text.partition(".")
        digits = len(rest) - len(rest.lstrip("0123456789"))
        text = f"{head}.{rest[:min(digits, 6)]}{rest[digits:]}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _container_exit_state(container: Any) -> tuple[bool, float | None]:
    """`(oom_killed, ran_s)` for a container that has exited. Never raises:
    a failed inspect means "not known", and the run is classified from its
    exit code alone."""
    try:
        container.reload()
        attrs = container.attrs if isinstance(container.attrs, dict) else {}
    except Exception:
        logger.debug("inspect after run failed for container %s", getattr(container, "id", "?"), exc_info=True)
        return False, None
    state = attrs.get("State")
    if not isinstance(state, dict):
        return False, None
    started = _parse_docker_time(state.get("StartedAt"))
    finished = _parse_docker_time(state.get("FinishedAt"))
    ran_s = finished - started if started is not None and finished is not None and finished >= started else None
    return state.get("OOMKilled") is True, ran_s


# ------------------------------------------------------------------
# Disk quota fallback
# ------------------------------------------------------------------

_ENV_DISK_WATCHDOG = "SANDBOX_DISK_WATCHDOG"
_ENV_DISK_WATCH_CHECK_TIMEOUT = "SANDBOX_DISK_WATCH_CHECK_TIMEOUT_S"
_ENV_DISK_WATCH_MAX_FAILURES = "SANDBOX_DISK_WATCH_MAX_FAILURES"
_DISK_WATCH_INTERVAL_S = 1.0
_DISK_WATCH_CHECK_TIMEOUT_S = 10.0
_DISK_WATCH_MAX_FAILURES = 3
# Graph drivers that enforce `storage_opt size` whenever they accept it:
# overlay2 refuses the option unless its backing filesystem is xfs with
# pquota. The containerd snapshotters (Driver "overlayfs", or a DriverStatus
# naming io.containerd.snapshotter) accept it and ignore it.
_QUOTA_ENFORCING_DRIVERS = frozenset({"overlay2", "btrfs", "zfs", "devicemapper", "windowsfilter"})
_SIZE_UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4, "p": 1024**5}
_DISK_QUOTA_NOTE = (
    "[sandbox] The run was stopped because it wrote more than {limit} to the sandbox's "
    "disk. Write less data (process it in chunks, or keep only what is needed).\n"
)
_DISK_UNCHECKED_NOTE = (
    "[sandbox] {what} was stopped because the server could not check this run's disk use. "
    "This happens when a run creates a very large number of files, or when the server is "
    "overloaded. Create fewer files; if the code is small, try again.\n"
)
_INSTALL_DISK_QUOTA_NOTE = (
    "[sandbox] Package installation was stopped because it wrote more than {limit} to the "
    "sandbox's disk. Install fewer or smaller packages.\n"
)

_disk_watch_lock = threading.Lock()
# None = not decided yet; otherwise the byte limit to watch for (0 = no watch).
_disk_watch_limit: int | None = None


def _parse_size(text: str) -> int | None:
    """Bytes in a Docker size string (``10g``, ``512m``, ``1.5GB``; binary
    units, as the daemon reads them), or None when it is not one."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)(?:i?b)?\s*", text.lower())
    if not m:
        return None
    return int(float(m.group(1)) * _SIZE_UNITS[m.group(2)])


def _daemon_enforces_quota(client: Any) -> tuple[bool, str]:
    """Whether the daemon behind ``client`` enforces ``storage_opt size``,
    and the storage driver it named. Unknown counts as not enforced."""
    if _egress_firewall._storage_opt_usable is not True:
        return False, "rejected the option"
    try:
        info = client.info()
    except Exception as exc:
        return False, f"unknown ({exc})"
    if not isinstance(info, dict):
        return False, "unknown"
    driver = str(info.get("Driver") or "")
    status = info.get("DriverStatus")
    status_map: dict[str, str] = {}
    if isinstance(status, list):
        for pair in status:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                status_map[str(pair[0]).lower()] = str(pair[1])
    if "io.containerd.snapshotter" in status_map.get("driver-type", ""):
        return False, f"{driver or '?'} (containerd snapshotter)"
    backing = status_map.get("backing filesystem")
    label = f"{driver or '?'}{f' on {backing}' if backing else ''}"
    if not status_map:
        # No driver status (an older proxy filters it out): the backing
        # filesystem can't be checked, so count the quota as not enforced.
        return False, f"{driver or '?'} (driver status unknown)"
    if driver == "overlay2" and (backing or "").lower() != "xfs":
        # overlay2 enforces a size only on xfs mounted with pquota.
        return False, label
    return driver in _QUOTA_ENFORCING_DRIVERS, label


def _disk_watch_limit_for(client: Any) -> int:
    """Bytes a run's writable layer may reach before the watchdog stops it,
    or 0 for no watchdog. Decided once per process, after the first
    container create has shown whether the daemon accepts the quota.

    ``SANDBOX_DISK_WATCHDOG``: ``auto`` (default) watches only when the
    daemon does not enforce ``SANDBOX_DISK_QUOTA``; ``on`` always watches;
    ``off`` never does.
    """
    global _disk_watch_limit
    if _disk_watch_limit is not None:
        return _disk_watch_limit
    with _disk_watch_lock:
        if _disk_watch_limit is not None:
            return _disk_watch_limit
        mode = (os.environ.get(_ENV_DISK_WATCHDOG) or "auto").strip().lower()
        quota = _egress_firewall._disk_quota()
        limit = _parse_size(quota) if quota else None
        if quota and limit is None:
            logger.error(
                "SANDBOX_DISK_QUOTA=%r is not a size; sandbox runs are not watched for disk use",
                quota,
            )
        if not limit or mode in {"off", "false", "0", "no"}:
            _disk_watch_limit = 0
            return 0
        if mode in {"on", "true", "1", "yes"}:
            _disk_watch_limit = limit
            logger.info("Sandbox disk watchdog on: runs are stopped past %s written", quota)
            return limit
        enforced, driver = _daemon_enforces_quota(client)
        if enforced:
            _disk_watch_limit = 0
            logger.info(
                "Sandbox disk quota SANDBOX_DISK_QUOTA=%s is enforced by the Docker daemon "
                "(storage driver %s)", quota, driver,
            )
            return 0
        _disk_watch_limit = limit
        logger.warning(
            "The Docker daemon does not enforce the sandbox disk quota SANDBOX_DISK_QUOTA=%s "
            "(storage driver %s). Each run's writable layer is checked every %gs instead and "
            "the run is stopped once it is past the quota; a fast writer can go somewhat past "
            "it between checks. For a hard limit, run the daemon on overlay2 over xfs with "
            "the pquota mount option.", quota, driver, _DISK_WATCH_INTERVAL_S,
        )
        return limit


def _env_seconds(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        logger.warning("Ignoring %s=%r: use a positive number of seconds; using %g", name, raw, default)
        return default
    return value


def _env_count(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        logger.warning("Ignoring %s=%r: use a whole number of at least 1; using %d", name, raw, default)
        return default
    return value


def _container_size_rw(client: Any, container_id: str, timeout: float | None = None) -> int | None:
    """``SizeRw`` of a container (bytes in its writable layer), or None
    when the daemon could not measure it (it reports -1 then)."""
    api = client.api
    kwargs: dict[str, Any] = {"params": {"size": 1}}
    if timeout is not None:
        kwargs["timeout"] = timeout
    res = api._get(api._url("/containers/{0}/json", container_id), **kwargs)
    data = api._result(res, True)
    size = data.get("SizeRw") if isinstance(data, dict) else None
    return size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None


class _DiskWatch:
    """Polls a running container's writable-layer size and kills the
    container once it is past ``limit``. Runs on its own daemon thread next
    to the blocking ``container.wait``.

    It fails closed. A run whose disk use cannot be read is stopped too:

    - A check that fails, or answers without a size, is a strike;
      ``SANDBOX_DISK_WATCH_MAX_FAILURES`` (3) strikes in a row stop the run.
    - A check that takes longer than ``SANDBOX_DISK_WATCH_CHECK_TIMEOUT_S``
      (10) stops the run at once. Each check runs on its own thread, so a
      client that hangs cannot hold the watch up. The daemon finds the size
      by walking the writable layer, so a slow check is also the sign of a
      run that made a huge number of files: the byte count cannot see
      inodes being used up, and this is the cheap signal that can.
    """

    def __init__(self, client: Any, container_id: str, limit: int) -> None:
        self._client = client
        self._id = container_id
        self._limit = limit
        self._stop = threading.Event()
        self.exceeded_bytes: int | None = None
        # Why the run was stopped without its size being known, or None.
        self.unchecked_reason: str | None = None
        self._check_timeout = _env_seconds(_ENV_DISK_WATCH_CHECK_TIMEOUT, _DISK_WATCH_CHECK_TIMEOUT_S)
        self._max_failures = _env_count(_ENV_DISK_WATCH_MAX_FAILURES, _DISK_WATCH_MAX_FAILURES)
        self._thread = threading.Thread(target=self._run, name="sandbox-disk-watch", daemon=True)

    @classmethod
    def start(cls, client: Any, container_id: str, limit: int) -> _DiskWatch | None:
        if limit <= 0:
            return None
        watch = cls(client, container_id, limit)
        watch._thread.start()
        return watch

    @property
    def stopped_run(self) -> bool:
        return self.exceeded_bytes is not None or self.unchecked_reason is not None

    def _check(self) -> tuple[str, int | None, str]:
        """One size read, bounded by the check timeout:
        ``("ok", size, "")``, ``("error", None, why)`` or ``("slow", None, why)``."""
        box: dict[str, Any] = {}

        def _read() -> None:
            try:
                box["size"] = _container_size_rw(self._client, self._id, timeout=self._check_timeout)
            except Exception as exc:
                box["error"] = exc

        started = time.monotonic()
        reader = threading.Thread(target=_read, name="sandbox-disk-check", daemon=True)
        reader.start()
        reader.join(self._check_timeout)
        elapsed = time.monotonic() - started
        if reader.is_alive() or elapsed >= self._check_timeout:
            return "slow", None, f"a size check took longer than {self._check_timeout:g}s"
        if "error" in box:
            exc = box["error"]
            if _is_timeout(exc):
                return "slow", None, f"a size check took longer than {self._check_timeout:g}s ({exc})"
            return "error", None, str(exc) or type(exc).__name__
        size = box.get("size")
        if size is None:
            return "error", None, "the daemon did not report the size"
        return "ok", size, ""

    def _kill(self) -> None:
        """Stop the container, retrying until it is stopped or the run ends:
        a failed kill must not leave an over-quota run writing. A kill that
        fails is followed by a forced removal."""
        while True:
            for stop_it in (
                lambda: self._client.api.kill(self._id),
                lambda: self._client.api.remove_container(self._id, force=True),
            ):
                try:
                    stop_it()
                    return
                except Exception as exc:
                    if _container_already_stopped(exc):
                        return
                    logger.debug("stopping %.12s for the disk watch failed: %s", self._id, exc)
            if self._stop.wait(_DISK_WATCH_INTERVAL_S):
                return

    def _run(self) -> None:
        strikes = 0
        while not self._stop.wait(_DISK_WATCH_INTERVAL_S):
            status, size, why = self._check()
            if self._stop.is_set():
                return
            if status == "slow":
                self.unchecked_reason = why
                logger.warning(
                    "sandbox disk watch: stopping %.12s, its disk use could not be checked: %s",
                    self._id, why,
                )
                self._kill()
                return
            if status == "error":
                strikes += 1
                if strikes >= self._max_failures:
                    self.unchecked_reason = f"{strikes} size checks in a row failed ({why})"
                    logger.warning(
                        "sandbox disk watch: stopping %.12s, its disk use could not be checked: %s",
                        self._id, self.unchecked_reason,
                    )
                    self._kill()
                    return
                continue
            strikes = 0
            if size is not None and size > self._limit:
                self.exceeded_bytes = size
                logger.warning(
                    "sandbox container %.12s wrote %d bytes, past the %d-byte disk quota; stopping it",
                    self._id, size, self._limit,
                )
                self._kill()
                return

    def stop(self) -> None:
        self._stop.set()
        # A check in flight is abandoned, not waited for: the run is over.
        self._thread.join(timeout=1)


def _container_already_stopped(exc: BaseException) -> bool:
    """A kill or removal refused because the container is not running or gone."""
    status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
    return status in (404, 409)


def _is_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()


def disk_watch_note(watch: _DiskWatch | None, *, install: bool = False) -> str:
    """The message for a run (or install) the disk watch stopped, or ""."""
    if watch is None:
        return ""
    if watch.exceeded_bytes is not None:
        note = _INSTALL_DISK_QUOTA_NOTE if install else _DISK_QUOTA_NOTE
        return note.format(limit=_egress_firewall._disk_quota() or "its quota")
    if watch.unchecked_reason is not None:
        return _DISK_UNCHECKED_NOTE.format(what="Package installation" if install else "The run")
    return ""


def disk_watch_limit_for(client: Any) -> int:
    """Public name of `_disk_watch_limit_for`, for the other Docker runner."""
    return _disk_watch_limit_for(client)


def start_disk_watch(client: Any, container_id: str) -> _DiskWatch | None:
    """Start watching a just-started container when this daemon needs it."""
    return _DiskWatch.start(client, container_id, _disk_watch_limit_for(client))


# ------------------------------------------------------------------
# Container logs
# ------------------------------------------------------------------

_ENV_LOG_MAX_SIZE = "SANDBOX_LOG_MAX_SIZE"
_ENV_LOG_MAX_FILES = "SANDBOX_LOG_MAX_FILES"
_DEFAULT_LOG_MAX_SIZE = "10m"
_DEFAULT_LOG_MAX_FILES = 3
_LOG_DRIVER = "local"
_log_lock = threading.Lock()
# 0 = driver and rotation options, 1 = driver only (its own rotation
# defaults), 2 = nothing (the daemon's default driver). Only goes up.
_log_config_level = 0


def _bounded_log_config(level: int) -> dict[str, Any] | None:
    if level >= 2:
        return None
    if level == 1:
        return {"type": _LOG_DRIVER, "config": {}}
    size_raw = (os.environ.get(_ENV_LOG_MAX_SIZE) or _DEFAULT_LOG_MAX_SIZE).strip()
    if not _parse_size(size_raw):
        logger.warning("Ignoring %s=%r: not a size; using %s", _ENV_LOG_MAX_SIZE, size_raw, _DEFAULT_LOG_MAX_SIZE)
        size_raw = _DEFAULT_LOG_MAX_SIZE
    files = _env_count(_ENV_LOG_MAX_FILES, _DEFAULT_LOG_MAX_FILES)
    return {"type": _LOG_DRIVER, "config": {"max-size": size_raw, "max-file": str(files)}}


def _is_log_config_refused(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in ("logconfig", "log driver", "logging driver", "log opt", "log-opt"))


def create_bounded_container(client: Any, **kwargs: Any) -> Any:
    """`create_sandbox_container` with the container's log kept bounded on
    the daemon's disk: the ``local`` driver, rotated at
    ``SANDBOX_LOG_MAX_SIZE`` (10m) × ``SANDBOX_LOG_MAX_FILES`` (3).

    Without it the daemon's default driver (usually ``json-file``, never
    rotated) keeps everything a run prints, outside the run's disk quota.
    The app only ever reads the head and tail, so nothing it shows is lost.
    When the daemon or a Docker proxy refuses the rotation options, the
    container is created with the driver alone (``local`` rotates by
    default), and when it refuses that too, without either; each step is
    logged once and remembered for the process.
    """
    global _log_config_level
    while True:
        level = _log_config_level
        log_config = _bounded_log_config(level)
        try:
            if log_config is None:
                return create_sandbox_container(client, **kwargs)
            return create_sandbox_container(client, log_config=log_config, **kwargs)
        except Exception as exc:
            if log_config is None or not _is_log_config_refused(exc):
                raise
            with _log_lock:
                if _log_config_level == level:
                    _log_config_level = level + 1
                    if level == 0:
                        logger.info(
                            "sandbox container log rotation options were refused (%s); using the "
                            "%r log driver with its own rotation", exc, _LOG_DRIVER,
                        )
                    else:
                        logger.warning(
                            "the %r log driver was refused for sandbox containers (%s); their "
                            "output goes to the daemon's default log driver, which may not "
                            "rotate", _LOG_DRIVER, exc,
                        )


async def _wait_until(aw: asyncio.Future[Any], deadline: float) -> bool:
    """Wait for *aw* until *deadline* (loop time) without cancelling it.
    Returns whether the caller was cancelled meanwhile; the wait goes on."""
    loop = asyncio.get_running_loop()
    cancelled = False
    while not aw.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            await asyncio.wait({aw}, timeout=remaining)
        except asyncio.CancelledError:
            cancelled = True
    return cancelled


class DockerCodingSandbox(CodingSandboxBackend):
    """One sandbox instance = one host working directory + a Docker image
    used to run every `execute()`/`install_packages()` call in a fresh,
    isolated container."""


    backend_name = "docker"

    def __init__(
        self,
        *,
        image: str = "agent-loop-sandbox:latest",
        working_dir: str | None = None,
        memory_limit_mb: int = 512,
        cpu_limit: float = 0.5,
        egress_network: str = "sandbox_egress",
        network_disabled: bool = True,
        allow_network: bool = False,
        pip_index_url: str = "https://pypi.org/simple",
        npm_registry: str = "https://registry.npmjs.org",
        package_allowlist: list[str] | None = None,
        package_denylist: list[str] | None = None,
        image_node_modules: str | None = None,
        egress_allow_cidrs: tuple[str, ...] = (),
        sandbox_user: str = "sandbox",
        context: SandboxContext | None = None,
        provider: "DockerClientProvider | None" = None,
    ) -> None:
        self._sandbox_id = str(uuid.uuid4())
        self._context = context
        # One shared client and one bounded executor per process. Creating
        # a client per call re-handshakes with the daemon every time, and
        # running the blocking docker-py calls on asyncio's DEFAULT
        # executor lets a few concurrent container runs starve every other
        # subsystem that shares it.
        self._provider = provider or get_default_provider()
        short_suffix = self._sandbox_id.replace("-", "")[:10]
        self._working_dir = os.path.realpath(
            working_dir or os.path.join(tempfile.gettempdir(), f"alcs-docker-{short_suffix}")
        )
        self._image = image
        self._memory_limit_mb = memory_limit_mb
        self._cpu_limit = cpu_limit
        self._egress_network = egress_network
        self._network_disabled = network_disabled
        self._allow_network = allow_network
        self._pip_index_url = pip_index_url
        self._npm_registry = npm_registry
        self._allowlist = set(package_allowlist) if package_allowlist else None
        self._denylist = set(package_denylist or [])
        self._image_node_modules = image_node_modules
        self._egress_allow_cidrs = parse_cidrs(egress_allow_cidrs)
        self._sandbox_user = sandbox_user
        self._installed: dict[str, set[str]] = {"typescript": set(), "python": set()}
        self._reflection = ReflectionEngine(
            memory_limit_mb=memory_limit_mb, process_limit=CONTAINER_HARDENING["pids_limit"],
        )
        self._provisioned = False
        # Bytes and files this sandbox has kept from its runs (see
        # `output_limits.SandboxRetention`). Read from the output dir at the
        # first run, so a sandbox reopened over an existing dir starts from
        # what is there. The lock serialises collection of parallel runs.
        self._kept: tuple[int, int] | None = None
        # Bytes charged so far for each working-dir file collected from
        # `/src`, by path. The working dir also holds uploads that were
        # never charged, so its sizes on disk cannot say what to give back
        # when a run shrinks a file.
        self._src_charged: dict[str, int] = {}
        self._collect_lock = threading.Lock()

    # ------------------------------------------------------------------
    # CodingSandboxBackend contract
    # ------------------------------------------------------------------

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    @classmethod
    def describe_capabilities(cls, *, allow_network: bool = False) -> SandboxCapabilities:
        """See `LocalCodingSandbox.describe_capabilities`."""
        return SandboxCapabilities(
            isolation=IsolationLevel.CONTAINER,
            supports_network=allow_network,
            supports_streaming=False,
            supports_reconnect=False,
            is_metered=False,
            persistent_filesystem=True,
        )

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self.describe_capabilities(allow_network=self._allow_network)

    @property
    def working_dir(self) -> str:
        return self._working_dir

    async def provision(self) -> SandboxInfo:
        if not self._provisioned:
            _make_private_dir(self._working_dir)
            os.makedirs(self._output_dir, exist_ok=True)
            register_sandbox_dir(self._working_dir)
            self._provisioned = True
            self._created_at = time.time()
        return SandboxInfo(
            sandbox_id=self._sandbox_id,
            status="ready",
            metadata={"backend": "docker", "image": self._image, "working_dir": self._working_dir},
        )

    async def execute(self, request: CodeRequest) -> CodeResult:
        if not self._provisioned:
            await self.provision()

        if request.packages:
            install_result = await self.install_packages(request.packages, request.language)
            if not install_result.success:
                return CodeResult(
                    stdout="",
                    stderr=install_result.stderr,
                    exit_code=1,
                    language=request.language,
                    duration_ms=0.0,
                    error_analysis=ErrorAnalysis(
                        category=ErrorCategory.IMPORT,
                        message=install_result.stderr or "package installation failed",
                        suggestion=(
                            "Fix the package spec, or call install_packages directly to see "
                            "the full installer output."
                        ),
                        is_retryable=True,
                    ),
                )

        start = time.monotonic()
        entry = validate_entry_file(request.entry_file or _ENTRY_FILES[request.language])
        src_dir = os.path.join(self._working_dir, "_src")
        shutil.rmtree(src_dir, ignore_errors=True)
        os.makedirs(src_dir, exist_ok=True)
        entry_path = os.path.join(src_dir, entry)
        os.makedirs(os.path.dirname(entry_path), exist_ok=True)
        with open(entry_path, "w", encoding="utf-8") as f:
            f.write(request.code)

        run_cmd = deadline_command(
            ["sh", "-c", _RUN_COMMANDS[request.language].format(file=shlex.quote(entry))],
            math.ceil(request.timeout),
        )

        # Everything `upload_file()` has ever staged into the working dir
        # (input artifacts from `input_artifacts`, skill resources, files
        # promoted from a PRIOR run's cwd — see `_promote_src_artifacts`)
        # lived on the HOST only until now: the run container previously
        # got just the entry file, never this. Read once, up front, so the
        # exact same bytes are both what gets archived into `/src` below
        # AND the baseline `_promote_src_artifacts` diffs against after the
        # run, instead of re-reading (and risking a second, possibly
        # racy) read off disk.
        staged_inputs = _scan_working_dir_inputs(self._working_dir)
        # Captured BEFORE this call's container runs (and therefore before
        # `_run_container_sync` merges a fresh /output extraction on top of
        # whatever a PRIOR call on this same sandbox already left there) —
        # see `_snapshot_mtimes`'s docstring.
        output_before = _snapshot_mtimes(self._output_dir)
        logger.info(
            "DockerCodingSandbox.execute: sandbox=%s language=%s packages=%s "
            "entry=%s working_dir=%s staged_inputs=%d (%s) "
            "total_staged_bytes=%d allow_network=%s image=%s",
            self._sandbox_id, request.language, request.packages,
            entry, self._working_dir, len(staged_inputs),
            sorted(staged_inputs.keys())[:20],
            sum(f.size for f in staged_inputs.values()),
            self._allow_network and request.allow_network,
            self._image,
        )

        network_enabled = self._allow_network and request.allow_network
        # Resolved here rather than inside the blocking helper: both are
        # async, both are cached per process, and doing them once up front
        # keeps the image pull out of the per-run critical path.
        await self._ensure_image()
        egress_network = None
        egress_cidrs: list[str] = []
        network_note = ""
        if network_enabled:
            egress_network = self._egress_network
            try:
                egress_cidrs = await self._provider.egress_network_cidrs(egress_network)
                if not egress_cidrs:
                    logger.error("egress network %s has no readable IPv4 subnet", egress_network)
            except EgressNetworkUnsafe as exc:
                # The run goes offline; an install on that network still refuses.
                logger.error("%s", exc)
            if not egress_cidrs:
                network_enabled, egress_network = False, None
                network_note = _NETWORK_UNAVAILABLE_NOTE
        handle = _RunHandle()
        fut = spawn_in_request(self._provider.run_blocking(
            self._run_container_sync,
            run_cmd, src_dir, request.timeout, network_enabled,
            staged_inputs, self._provider.client, egress_network,
            egress_cidrs, handle,
        ))
        try:
            # Shielded: a cancel or the host-side timeout must not abandon the
            # worker thread, which still owns a running container.
            outcome = await asyncio.wait_for(asyncio.shield(fut), timeout=request.timeout + 30)
        except asyncio.CancelledError:
            # Never turn a cancel into a result. The caller's slot is released
            # when this raises, so the container has to be gone first.
            await self._stop_run(handle, fut)
            raise
        except asyncio.TimeoutError:
            await self._stop_run(handle, fut)
            duration_ms = (time.monotonic() - start) * 1000
            result = CodeResult(
                stdout="",
                stderr=f"Execution timed out after {request.timeout}s",
                exit_code=-1,
                language=request.language,
                duration_ms=duration_ms,
            )
            return result.model_copy(update={"error_analysis": self._reflection.analyze(result)})
        except CodingSandboxError:
            raise
        except Exception as exc:
            logger.exception("DockerCodingSandbox.execute failed")
            duration_ms = (time.monotonic() - start) * 1000
            return CodeResult(
                stdout="", stderr=str(exc), exit_code=-1,
                language=request.language, duration_ms=duration_ms,
            )

        duration_ms = (time.monotonic() - start) * 1000
        artifacts = self._list_output_artifacts(output_before)
        artifacts.extend(self._promote_src_artifacts(src_dir, entry, staged_inputs))
        artifacts.sort()
        return self._classify(outcome, request, duration_ms, artifacts, network_note)

    def _classify(
        self, outcome: _RunOutcome, request: CodeRequest, duration_ms: float,
        artifacts: list[str], network_note: str,
    ) -> CodeResult:
        """The `CodeResult` for a finished run container.

        A run that lasted its whole timeout is a timeout, checked first: the
        in-container deadline kills with SIGKILL, so its 137 looks like any
        other kill, and it never sets the OOM flag. The kernel's OOM flag is
        checked next, because a 137 exit alone cannot tell an out-of-memory
        kill from any other SIGKILL. When the flag is set but the program
        still exited 0 (a child process was killed and the parent carried
        on), the run stays a success and only gets the note.
        """
        stderr = network_note + outcome.collection_note + outcome.stderr
        exit_code = outcome.exit_code
        if outcome.disk_exceeded_bytes is not None:
            quota = _egress_firewall._disk_quota() or "its quota"
            stderr = _DISK_QUOTA_NOTE.format(limit=quota) + stderr
            result = CodeResult(
                stdout=outcome.stdout, stderr=stderr, exit_code=exit_code if exit_code else 137,
                language=request.language, duration_ms=duration_ms, artifacts=artifacts,
            )
            return result.model_copy(update={
                "error_analysis": self._reflection.disk_quota_hit(stderr, quota),
            })
        if outcome.disk_unchecked is not None:
            stderr = _DISK_UNCHECKED_NOTE.format(what="The run") + stderr
            result = CodeResult(
                stdout=outcome.stdout, stderr=stderr, exit_code=exit_code if exit_code else 137,
                language=request.language, duration_ms=duration_ms, artifacts=artifacts,
            )
            return result.model_copy(update={"error_analysis": ErrorAnalysis(
                category=ErrorCategory.RUNTIME,
                message="[sandbox] The run was stopped because the server could not check its disk use.",
                suggestion=(
                    "Create fewer files (for example, write one archive or one database file "
                    "instead of many small files). If the code does not create many files, "
                    "try again."
                ),
                stack_trace=stderr or None,
                is_retryable=True,
            )})
        if outcome.deadline_hit and exit_code != 0:
            result = CodeResult(
                stdout=outcome.stdout,
                stderr=f"Execution timed out after {request.timeout}s\n{stderr}".strip(),
                exit_code=-1, language=request.language, duration_ms=duration_ms,
                artifacts=artifacts,
            )
            return result.model_copy(update={"error_analysis": self._reflection.analyze(result)})
        if outcome.oom_killed:
            stderr = _OOM_NOTE.format(limit=self._memory_limit_mb) + stderr
            if exit_code != 0:
                exit_code = 137
        result = CodeResult(
            stdout=outcome.stdout, stderr=stderr, exit_code=exit_code,
            language=request.language, duration_ms=duration_ms, artifacts=artifacts,
        )
        if result.success:
            return result
        if outcome.oom_killed:
            analysis = self._reflection.out_of_memory(outcome.stderr, certain=True)
        else:
            analysis = self._reflection.analyze(result)
        return result.model_copy(update={"error_analysis": analysis})

    async def install_packages(self, packages: list[str], language: CodingLanguage) -> InstallResult:
        if not self._provisioned:
            await self.provision()
        if not packages:
            return InstallResult(success=True, installed=[])

        to_install: list[str] = []
        for spec in packages:
            name = package_name(spec, language)
            if not validate_package_spec(spec, language):
                return InstallResult(success=False, stderr=f"invalid or unsafe package spec: {spec!r}")
            if self._denylist and matches_package_set(name, self._denylist, language):
                return InstallResult(success=False, stderr=f"package {name!r} is denylisted")
            if self._allowlist is not None and not matches_package_set(name, self._allowlist, language):
                return InstallResult(success=False, stderr=f"package {name!r} is not in the configured allowlist")
            if canonical_package_key(name, language) not in self._installed[language]:
                to_install.append(spec)

        if not to_install:
            return InstallResult(success=True, installed=[])

        await self._ensure_image()
        network_name = self._egress_network
        try:
            egress_cidrs = await self._provider.egress_network_cidrs(network_name)
            handle = _RunHandle()
            fut = spawn_in_request(self._provider.run_blocking(
                self._install_packages_sync, to_install, language,
                self._provider.client, network_name, egress_cidrs, handle,
            ))
            try:
                success, stdout, stderr = await asyncio.shield(fut)
            except asyncio.CancelledError:
                await self._stop_run(handle, fut)
                raise
        except CodingSandboxError:
            raise
        except Exception as exc:
            logger.exception("DockerCodingSandbox.install_packages failed")
            return InstallResult(success=False, stderr=str(exc))

        if success:
            for spec in to_install:
                self._installed[language].add(canonical_package_key(package_name(spec, language), language))
        return InstallResult(
            success=success,
            installed=[package_name(s, language) for s in to_install] if success else [],
            stdout=stdout,
            stderr=stderr,
        )

    async def _stop_run(self, handle: _RunHandle, fut: asyncio.Future[Any]) -> None:
        """Stop a run whose caller was cancelled or timed out, and wait for
        its worker thread to finish, at most `_CANCEL_CLEANUP_S`.

        Force-remove kills and removes the container in one call, so the
        thread's `container.wait` returns at once and the thread is freed
        too. It runs on the control pool, because the run pool is full
        exactly when something has to be stopped. A second cancel during
        the wait is remembered and re-raised at the end, so it cannot cut
        the wait short and release a slot while the container still runs.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        handle.cancelled.set()
        # Retrieved here so an abandoned run's error is not logged as
        # "never retrieved" when it finally ends.
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        cancelled_again = False
        deadline = started + _CANCEL_CLEANUP_S
        container_id = handle.container_id
        if container_id:
            # The removal (and its wait for a control thread) counts against
            # the same bound as the worker thread.
            removal = spawn_in_request(self._provider.run_control(
                self._provider.client.api.remove_container, container_id, force=True,
            ))

            def removed(task: asyncio.Future[Any], cid: str = container_id) -> None:
                if not task.cancelled() and task.exception() is not None:
                    logger.debug("removing cancelled sandbox container %.12s failed: %s", cid, task.exception())

            removal.add_done_callback(removed)
            cancelled_again = await _wait_until(removal, deadline)
        cancelled_again = await _wait_until(fut, deadline) or cancelled_again
        if fut.done():
            logger.info(
                "sandbox run cancelled: container %.12s removed after %.1fs",
                handle.container_id or "-", loop.time() - started,
            )
        else:
            logger.warning(
                "sandbox run %.12s did not stop within %gs of cancel; releasing its slot "
                "(the proxy reaper removes the container if it is still there)",
                handle.container_id or "-", _CANCEL_CLEANUP_S,
            )
        if cancelled_again:
            raise asyncio.CancelledError

    async def upload_file(self, path: str, content: bytes) -> None:
        if not self._provisioned:
            await self.provision()
        full_path = self._resolve_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
        logger.info(
            "DockerCodingSandbox.upload_file: path=%s full_path=%s size=%d sandbox=%s",
            path, full_path, len(content), self._sandbox_id,
        )

    async def download_file(self, path: str) -> bytes:
        full_path = self._resolve_path(path)
        with open(full_path, "rb") as f:
            return f.read()

    async def read_file_head(self, path: str, max_bytes: int) -> tuple[bytes, bool]:
        return read_local_file_head(self._resolve_path(path), max_bytes)

    async def file_size(self, path: str) -> int | None:
        return os.stat(self._resolve_path(path)).st_size

    async def list_files(self) -> list[str]:
        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._working_dir):
            dirnames[:] = [
                d for d in dirnames
                if d not in _LISTING_IGNORED_DIRS and not d.startswith("_src")
            ]
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if os.path.islink(full):
                    continue
                results.append(os.path.relpath(full, self._working_dir))
        return sorted(results)

    async def destroy(self) -> None:
        shutil.rmtree(self._working_dir, ignore_errors=True)
        unregister_sandbox_dir(self._working_dir)
        self._provisioned = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _output_dir(self) -> str:
        return os.path.join(self._working_dir, "output")

    @property
    def _deps_python_dir(self) -> str:
        return os.path.join(self._working_dir, "deps_python")

    @property
    def _deps_node_dir(self) -> str:
        return os.path.join(self._working_dir, "node_modules")

    def _resolve_path(self, path: str) -> str:
        """Reject any `path` that escapes the sandbox working directory —
        same traversal boundary as `LocalCodingSandbox`/`E2BCodingSandbox`."""
        root = os.path.realpath(self._working_dir)
        full_path = os.path.realpath(os.path.join(root, normalize_sandbox_path(path)))
        if full_path != root and not full_path.startswith(root + os.sep):
            raise ValueError(f"path {path!r} escapes the sandbox working directory")
        return full_path

    def _promote_src_artifacts(
        self, src_dir: str, entry: str, staged_inputs: dict[str, _StagedFile],
    ) -> list[str]:
        """Move files the program wrote to its container cwd (/src, mirrored
        back into `src_dir` after the run) up into the sandbox working dir,
        and report them as artifacts.

        Models overwhelmingly write output files to their cwd, not to
        $OUTPUT_DIR — the local backend captures those via its mtime diff,
        but Docker only extracted /output, silently discarding every
        cwd-written file with the container. This restores parity.

        `staged_inputs` describes the working-dir files that were archived
        INTO `/src` before the run (see `execute()`) — every
        artifact the model staged via `input_artifacts` lands back here
        byte-identical unless the program actually rewrote it, since the
        mirror-back is a merge, not a diff. Skip re-"promoting" (and
        re-reporting as a freshly produced artifact) any file whose content
        still matches its staged baseline; a program that genuinely
        modified a staged input (same path, different bytes) still gets
        that change picked up and reported.
        """
        promoted: list[str] = []
        skipped_unchanged: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(src_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, src_dir)
                if rel == entry:
                    continue
                if os.path.islink(full):
                    # A program that symlinked a host path into its cwd must not
                    # have that followed when we read or move it back (SB-5).
                    logger.warning("_promote_src_artifacts: skipping symlink %s", rel)
                    continue
                if rel in staged_inputs:
                    if _replaced_since_staged(os.path.join(self._working_dir, rel), staged_inputs[rel]):
                        # Uploaded again while the run was going: the newer upload wins.
                        logger.warning(
                            "_promote_src_artifacts: %r was replaced on the host during the run; keeping that copy",
                            rel,
                        )
                        continue
                    same = _same_content(full, os.path.join(self._working_dir, rel))
                    if same is None:
                        continue
                    if same:
                        skipped_unchanged.append(rel)
                        continue
                dest = os.path.join(self._working_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(full, dest)
                promoted.append(rel)
        logger.info(
            "_promote_src_artifacts: promoted=%s skipped_unchanged=%s "
            "staged_baselines=%d entry=%s",
            promoted[:20], skipped_unchanged[:20],
            len(staged_inputs), entry,
        )
        return promoted

    def _list_output_artifacts(self, before: dict[str, float] | None = None) -> list[str]:
        """List files under `output/`, relative to the sandbox working dir.

        `before` (a `_snapshot_mtimes(self._output_dir)` taken prior to
        THIS call's container run) restricts the result to files that are
        new or changed since then — `output/` is a host directory that
        persists and merges across calls on the same sandbox instance
        (`_extract_container_dir` never clears it), so without this filter
        every earlier call's output would be re-listed, re-downloaded, and
        re-registered on every subsequent call in the same run. `None`
        (the default) preserves the unfiltered "list everything" behavior
        for callers that don't have a baseline (e.g. tests, `list_files()`
        semantics elsewhere in this module)."""
        results: list[str] = []
        if not os.path.isdir(self._output_dir):
            logger.info("_list_output_artifacts: output_dir %s does not exist", self._output_dir)
            return results
        for dirpath, _dirnames, filenames in os.walk(self._output_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if os.path.islink(full):
                    # Never report a symlink as a deliverable artifact — a
                    # later download/read would follow it off the host (SB-5).
                    logger.warning("_list_output_artifacts: skipping symlink %s", full)
                    continue
                if before is not None:
                    rel_to_output = os.path.relpath(full, self._output_dir)
                    try:
                        mtime = os.path.getmtime(full)
                    except OSError:
                        continue
                    if before.get(rel_to_output) == mtime:
                        continue
                results.append(os.path.relpath(full, self._working_dir))
        logger.info("_list_output_artifacts: found %d file(s): %s", len(results), sorted(results))
        return sorted(results)

    def _firewalled(self, command: list[str], egress_cidrs: list[str], token: str) -> dict[str, Any]:
        return firewalled_container_kwargs(
            command,
            network_cidrs=egress_cidrs,
            allowed_cidrs=self._egress_allow_cidrs,
            run_as=self._sandbox_user,
            token=token,
        )

    # -- blocking docker-py calls: only ever invoked via
    # -- DockerClientProvider.run_blocking, on its dedicated executor --

    def _run_container_sync(
        self,
        command: list[str],
        src_dir: str,
        timeout: float,
        network_enabled: bool,
        staged_inputs: dict[str, _StagedFile],
        client: Any,
        egress_network: str | None,
        egress_cidrs: list[str] | None = None,
        handle: _RunHandle | None = None,
    ) -> _RunOutcome:
        handle = handle or _RunHandle()
        token = new_firewall_token()
        outcome = self._run_container_once(
            command, src_dir, timeout, network_enabled, staged_inputs, client,
            egress_network, egress_cidrs or [], token, handle,
        )
        if network_enabled and firewall_unavailable(outcome.exit_code, outcome.stderr, token):
            # The firewall step exits before the command starts, so running
            # again offline cannot repeat any of the program's side effects.
            logger.error(
                "_run_container_sync: image %s cannot install the egress firewall; "
                "running without network: %.500s", self._image, outcome.stderr,
            )
            outcome = self._run_container_once(
                command, src_dir, timeout, False, staged_inputs, client, None, [], "", handle,
            )
            outcome.stderr = _NETWORK_UNAVAILABLE_NOTE + outcome.stderr
        return outcome

    def _run_container_once(
        self,
        command: list[str],
        src_dir: str,
        timeout: float,
        network_enabled: bool,
        staged_inputs: dict[str, _StagedFile],
        client: Any,
        egress_network: str | None,
        egress_cidrs: list[str],
        token: str,
        handle: _RunHandle | None = None,
    ) -> _RunOutcome:
        handle = handle or _RunHandle()
        container = None
        try:
            has_py_deps = os.path.isdir(self._deps_python_dir) and bool(os.listdir(self._deps_python_dir))
            has_node_deps = os.path.isdir(self._deps_node_dir) and bool(os.listdir(self._deps_node_dir))
            env = {"OUTPUT_DIR": "/output"}
            if has_py_deps:
                env["PYTHONPATH"] = "/deps"
            node_paths: list[str] = []
            if has_node_deps:
                node_paths.append("/node_modules")
            if self._image_node_modules:
                node_paths.append(self._image_node_modules)
            if node_paths:
                env["NODE_PATH"] = ":".join(node_paths)

            mem_bytes = self._memory_limit_mb * 1024 * 1024
            nano_cpus = int(self._cpu_limit * 1e9)
            container_kwargs: dict[str, Any] = {
                "image": self._image,
                "command": command,
                "environment": env,
                "working_dir": "/src",
                "mem_limit": mem_bytes,
                "nano_cpus": nano_cpus,
                "tmpfs": {"/tmp": "size=100M"},
                "detach": True,
                **CONTAINER_HARDENING,
            }
            if network_enabled:
                container_kwargs["network"] = egress_network
                container_kwargs["network_disabled"] = False
                container_kwargs.update(self._firewalled(command, egress_cidrs, token))
            else:
                container_kwargs["network_mode"] = "none"
                container_kwargs["network_disabled"] = self._network_disabled
            container = create_bounded_container(client, **container_kwargs)
            handle.container_id = container.id
            if handle.cancelled.is_set():
                raise _RunCancelled
            logger.info(
                "_run_container_sync: container created id=%.12s image=%s "
                "network_enabled=%s mem_limit=%dMB cpu=%.1f env=%s",
                container.id, self._image, network_enabled,
                self._memory_limit_mb, self._cpu_limit, env,
            )

            container.put_archive("/", _tar_empty_dir("src", mode=0o777))
            if staged_inputs:
                tar_size = _put_tar(
                    container, "/src",
                    lambda tar: _add_staged_inputs(tar, self._working_dir, staged_inputs),
                )
                logger.info(
                    "_run_container_sync: put_archive staged_inputs into /src — "
                    "%d file(s), tar_size=%d bytes, paths=%s",
                    len(staged_inputs), tar_size, sorted(staged_inputs.keys())[:20],
                )
            else:
                logger.info("_run_container_sync: no staged_inputs to archive")
            _put_directory(container, "/src", src_dir)
            container.put_archive("/", _tar_empty_dir("output", mode=0o777))
            # Restore what earlier calls on this sandbox left in $OUTPUT_DIR.
            # Every execute() gets a fresh container, so without this the
            # deliverable directory silently resets between calls while the
            # working directory persists — and a model that writes a file
            # then reads it back to verify it (the normal pattern) gets a
            # FileNotFoundError for a file that is right there on the host.
            # `_tar_directory` preserves mtimes, so restored files still look
            # unchanged to `_list_output_artifacts` and are not re-reported.
            if os.path.isdir(self._output_dir) and os.listdir(self._output_dir):
                _put_directory(container, "/output", self._output_dir)
            if has_py_deps:
                container.put_archive("/", _tar_empty_dir("deps", mode=0o755))
                _put_directory(container, "/deps", self._deps_python_dir)
            if has_node_deps:
                container.put_archive("/", _tar_empty_dir("node_modules", mode=0o755))
                _put_directory(container, "/node_modules", self._deps_node_dir)

            logger.info(
                "_run_container_sync: starting container %.12s "
                "(has_py_deps=%s has_node_deps=%s)",
                container.id, has_py_deps, has_node_deps,
            )
            if handle.cancelled.is_set():
                raise _RunCancelled
            container.start()
            watch = _DiskWatch.start(client, container.id, _disk_watch_limit_for(client))

            timed_out = False
            oom_killed, ran_s = False, None
            try:
                exit_info = container.wait(timeout=timeout + _HOST_WAIT_MARGIN_S)
                exit_code = exit_info.get("StatusCode", -1)
            except Exception:
                timed_out = True
                exit_code = -1
                try:
                    container.kill()
                except Exception:
                    pass
            else:
                oom_killed, ran_s = _container_exit_state(container)
            finally:
                if watch is not None:
                    watch.stop()
            disk_exceeded = watch.exceeded_bytes if watch is not None else None
            disk_unchecked = watch.unchecked_reason if watch is not None else None
            if handle.cancelled.is_set():
                raise _RunCancelled

            stdout = _capped_logs(container, stdout=True)
            stderr = _capped_logs(container, stdout=False)
            if timed_out:
                stderr = f"Execution timed out after {timeout}s\n{stderr}".strip()

            logger.info(
                "_run_container_sync: container %.12s finished exit_code=%d "
                "timed_out=%s oom_killed=%s stdout_len=%d stderr_len=%d stderr_preview=%.500s",
                container.id, exit_code, timed_out, oom_killed,
                len(stdout), len(stderr),
                stderr[:500] if stderr else "",
            )

            os.makedirs(self._output_dir, exist_ok=True)
            budget = self._collect_run_files(container, src_dir, staged_inputs)
            # The exit status after `timeout -s KILL` is not a reliable signal
            # (137, or 124 from some builds), so the run time decides.
            deadline_hit = timed_out or (ran_s is not None and ran_s >= timeout - 1)
            return _RunOutcome(
                exit_code, stdout, stderr, oom_killed=oom_killed, ran_s=ran_s,
                deadline_hit=deadline_hit and disk_exceeded is None and disk_unchecked is None,
                disk_exceeded_bytes=disk_exceeded,
                disk_unchecked=disk_unchecked,
                collection_note=budget.note(),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def _collect_run_files(
        self, container: Any, src_dir: str, staged_inputs: dict[str, _StagedFile],
    ) -> ExtractBudget:
        """Bring a finished run's ``/output`` and ``/src`` back to the host.

        One budget covers both directories: together they are what this run
        hands back. It is also held to what the sandbox may still keep
        across its runs (`SandboxRetention`). Files the run got from the
        host and left as they were (restored output, staged inputs, the
        entry file) are read past, not extracted again, and not counted.
        """
        output_dir = self._output_dir
        output_bytes, _ = _dir_usage(output_dir)
        src_bytes, _ = _dir_usage(src_dir)
        staged_bytes = sum(f.size for f in staged_inputs.values())

        # Each returns the host file the member may be a copy of; the
        # extractor compares the bytes before it skips the member.
        def _output_unchanged(name: str, member: tarfile.TarInfo) -> str | None:
            path = os.path.join(output_dir, name)
            return path if _same_as_host_file(path, member) else None

        def _src_unchanged(name: str, member: tarfile.TarInfo) -> str | None:
            staged = staged_inputs.get(name)
            if staged is not None and staged.matches(member):
                return os.path.join(self._working_dir, name)
            path = os.path.join(src_dir, name)
            return path if _same_as_host_file(path, member) else None

        with self._collect_lock:
            if self._kept is None:
                self._kept = _dir_usage(output_dir)
            kept_bytes, kept_files = self._kept
            budget = ExtractBudget.for_output(
                SandboxRetention.from_env(kept_bytes, kept_files),
                unchanged_allowance=output_bytes + src_bytes + staged_bytes,
            )
            _extract_container_dir(
                container, "/output", output_dir, budget=budget, unchanged=_output_unchanged,
            )
            # /src files move up into the working dir, replacing what is there.
            _extract_container_dir(
                container, "/src", src_dir, budget=budget, unchanged=_src_unchanged,
                charged=self._src_charged,
            )
            self._kept = (
                max(0, kept_bytes + budget.retained_bytes), max(0, kept_files + budget.retained_files),
            )
        return budget

    async def _ensure_image(self) -> None:
        """Pull the sandbox image if the daemon doesn't have it.

        Presence is cached per process, so this is a dict lookup on every
        run after the first — the previous code asked the daemon on every
        single execution.
        """
        if await self._provider.ensure_image(self._image):
            return
        logger.info("Sandbox image %s not found locally, attempting pull ...", self._image)
        try:
            await self._provider.pull_image(self._image)
        except Exception as exc:
            # A failed pull says nothing about the image when the daemon
            # never answered at all: an endpoint nothing is listening on
            # fails `images.get` too, so `ensure_image` returning False
            # cannot tell the two apart. Blaming the image sends the reader
            # hunting for something that is usually already present — the
            # socket proxy simply was not running.
            if not await self._provider.ping():
                raise CodingSandboxError(
                    f"The coding sandbox cannot run: Docker is unreachable at "
                    f"{self._provider.docker_host()} ({exc}). The sandbox reaches the "
                    f"daemon through a proxy that has to be running: under Compose "
                    f"that is docker-socket-proxy, started by the 'sandbox' profile; "
                    f"on Helm it is the docker-proxy sidecar."
                ) from exc
            raise CodingSandboxError(
                f"Sandbox image {self._image!r} is not available locally and "
                f"could not be pulled: {exc}"
            ) from exc

    def _install_packages_sync(
        self, to_install: list[str], language: CodingLanguage,
        client: Any, network_name: str, egress_cidrs: list[str] | None = None,
        handle: _RunHandle | None = None,
    ) -> tuple[bool, str, str]:
        if language == "python":
            cmd = [
                "sh", "-c",
                "pip install --quiet --no-cache-dir --target /deps "
                f"--index-url {self._pip_index_url} " + _shell_words(to_install),
            ]
            extract_path = "/deps"
            host_target = self._deps_python_dir
        else:
            cmd = [
                "sh", "-c",
                "mkdir -p /install && npm install --prefix /install --no-save "
                f"--loglevel=error --registry={self._npm_registry} "
                + _shell_words(to_install),
            ]
            extract_path = "/install/node_modules"
            host_target = self._deps_node_dir

        logger.info(
            "_install_packages_sync: language=%s to_install=%s image=%s "
            "network=%s host_target=%s",
            language, to_install, self._image, network_name, host_target,
        )
        # Fail closed: pip builds sdists and npm runs lifecycle scripts, so an
        # unfiltered install container would hand package code the host,
        # private ranges and cloud metadata.
        if not egress_cidrs:
            logger.error("_install_packages_sync: egress network %s has no readable IPv4 subnet", network_name)
            return False, "", _INSTALL_FIREWALL_UNAVAILABLE.format(reason="egress bridge subnet unreadable")
        token = new_firewall_token()
        exit_code, stdout, stderr = self._install_in_container(
            client, network_name, extract_path, host_target, to_install,
            self._firewalled(deadline_command(cmd, _INSTALL_TIMEOUT_S), egress_cidrs, token), handle,
        )
        if firewall_unavailable(exit_code, stderr, token):
            logger.error(
                "_install_packages_sync: image %s cannot install the egress firewall: %.500s",
                self._image, stderr,
            )
            return False, stdout, _INSTALL_FIREWALL_UNAVAILABLE.format(reason=f"image {self._image}") + stderr
        return exit_code == 0, stdout, stderr

    def _install_in_container(
        self, client: Any, network_name: str, extract_path: str, host_target: str,
        to_install: list[str], command_kwargs: dict[str, Any],
        handle: _RunHandle | None = None,
    ) -> tuple[int, str, str]:
        handle = handle or _RunHandle()
        container = create_bounded_container(
            client,
            purpose="install",
            image=self._image,
            environment={},
            mem_limit=self._memory_limit_mb * 1024 * 1024,
            nano_cpus=int(self._cpu_limit * 1e9),
            network=network_name,
            network_disabled=False,
            detach=True,
            **command_kwargs,
        )
        handle.container_id = container.id
        try:
            if handle.cancelled.is_set():
                raise _RunCancelled
            if extract_path == "/deps":
                container.put_archive("/", _tar_empty_dir("deps", mode=0o777))
            else:
                container.put_archive("/", _tar_empty_dir("install", mode=0o777))
            if handle.cancelled.is_set():
                raise _RunCancelled
            container.start()
            watch = _DiskWatch.start(client, container.id, _disk_watch_limit_for(client))
            try:
                exit_info = container.wait(timeout=_INSTALL_TIMEOUT_S + _HOST_WAIT_MARGIN_S)
                timed_out = False
            except Exception:
                # The deadline inside the container failed to end it (an image
                # without `timeout`); a raw read-timeout text would reach the model.
                if handle.cancelled.is_set():
                    raise _RunCancelled from None
                timed_out = True
                exit_info = {"StatusCode": -1}
                try:
                    container.kill()
                except Exception:
                    pass
            finally:
                if watch is not None:
                    watch.stop()
            if handle.cancelled.is_set():
                raise _RunCancelled
            exit_code = exit_info.get("StatusCode", -1)
            oom_killed, ran_s = _container_exit_state(container) if not timed_out else (False, None)
            stdout = _capped_logs(container, stdout=True)
            stderr = _capped_logs(container, stdout=False)
            if watch is not None and watch.stopped_run:
                exit_code = exit_code or 137
                stderr = disk_watch_note(watch, install=True) + stderr
            elif exit_code != 0 and (timed_out or (ran_s is not None and ran_s >= _INSTALL_TIMEOUT_S - 1)):
                stderr = _INSTALL_TIMEOUT_NOTE.format(seconds=_INSTALL_TIMEOUT_S) + stderr
            elif oom_killed and exit_code != 0:
                stderr = _INSTALL_OOM_NOTE.format(limit=self._memory_limit_mb) + stderr
            logger.info(
                "_install_packages_sync: container %.12s exit_code=%d "
                "stdout_len=%d stderr_preview=%.500s",
                container.id, exit_code, len(stdout),
                stderr[:500] if stderr else "",
            )
            if exit_code != 0:
                logger.warning(
                    "_install_packages_sync: install FAILED for %s — "
                    "exit_code=%d stderr=%.1000s",
                    to_install, exit_code, stderr,
                )
                return exit_code, stdout, stderr
            os.makedirs(host_target, exist_ok=True)
            # Dependency archive (pip --target / npm node_modules): keep in-tree
            # symlinks such as node_modules/.bin so installed CLIs survive the
            # round trip; filter="data" still blocks links that escape the dir.
            # It is extracted next to the installed packages and merged in only
            # once it is complete, so a refused install leaves them as they were.
            budget = ExtractBudget.for_dependencies()
            staging = tempfile.mkdtemp(prefix=".install-", dir=os.path.dirname(host_target))
            try:
                _extract_container_dir(
                    container, extract_path, staging, allow_symlinks=True, budget=budget,
                )
                note = budget.note()
                if note:
                    logger.warning("_install_packages_sync: %s", note.strip())
                    return 1, stdout, note + stderr
                _merge_tree(staging, host_target)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            return exit_code, stdout, stderr
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass


# ------------------------------------------------------------------
# Tar helpers for put_archive / get_archive — ported from
# app/sandbox/docker_executor.py (generic, no PipesHub-specific naming).
# ------------------------------------------------------------------

def _merge_tree(src: str, dst: str) -> None:
    """Move everything under ``src`` into ``dst``, replacing files and links
    of the same name; directories are merged."""
    for dirpath, dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        if os.path.islink(target_dir) or (os.path.lexists(target_dir) and not os.path.isdir(target_dir)):
            os.unlink(target_dir)
        os.makedirs(target_dir, exist_ok=True)
        for name in list(dirnames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                # A link to a directory is moved as a link, not walked.
                dirnames.remove(name)
                filenames.append(name)
        for name in filenames:
            target = os.path.join(target_dir, name)
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            os.replace(os.path.join(dirpath, name), target)


def _shell_words(specs: list[str]) -> str:
    """Package specs as literal ``sh`` words: a version range like
    ``numpy>=1.26`` would otherwise be read as a redirection, and an npm
    ``lodash@4.*`` as a glob."""
    return " ".join(shlex.quote(spec) for spec in specs)


def _read_file_nofollow(path: str) -> bytes | None:
    """Read a file, refusing to follow a final-component symlink (SB-5).

    Returns ``None`` (and logs) when the path is a symlink or cannot be opened
    without following one, so a booby-trapped link left in the sandbox dir can
    never make the host read a file outside it.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        logger.warning("Refusing to read %s without following symlinks: %s", path, exc)
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            return fh.read()
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def _collect_working_dir_inputs(working_dir: str) -> dict[str, bytes]:
    """Every file under `working_dir` EXCEPT the reserved, separately-
    archived subtrees (`_RESERVED_WORKING_DIR_SUBDIRS`) — i.e. whatever
    `upload_file()` has staged there across this sandbox's lifetime
    (`input_artifacts` bytes, skill resources, ...) plus any file a PRIOR
    run promoted from its own cwd (see `_promote_src_artifacts`). Returned
    as an explicit `{relative_path: content}` map, read fully into memory,
    so the caller can both build a tar from it AND diff against it later
    without a second disk read.
    """
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(working_dir):
        if dirpath == working_dir:
            dirnames[:] = [d for d in dirnames if d not in _RESERVED_WORKING_DIR_SUBDIRS]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, working_dir)
            if os.path.islink(full):
                # Skip symlinks so a staged-input tar never carries host file
                # contents back into the next container (SB-5).
                logger.warning("_collect_working_dir_inputs: skipping symlink %s", rel)
                continue
            content = _read_file_nofollow(full)
            if content is not None:
                files[rel] = content
    return files


def _tar_files(files: dict[str, bytes]) -> bytes:
    """In-memory tar built from an explicit `{relative_path: content}`
    map — unlike `_tar_directory` (which tars a real directory's flat
    listing), this preserves whatever nested structure the paths encode
    (e.g. `input/artifacts/foo.png`) without touching disk beyond what the
    caller already read."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel_path, content in files.items():
            info = tarfile.TarInfo(name=rel_path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf.read()


def _tar_empty_dir(name: str, *, mode: int = 0o755) -> bytes:
    """In-memory tar containing a single empty directory entry."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.type = tarfile.DIRTYPE
        info.mode = mode
        tar.addfile(info)
    buf.seek(0)
    return buf.read()


# Archives up to this size are sent from memory; larger ones from an
# unlinked temporary file, so restoring a sandbox's files never holds them
# all in memory.
_TAR_IN_MEMORY_MAX = 1024 * 1024
_HASH_CHUNK = 1024 * 1024


def _put_tar(container: Any, path: str, add: Callable[[tarfile.TarFile], None]) -> int:
    """``put_archive`` of a tar written by ``add``, spooled through a
    temporary file. Returns the archive size."""
    with tempfile.TemporaryFile() as spool:
        with tarfile.open(fileobj=spool, mode="w") as tar:
            add(tar)
        size = spool.tell()
        spool.seek(0)
        container.put_archive(path, spool.read() if size <= _TAR_IN_MEMORY_MAX else spool)
        return size


def _put_directory(container: Any, path: str, src_dir: str) -> int:
    """``put_archive`` of every entry directly under ``src_dir`` (flat, no
    wrapping directory entry), streamed from disk."""
    def _add(tar: tarfile.TarFile) -> None:
        for fname in os.listdir(src_dir):
            tar.add(os.path.join(src_dir, fname), arcname=fname)
    return _put_tar(container, path, _add)


@dataclass(frozen=True)
class _StagedFile:
    """A working-dir file archived into ``/src``: its size and whole-second
    mtime, as written into the archive, and the nanosecond mtime, inode and
    device of the host file, so a later upload is told apart from the copy
    that was staged even with the same size in the same second."""

    size: int
    mtime: int
    mtime_ns: int
    ino: int
    dev: int

    @classmethod
    def from_stat(cls, st: os.stat_result) -> _StagedFile:
        return cls(st.st_size, int(st.st_mtime), st.st_mtime_ns, st.st_ino, st.st_dev)

    def matches(self, member: tarfile.TarInfo) -> bool:
        return member.size == self.size and abs(float(member.mtime) - self.mtime) < 1.0


def _scan_working_dir_inputs(working_dir: str) -> dict[str, _StagedFile]:
    """Every regular file under `working_dir` EXCEPT the reserved, separately-
    archived subtrees (`_RESERVED_WORKING_DIR_SUBDIRS`) — whatever
    `upload_file()` has staged there (`input_artifacts`, skill resources,
    ...) plus files earlier runs promoted from their cwd. Only sizes and
    mtimes are read here; `_add_staged_inputs` streams the contents into
    the archive, so they are never all in memory at once."""
    files: dict[str, _StagedFile] = {}
    for dirpath, dirnames, filenames in os.walk(working_dir):
        if dirpath == working_dir:
            dirnames[:] = [d for d in dirnames if d not in _RESERVED_WORKING_DIR_SUBDIRS]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, working_dir)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                # Skip symlinks so a staged-input tar never carries host file
                # contents back into the next container (SB-5).
                logger.warning("_scan_working_dir_inputs: skipping non-regular file %s", rel)
                continue
            files[rel] = _StagedFile.from_stat(st)
    return files


def _add_staged_inputs(
    tar: tarfile.TarFile, working_dir: str, staged: dict[str, _StagedFile],
) -> None:
    """Write the staged files into ``tar`` from disk, without following a
    symlink swapped in since the scan. ``staged`` is updated to what was
    actually written."""
    for rel in list(staged):
        full = os.path.join(working_dir, rel)
        try:
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            logger.warning("Refusing to stage %s without following symlinks: %s", rel, exc)
            del staged[rel]
            continue
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                del staged[rel]
                continue
            info = tarfile.TarInfo(name=rel)
            info.size = st.st_size
            info.mtime = int(st.st_mtime)
            tar.addfile(info, fh)
            staged[rel] = _StagedFile.from_stat(st)


def _same_as_host_file(path: str, member: tarfile.TarInfo) -> bool:
    """Whether the regular host file at ``path`` already has ``member``'s
    size and (whole-second) mtime, i.e. the run got it from the host and
    left it alone."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISREG(st.st_mode)
        and st.st_size == member.size
        and abs(float(member.mtime) - st.st_mtime) < 1.0
    )


def _dir_usage(root: str) -> tuple[int, int]:
    """Bytes and regular files under ``root`` (apparent sizes)."""
    total = files = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, fname))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
                files += 1
    return total, files


def _replaced_since_staged(path: str, staged: _StagedFile) -> bool:
    """Whether the host file at ``path`` is no longer the one archived into
    the run (another upload replaced it, or it is gone). An upload that
    renames a new file into place changes the inode; one that writes in
    place changes the nanosecond mtime, even within the same second."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return (
        not stat.S_ISREG(st.st_mode)
        or st.st_size != staged.size
        or st.st_mtime_ns != staged.mtime_ns
        or st.st_ino != staged.ino
        or st.st_dev != staged.dev
    )


def _same_content(a: str, b: str) -> bool | None:
    """Whether two host files have the same bytes, read in chunks without
    following symlinks; None when either cannot be read."""
    try:
        fa = os.open(a, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        logger.warning("Refusing to read %s without following symlinks: %s", a, exc)
        return None
    try:
        try:
            fb = os.open(b, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return False
        with os.fdopen(fa, "rb", closefd=False) as ha, os.fdopen(fb, "rb") as hb:
            if os.fstat(ha.fileno()).st_size != os.fstat(hb.fileno()).st_size:
                return False
            while True:
                x, y = ha.read(_HASH_CHUNK), hb.read(_HASH_CHUNK)
                if x != y:
                    return False
                if not x:
                    return True
    finally:
        os.close(fa)


def _extract_container_dir(
    container: object, container_path: str, local_dir: str, *, allow_symlinks: bool = False,
    budget: ExtractBudget | None = None,
    unchanged: Callable[[str, tarfile.TarInfo], str | None] | None = None,
    charged: dict[str, int] | None = None,
) -> ExtractBudget:
    """Pull a directory from a container via `get_archive` and extract it
    into `local_dir`, merging with (not clearing) whatever's already there.

    The archive is read as it streams in, within `budget` (a fresh output
    budget when none is given): files over the per-file limit are skipped,
    collection stops at the total or file-count limit, and nothing larger
    than the limits is ever held in memory or written to disk. See
    `output_limits.extract_tar_stream`.

    For untrusted archives (``/output``, ``/src``) only regular files are
    extracted: a symlink left on the host would be followed by the next run's
    readers and copy host files back into the sandbox (SB-5). For dependency
    archives (``allow_symlinks=True`` — ``/deps``, ``/node_modules``, which npm
    fills with ``.bin`` links) symlinks are kept, but `filter="data"` still
    rejects any link whose target escapes `local_dir`, so nothing can point at
    the host. Every member is sanitised by `filter="data"`, and a member whose
    own path would land outside `local_dir` is skipped. One bad member is
    skipped, not fatal, so the rest of the archive still extracts.
    """
    budget = budget or ExtractBudget.for_output()
    try:
        bits, _ = container.get_archive(container_path)
        extract_tar_stream(
            bits, container_path, local_dir, budget,
            allow_symlinks=allow_symlinks, unchanged=unchanged, charged=charged,
        )
    except Exception:
        logger.debug("No output artifacts to extract from container %s", container_path)
    return budget


def _capped_logs(container: Any, *, stdout: bool) -> str:
    """One of a finished container's streams, keeping only its first and
    last bytes (`output_limits.stdio_limits`); the rest is read past and
    dropped, never held."""
    try:
        try:
            # `follow` defaults to `stream`: a container still running (a kill
            # that failed) would keep this read open.
            stream = container.logs(stdout=stdout, stderr=not stdout, stream=True, follow=False)
        except TypeError:
            # A client without streaming logs.
            stream = container.logs(stdout=stdout, stderr=not stdout)
    except Exception as exc:
        logger.debug("reading container logs failed: %s", exc)
        return ""
    return read_capped_stream(stream)


def _make_private_dir(path: str) -> None:
    """Create `path` readable only by this user (0700), whatever the umask:
    other local users must not read another chat's inputs and outputs."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        logger.warning("could not make sandbox dir %s private: %s", path, exc)


def read_local_file_head(full_path: str, max_bytes: int) -> tuple[bytes, bool]:
    """At most `max_bytes` of a host file, and whether it has more. Reads
    only what it returns (plus one byte)."""
    max_bytes = max(0, int(max_bytes))
    with open(full_path, "rb") as f:
        data = f.read(max_bytes + 1)
    return data[:max_bytes], len(data) > max_bytes
