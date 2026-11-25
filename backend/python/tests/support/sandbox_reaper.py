"""Sandbox objects a test run made: labelled with the run and the process that owns them, and removed when they end.

SBXLEAK: a broker's sandboxes, warm sandboxes, per-sandbox networks and volumes are made by the broker under test,
often in a worker process the test spawned, and carry only the broker's own labels. A lane's teardown removes them by
its namespace, but nothing did when that teardown never ran or ran before a worker's last acquire: a thread-method
timeout, a hang guard and xdist's ``os._exit`` skip it, and a worker outlives a parent killed by pid. Each orphan
holds a network from the daemon's address pools, which then run out for every lane on the host.

``label_broker_objects`` wraps docker-py's ``create_container``, ``create_network`` and ``create_volume`` in this
process: an object a broker manages (an ``io.pipeshub.<namespace>.managed=1`` label) also gets

- ``RUN``: the pytest invocation (the xdist controller's, inherited by its workers and their child processes);
- ``OWNER``: the pytest process the object belongs to (an xdist worker, or the single process), inherited by the
  worker processes a test spawns, whose sandboxes must outlive them (a redrive adopts them);
- ``docker_session.USER``.

``reap_owned`` (each pytest process, when it ends) and ``reap_run`` (the invocation's own process, after its xdist
workers are down) remove what carries this process's or this run's label: only objects these labels were put on, by
this run, are ever listed. ``reap_dead_runs`` (each run's own process, at its start) takes what an earlier run left
when it was killed before any of this could run (SIGKILL): objects whose ``OWNER`` process is gone on this host. ``docker_session.labels`` puts ``RUN`` on a suite's resources (a lane's proxy, its rig
network) too, so ``reap_run`` also takes those of an xdist worker killed before its own exit handlers ran. ``tests/conftest.py`` calls them at session finish and on every way out of the process.
"""

from __future__ import annotations

import contextlib
import functools
import os
import signal
import socket
import sys
import uuid
from typing import TYPE_CHECKING, Any

from tests.support import docker_session

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

RUN = docker_session.RUN
OWNER = docker_session.OWNER
RUN_ENV = docker_session.RUN_ENV
OWNER_ENV = docker_session.OWNER_ENV
_WRAPPED = "_pxh_sandbox_reaper"
# A client call that hangs must not keep a dying process alive.
_CLIENT_TIMEOUT_S = 30


def _new_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def run_id() -> str:
    """This invocation's id; the first pytest process sets it, its workers and their children inherit it."""
    return os.environ.setdefault(RUN_ENV, _new_id())


def owns_run() -> bool:
    """Whether this process started the run (the xdist controller, or a run without xdist)."""
    host, _, rest = run_id().partition(":")
    return host == socket.gethostname() and rest.partition(":")[0] == str(os.getpid())


def start_owner() -> str:
    """Called by each pytest process: it owns what it and the processes it spawns make from now on."""
    owner = _new_id()
    os.environ[OWNER_ENV] = owner
    _ = run_id()
    return owner


def object_labels() -> dict[str, str]:
    return {
        RUN: run_id(),
        OWNER: os.environ.get(OWNER_ENV) or start_owner(),
        docker_session.USER: docker_session._user(),  # pyright: ignore[reportPrivateUsage]
    }


def is_broker_managed(labels: object) -> bool:
    """A broker's object: one of its namespace's ``<namespace>.managed=1`` (``DockerSandboxBroker``'s labels)."""
    if not isinstance(labels, dict):
        return False
    return any(
        str(key).startswith("io.pipeshub.") and str(key).endswith(".managed") and value == "1"
        for key, value in labels.items()
    )


