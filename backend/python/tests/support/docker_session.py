"""Docker resources of one test process: attributable, and reclaimed even when the process dies before teardown.

A lane's fixtures remove what they started, but a process killed by pid, by pytest-timeout's ``thread`` method
(``os._exit``) or an xdist worker crash runs no finalizer, so its containers outlive it (MCPLEAK: E2E3's MCP
runners and Neo4j ran for days; BUGS-A: E2E4's ArangoDB, which carried none of these labels). Every resource a
suite starts carries ``labels(suite)``: ``remove_session`` (registered with ``at_exit`` on first use) removes this
process's at exit, and ``reap_stale`` at a suite's start removes those of an earlier process of the same suite and
user that is gone, or that is past its lifetime whatever its process looks like.

What the reaper assumes, and why it holds on a daemon shared with other users and agents:
- only ``pxh-*`` resources (by name, or an image's tag) carrying this suite's and this account's labels are ever
  candidates; anything else on the daemon is never listed or touched;
- a session is judged gone only by a pid check on this host; another host's sessions only expire;
- a pid can be reused and a run can hang, so a live-looking session's resources still go after ``LIFETIME``;
- ages are measured on the daemon's clock, the one that stamped ``Created``.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import pwd
import re
import socket
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

SUITE = "pxh.test.suite"
SESSION = "pxh.test.session"
USER = "pxh.test.user"
LIFETIME = "pxh.test.lifetimeS"
# The pytest invocation (``tests/support/sandbox_reaper.py``): its own process removes whatever carries it at the end,
# what a worker killed before its exit handlers ran left included.
RUN = "pxh.test.run"
RUN_ENV = "PXH_TEST_RUN"
# The pytest process that owns it; a run killed outright is swept by the next run once this process is gone.
OWNER = "pxh.test.owner"
OWNER_ENV = "PXH_TEST_OWNER"
# Only a resource at least this old is reaped, so a session that is starting up is never raced.
STALE_AFTER_S = 600
# No lane holds a resource this long (the longest Docker lane runs for about an hour).
LIFETIME_S = 12 * 3600
# How long a slow daemon is given to finish a container removal it has started.
REMOVE_WAIT_S = 300
PREFIX = "pxh-"

_HOST = socket.gethostname()
_session = f"{_HOST}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
_registered: set[str] = set()
_exit_handlers: list[tuple[Callable[..., object], tuple[object, ...]]] = []
# Whether ``run_exit_handlers`` is registered with ``atexit`` (once, whatever runs the handlers first).
_atexit_registered: list[bool] = []


def _user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def at_exit(handler: Callable[..., object], *args: object) -> None:
    """Runs ``handler(*args)`` when this process ends: from ``atexit``, and from ``run_exit_handlers`` on the paths
    that skip ``atexit`` (``tests/conftest.py``'s ``os._exit`` under xdist, a hang guard's or pytest-timeout's
    ``thread`` method's ``os._exit``, SIGTERM). SBXLEAK: under xdist every worker and the controller leave through
    ``os._exit``, so handlers registered with ``atexit`` alone never ran in a gate."""
    if not _atexit_registered:
        _atexit_registered.append(True)
        atexit.register(run_exit_handlers)
    _exit_handlers.append((handler, args))


def run_exit_handlers() -> None:
    """Each registered handler once, latest first as ``atexit`` runs them; nothing raises."""
    while _exit_handlers:
        handler, args = _exit_handlers.pop()
        with contextlib.suppress(Exception):
            _ = handler(*args)


def labels(suite: str) -> dict[str, str]:
    """The labels of ``suite``'s resources started by this process; the first call registers its ``at_exit``."""
    if suite not in _registered:
        _registered.add(suite)
        at_exit(remove_session, suite)
    run = {label: os.environ[env] for label, env in ((RUN, RUN_ENV), (OWNER, OWNER_ENV)) if os.environ.get(env)}
    return {SUITE: suite, SESSION: _session, USER: _user(), LIFETIME: str(LIFETIME_S), **run}


def _gone(session: str) -> bool:
    """A session of this host whose process no longer runs; another host's is never judged gone."""
    host, _, rest = session.partition(":")
    pid = rest.partition(":")[0]
    if host != _HOST or not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _epoch(value: object) -> int | None:
    """Docker's ``Created``: seconds for containers and images, RFC 3339 (nanoseconds, any offset) otherwise."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        with contextlib.suppress(ValueError):
            return int(datetime.fromisoformat(re.sub(r"\.\d+", "", value)).timestamp())
    return None


def _is_not_found(exc: Exception) -> bool:
    return getattr(getattr(exc, "response", None), "status_code", None) == 404 or type(exc).__name__ == "NotFound"


def _is_slow(exc: Exception) -> bool:
    """The daemon is still removing it: the client gave up waiting, or an earlier request already started it."""
    import requests

    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status == 409 and "already in progress" in str(exc)


def remove_container(api: Any, container: str, **kwargs: Any) -> bool:  # noqa: ANN401 - docker.APIClient
    """Force-removes ``container``. A removal the daemon is slow to finish is waited for (``REMOVE_WAIT_S``) rather
    than failed; ``False`` if it is still running then (the daemon finishes it on its own). Other errors raise."""
    try:
        api.remove_container(container, force=True, **kwargs)
        return True
    except Exception as exc:
        if _is_not_found(exc):
            return True
        if not _is_slow(exc):
            raise
    try:
        _ = api.wait(container, timeout=REMOVE_WAIT_S, condition="removed")
    except Exception as exc:
        if _is_not_found(exc):
            return True
        if not _is_slow(exc):
            raise
        return False
    return True


def _remove(client: Any, filters: list[str], keep: Any) -> int:  # noqa: ANN401 - docker.DockerClient
    """Removes the ``pxh-*`` containers, then volumes, networks and images that match ``filters`` and that ``keep``
    does not keep; the number of containers removed."""
    api = client.api
    removed = 0
    for info in api.containers(all=True, filters={"label": filters}):
        named = any(n.lstrip("/").startswith(PREFIX) for n in info.get("Names") or [])
        if named and not keep(info.get("Labels") or {}, _epoch(info.get("Created"))):
            with contextlib.suppress(Exception):
                if remove_container(api, info["Id"], v=True):
                    removed += 1
    for volume in (api.volumes(filters={"label": filters}) or {}).get("Volumes") or []:
        if volume["Name"].startswith(PREFIX) and not keep(volume.get("Labels") or {}, _epoch(volume.get("CreatedAt"))):
            with contextlib.suppress(Exception):
                api.remove_volume(volume["Name"], force=True)
    for network in api.networks(filters={"label": filters}) or []:
        if network["Name"].startswith(PREFIX) and not keep(network.get("Labels") or {}, _epoch(network.get("Created"))):
            with contextlib.suppress(Exception):
                api.remove_network(network["Id"])
    for image in api.images(filters={"label": filters}) or []:
        tagged = any(t.startswith(PREFIX) for t in image.get("RepoTags") or [])
        if tagged and not keep(image.get("Labels") or {}, _epoch(image.get("Created"))):
            with contextlib.suppress(Exception):
                api.remove_image(image["Id"], force=True)
    return removed


def remove_session(suite: str, client: Any = None) -> int:  # noqa: ANN401 - docker.DockerClient
    """This process's resources of ``suite``; the number of containers removed. Never raises: ``at_exit`` runs it
    in every process that labelled something, whether a daemon still answers or not."""
    try:
        import docker

        own = client or docker.from_env()
    except Exception:
        return 0
    try:
        filters = [f"{SUITE}={suite}", f"{SESSION}={_session}", f"{USER}={_user()}"]
        return _remove(own, filters, lambda _labels, _created: False)
    except Exception:
        return 0
    finally:
        if client is None:
            own.close()


def reap_stale(client: Any, suite: str, *, older_than_s: int = STALE_AFTER_S) -> int:  # noqa: ANN401
    """``suite``'s resources of this user left by a process that is gone (at least ``older_than_s`` old), or past
    their own ``LIFETIME`` whoever holds them; the number of containers removed. This process's resources, other
    suites', other users' and anything not ``pxh-*`` are kept."""
    try:
        now = _epoch(client.api.info().get("SystemTime")) or int(time.time())
    except Exception:
        now = int(time.time())

    def keep(item_labels: dict[str, str], created: int | None) -> bool:
        session = item_labels.get(SESSION, "")
        if session == _session:
            return True
        age = None if created is None else now - created
        lifetime = item_labels.get(LIFETIME, "")
        if age is not None and lifetime.isdigit() and age > int(lifetime):
            return False
        if not _gone(session):
            return True
        return age is not None and age < older_than_s

    return _remove(client, [f"{SUITE}={suite}", f"{USER}={_user()}"], keep)


__all__ = [
    "LIFETIME",
    "LIFETIME_S",
    "OWNER",
    "OWNER_ENV",
    "RUN",
    "RUN_ENV",
    "SESSION",
    "STALE_AFTER_S",
    "SUITE",
    "USER",
    "at_exit",
    "labels",
    "reap_stale",
    "remove_container",
    "remove_session",
    "run_exit_handlers",
]