def _labelling(create: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(create)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - docker-py's signatures
        labels: Any = kwargs.get("labels")
        if is_broker_managed(labels):
            kwargs["labels"] = {**labels, **object_labels()}
        return create(self, *args, **kwargs)

    setattr(wrapper, _WRAPPED, True)
    return wrapper


def label_broker_objects() -> None:
    """Every broker object this process creates from now on carries ``object_labels()``; idempotent."""
    from docker.api.client import APIClient

    for name in ("create_container", "create_network", "create_volume"):
        create = getattr(APIClient, name)
        if not getattr(create, _WRAPPED, False):
            setattr(APIClient, name, _labelling(create))


def _client() -> Any:  # noqa: ANN401 - docker.DockerClient
    import docker

    return docker.from_env(timeout=_CLIENT_TIMEOUT_S)


def _remove(client: Any, label: str, value: str) -> list[str]:  # noqa: ANN401 - docker.DockerClient
    """The containers, then networks (anything still attached is disconnected first), then volumes carrying
    ``label=value`` and this user's label; the names removed."""
    api = client.api
    filters = {"label": [f"{label}={value}", f"{docker_session.USER}={docker_session._user()}"]}  # pyright: ignore[reportPrivateUsage]
    removed: list[str] = []
    for info in api.containers(all=True, filters=filters):
        with contextlib.suppress(Exception):
            if docker_session.remove_container(api, info["Id"], v=True):
                removed.append(((info.get("Names") or ["?"])[0]).lstrip("/"))
    for network in api.networks(filters=filters) or []:
        with contextlib.suppress(Exception):
            attached = api.inspect_network(network["Id"]).get("Containers") or {}
            for container in attached:
                with contextlib.suppress(Exception):
                    api.disconnect_container_from_network(container, network["Id"], force=True)
            api.remove_network(network["Id"])
            removed.append(network["Name"])
    for volume in (api.volumes(filters=filters) or {}).get("Volumes") or []:
        with contextlib.suppress(Exception):
            api.remove_volume(volume["Name"], force=True)
            removed.append(volume["Name"])
    return removed


def _reap(label: str, value: str | None, client: Any = None) -> list[str]:  # noqa: ANN401
    if not value:
        return []
    try:
        own = client or _client()
    except Exception:
        return []
    try:
        removed = _remove(own, label, value)
    except Exception:
        return []
    finally:
        if client is None:
            own.close()
    if removed and sys.__stderr__ is not None:
        # A leak, said where a gate's log shows it: a lane's own teardown should have removed these.
        with contextlib.suppress(Exception):
            sys.__stderr__.write(f"\nsandbox_reaper: removed {len(removed)} object(s) left by {value}: {', '.join(removed)}\n")
            sys.__stderr__.flush()
    return removed


def reap_owned(client: Any = None) -> list[str]:  # noqa: ANN401
    """What this process (and the workers it spawned) made and left; never raises."""
    return _reap(OWNER, os.environ.get(OWNER_ENV), client)


def reap_run(client: Any = None) -> list[str]:  # noqa: ANN401
    """What any process of this run left; only the run's own process calls it, once its xdist workers are down."""
    return _reap(RUN, os.environ.get(RUN_ENV), client) if owns_run() else []


def dead_owners(client: Any) -> set[str]:  # noqa: ANN401 - docker.DockerClient
    """Owners of this user's run-labelled objects whose process is gone on this host (``docker_session``'s rule: a
    pid check here, another host's owner is never judged gone)."""
    api = client.api
    filters = {"label": [RUN, f"{docker_session.USER}={docker_session._user()}"]}  # pyright: ignore[reportPrivateUsage]
    listed = [
        *(c.get("Labels") or {} for c in api.containers(all=True, filters=filters)),
        *(n.get("Labels") or {} for n in api.networks(filters=filters) or []),
        *(v.get("Labels") or {} for v in (api.volumes(filters=filters) or {}).get("Volumes") or []),
    ]
    owners = {str(labels.get(OWNER, "")) for labels in listed} - {"", os.environ.get(OWNER_ENV, "")}
    return {owner for owner in owners if docker_session._gone(owner)}  # pyright: ignore[reportPrivateUsage]


def reap_dead_runs(client: Any = None) -> list[str]:  # noqa: ANN401
    """What an earlier run left whose owning process is gone (a run SIGKILLed before any exit handler ran): only
    objects carrying ``RUN`` and a dead ``OWNER``; a live owner's and unlabelled objects are never touched. Never
    raises."""
    try:
        own = client or _client()
    except Exception:
        return []
    try:
        removed: list[str] = []
        for owner in sorted(dead_owners(own)):
            removed += _reap(OWNER, owner, own)
        return removed
    except Exception:
        return []
    finally:
        if client is None:
            own.close()


def sweep_dead_runs_in_background() -> None:
    """``reap_dead_runs`` off the main thread, so a session never waits on the daemon to start."""
    import threading

    threading.Thread(target=reap_dead_runs, name="sandbox_reaper dead runs", daemon=True).start()


def finalize() -> None:
    """Everything this process, and when it owns the run, the whole run left behind; never raises."""
    _ = reap_owned()
    _ = reap_run()


def _terminate(signum: int, frame: FrameType | None) -> None:
    """The first SIGTERM ends the run as Ctrl-C does (teardown, session finish, ``finalize``); a second one ends the
    process at once, after the exit handlers (``finalize`` among them)."""
    del frame
    signal.signal(signum, _terminate_now)
    raise KeyboardInterrupt


def _terminate_now(signum: int, frame: FrameType | None) -> None:
    del frame
    docker_session.run_exit_handlers()
    os._exit(128 + signum)


def install_sigterm() -> None:
    """For the run's own process, unless something else handles SIGTERM already."""
    with contextlib.suppress(ValueError):
        if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
            _ = signal.signal(signal.SIGTERM, _terminate)


def die_with_parent() -> None:
    """``preexec_fn`` of a worker process a test spawns: the kernel kills it when the thread that spawned it ends
    (Linux ``PR_SET_PDEATHSIG``), so a worker never outlives a test process killed by pid or by ``os._exit`` and
    goes on acquiring sandboxes after its parent's teardown and reaper have run. Elsewhere a no-op."""
    if not sys.platform.startswith("linux"):
        return
    import ctypes

    pr_set_pdeathsig = 1
    _ = ctypes.CDLL(None, use_errno=True).prctl(pr_set_pdeathsig, signal.SIGKILL)
    if os.getppid() == 1:
        # The parent died between the fork and the prctl: init adopted this child already.
        os._exit(1)


def install_in_worker_process() -> None:
    """In a process a test spawned to act as a worker (a broker, an attempt): label what it makes for the test
    process that spawned it, which removes them. The worker never reaps: its sandboxes outlive it by design."""
    if os.environ.get(RUN_ENV):
        label_broker_objects()


__all__ = [
    "OWNER",
    "OWNER_ENV",
    "RUN",
    "RUN_ENV",
    "dead_owners",
    "die_with_parent",
    "finalize",
    "install_in_worker_process",
    "install_sigterm",
    "is_broker_managed",
    "label_broker_objects",
    "object_labels",
    "owns_run",
    "reap_owned",
    "reap_dead_runs",
    "reap_run",
    "run_id",
    "start_owner",
    "sweep_dead_runs_in_background",
]
