"""BUGS-A: the assumptions of the test suites' Docker reaper (``tests/support/docker_session.py``), on a fake daemon.

Each case pins one assumption ``reap_stale`` and ``remove_session`` make, and what they must never do on a daemon
shared with other users and other agents: touch a resource that is not ``pxh-*``, another user's, or a live
session's that is still within its lifetime.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import docker.errors
import pytest
import requests

from tests.support import docker_session

NOW = 1_800_000_000
SUITE = "bugs-a-fake"


def _iso(epoch: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def _dead_session() -> str:
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    _ = exited.wait()
    return f"{docker_session._HOST}:{exited.pid}:dead"  # pyright: ignore[reportPrivateUsage]


class _Daemon:
    """Containers, volumes, networks and images by name, each with labels and a creation time; filters by label
    equality as Docker does."""

    def __init__(self, now: int = NOW) -> None:
        self.now = now
        self.items: dict[str, dict[str, tuple[dict[str, str], int]]] = {
            "container": {}, "volume": {}, "network": {}, "image": {}
        }
        self.removed: list[str] = []

    def add(self, kind: str, name: str, labels: dict[str, str], *, age_s: int = 3600) -> str:
        self.items[kind][name] = (labels, self.now - age_s)
        return name

    def info(self) -> dict[str, Any]:
        return {"SystemTime": _iso(self.now)}

    def _match(self, kind: str, filters: dict[str, list[str]]) -> list[tuple[str, dict[str, str], int]]:
        wanted = [f.split("=", 1) for f in filters.get("label", [])]
        return [
            (name, labels, created)
            for name, (labels, created) in self.items[kind].items()
            if all(labels.get(k) == v for k, v in wanted)
        ]

    def containers(self, *, all: bool, filters: dict[str, list[str]]) -> list[dict[str, Any]]:  # noqa: A002
        return [{"Id": f"id-{n}", "Names": [f"/{n}"], "Labels": lb, "Created": c} for n, lb, c in self._match("container", filters)]

    def volumes(self, *, filters: dict[str, list[str]]) -> dict[str, Any]:
        return {"Volumes": [{"Name": n, "Labels": lb, "CreatedAt": _iso(c)} for n, lb, c in self._match("volume", filters)]}

    def networks(self, *, filters: dict[str, list[str]]) -> list[dict[str, Any]]:
        return [{"Id": f"id-{n}", "Name": n, "Labels": lb, "Created": _iso(c)} for n, lb, c in self._match("network", filters)]

    def images(self, *, filters: dict[str, list[str]]) -> list[dict[str, Any]]:
        return [{"Id": f"id-{n}", "RepoTags": [n], "Labels": lb, "Created": c} for n, lb, c in self._match("image", filters)]

    def _drop(self, kind: str, ref: str) -> None:
        name = ref.removeprefix("id-")
        del self.items[kind][name]
        self.removed.append(name)

    def remove_container(self, ref: str, **_: Any) -> None:  # noqa: ANN401
        self._drop("container", ref)

    def remove_volume(self, ref: str, **_: Any) -> None:  # noqa: ANN401
        self._drop("volume", ref)

    def remove_network(self, ref: str) -> None:
        self._drop("network", ref)

    def remove_image(self, ref: str, **_: Any) -> None:  # noqa: ANN401
        self._drop("image", ref)


class _Client:
    def __init__(self, api: Any) -> None:  # noqa: ANN401
        self.api = api


class _Provider:
    """``DockerClientProvider``'s shape: what the broker fixtures' ``cleanup`` takes."""

    def __init__(self, api: Any) -> None:  # noqa: ANN401
        self.client = _Client(api)


def _own(**overrides: str) -> dict[str, str]:
    return {**docker_session.labels(SUITE), **overrides}


def test_a_dead_sessions_container_volume_network_and_image_are_reaped() -> None:
    daemon = _Daemon()
    dead = _own(**{docker_session.SESSION: _dead_session()})
    for kind in ("container", "volume", "network", "image"):
        daemon.add(kind, f"pxh-dead-{kind}", dead)
    assert docker_session.reap_stale(_Client(daemon), SUITE) == 1
    assert sorted(daemon.removed) == ["pxh-dead-container", "pxh-dead-image", "pxh-dead-network", "pxh-dead-volume"]


def test_a_live_session_past_its_lifetime_is_reaped_and_one_within_it_is_kept() -> None:
    """A pid the kernel gave to another process, or a hung run, keeps its session 'alive' for ever: the lifetime
    label bounds the leak (E2E4's Arango containers ran for days)."""
    daemon = _Daemon()
    lifetime = int(_own()[docker_session.LIFETIME])
    live_elsewhere = {docker_session.SESSION: "elsewhere:1:x"}
    expired = daemon.add("container", "pxh-expired", _own(**live_elsewhere), age_s=lifetime + 60)
    fresh = daemon.add("container", "pxh-fresh", _own(**live_elsewhere), age_s=lifetime - 60)
    expired_net = daemon.add("network", "pxh-expired-net", _own(**live_elsewhere), age_s=lifetime + 60)
    assert docker_session.reap_stale(_Client(daemon), SUITE) == 1
    assert sorted(daemon.removed) == sorted([expired, expired_net])
    assert fresh in daemon.items["container"]


def test_this_sessions_own_resources_are_never_reaped_whatever_their_age() -> None:
    daemon = _Daemon()
    lifetime = int(_own()[docker_session.LIFETIME])
    daemon.add("container", "pxh-mine", _own(), age_s=lifetime * 10)
    assert docker_session.reap_stale(_Client(daemon), SUITE) == 0


def test_age_is_measured_on_the_daemons_clock_not_this_hosts() -> None:
    """A daemon whose clock runs behind the broker's would otherwise make every resource look old."""
    daemon = _Daemon(now=NOW - 7 * 86400)
    lifetime = int(_own()[docker_session.LIFETIME])
    daemon.add("container", "pxh-young", _own(**{docker_session.SESSION: "elsewhere:1:x"}), age_s=lifetime // 2)
    assert docker_session.reap_stale(_Client(daemon), SUITE) == 0


def test_never_a_resource_outside_pxh_or_of_another_user_or_suite_or_without_labels() -> None:
    daemon = _Daemon()
    dead = {docker_session.SESSION: _dead_session()}
    lifetime = int(_own()[docker_session.LIFETIME])
    kept = [
        daemon.add("container", "someone-elses-db", _own(**dead)),
        daemon.add("volume", "data-volume", _own(**dead)),
        daemon.add("network", "bridge-of-theirs", _own(**dead)),
        daemon.add("image", "redis:7", _own(**dead)),
        daemon.add("container", "pxh-other-user", _own(**dead, **{docker_session.USER: "someone-else"}), age_s=lifetime * 2),
        daemon.add("container", "pxh-other-suite", _own(**dead, **{docker_session.SUITE: "other"}), age_s=lifetime * 2),
        daemon.add("container", "pxh-unlabelled", {"pipeshub.harness.owner": "x-lead"}, age_s=lifetime * 2),
    ]
    assert docker_session.reap_stale(_Client(daemon), SUITE) == 0
    assert daemon.removed == []
    assert all(any(k in items for items in daemon.items.values()) for k in kept)


def test_a_young_dead_session_is_kept_until_the_bound() -> None:
    daemon = _Daemon()
    daemon.add("container", "pxh-young-dead", _own(**{docker_session.SESSION: _dead_session()}), age_s=5)
    assert docker_session.reap_stale(_Client(daemon), SUITE) == 0
    assert docker_session.reap_stale(_Client(daemon), SUITE, older_than_s=0) == 1


def test_the_user_label_is_the_account_not_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``$USER`` differs between a login shell, ``sudo -E`` and CI; the uid's account name does not."""
    import pwd

    monkeypatch.setenv("USER", "spoofed")
    monkeypatch.setenv("LOGNAME", "spoofed")
    assert docker_session.labels(SUITE)[docker_session.USER] == pwd.getpwuid(__import__("os").getuid()).pw_name


def test_the_session_finalizer_survives_a_daemon_that_is_gone() -> None:
    """``atexit`` runs it in every process that labelled something, Docker or not: it must not raise."""

    class _Down:
        def containers(self, **_: Any) -> list[Any]:  # noqa: ANN401
            raise requests.exceptions.ConnectionError("no daemon")

    assert docker_session.remove_session(SUITE, _Client(_Down())) == 0


class _SlowApi:
    """``remove_container`` times out client-side while the daemon goes on removing; ``wait`` reports the end."""

    def __init__(self, *, remove: BaseException | None, wait: BaseException | None) -> None:
        self.remove_error, self.wait_error = remove, wait
        self.waited: list[tuple[str, str]] = []

    def remove_container(self, ref: str, **_: Any) -> None:  # noqa: ANN401
        if self.remove_error is not None:
            raise self.remove_error

    def wait(self, ref: str, *, timeout: int, condition: str) -> dict[str, Any]:
        self.waited.append((ref, condition))
        if self.wait_error is not None:
            raise self.wait_error
        return {"StatusCode": 0}


def _in_progress() -> docker.errors.APIError:
    response = requests.Response()
    response.status_code = 409
    return docker.errors.APIError(
        "409", response=response, explanation="removal of container abc is already in progress"
    )


@pytest.mark.parametrize("slow", [requests.exceptions.ReadTimeout("Read timed out"), _in_progress()], ids=["timeout", "in_progress"])
def test_a_slow_removal_waits_for_the_daemon_rather_than_failing(slow: BaseException) -> None:
    api = _SlowApi(remove=slow, wait=None)
    assert docker_session.remove_container(api, "c1") is True
    assert api.waited == [("c1", "removed")]


def test_a_removal_still_running_after_the_wait_is_reported_not_raised() -> None:
    api = _SlowApi(remove=requests.exceptions.ReadTimeout("t"), wait=requests.exceptions.ReadTimeout("t"))
    assert docker_session.remove_container(api, "c1") is False


def test_a_container_already_gone_is_removed() -> None:
    api = _SlowApi(remove=docker.errors.NotFound("gone"), wait=None)
    assert docker_session.remove_container(api, "c1") is True
    assert api.waited == []


def test_any_other_removal_error_is_raised() -> None:
    response = requests.Response()
    response.status_code = 500
    api = _SlowApi(remove=docker.errors.APIError("500", response=response, explanation="driver failed"), wait=None)
    with pytest.raises(docker.errors.APIError):
        docker_session.remove_container(api, "c1")


class _TeardownApi(_SlowApi):
    """A sandbox the daemon is still removing: its volume and network are in use."""

    def __init__(self) -> None:
        super().__init__(remove=_in_progress(), wait=requests.exceptions.ReadTimeout("t"))

    def containers(self, **_: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return [{"Id": "c1"}]

    def volumes(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"Volumes": [{"Name": "pxh-e0r-x-vol"}]}

    def remove_volume(self, name: str, **_: Any) -> None:  # noqa: ANN401
        raise AssertionError(f"{name}: a volume in use by a container being removed")

    def networks(self, **_: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return [{"Id": "n1"}]

    def disconnect_container_from_network(self, *_: Any, **__: Any) -> None:  # noqa: ANN401
        return None

    def remove_network(self, _: str) -> None:
        raise docker.errors.APIError("403 has active endpoints")


def test_a_teardown_whose_container_the_daemon_is_still_removing_does_not_fail_the_test() -> None:
    """rx5 (verify lane): the broker's removal timed out, the fixture's own remove then got 409 "already in
    progress" and errored the test. Now the fixture waits, and what is still going is retried at exit."""
    from tests.unit.harness.sandbox_broker import conftest

    provider = _Provider(_TeardownApi())
    namespace = "io.pipeshub.bugsa.slow"
    try:
        with pytest.warns(UserWarning, match="still being removed"):
            conftest.cleanup(provider, namespace, "pxh-e0r-x")  # type: ignore[arg-type]
        conftest.cleanup_networks(provider, namespace, "proxy")  # type: ignore[arg-type]
        assert conftest._later == {namespace: "pxh-e0r-x"}  # pyright: ignore[reportPrivateUsage]
    finally:
        _ = conftest._later.pop(namespace, None)  # pyright: ignore[reportPrivateUsage]


def test_a_network_that_will_not_go_for_another_reason_still_fails_the_teardown() -> None:
    from tests.unit.harness.sandbox_broker import conftest

    with pytest.raises(docker.errors.APIError):
        conftest.cleanup_networks(_Provider(_TeardownApi()), "io.pipeshub.bugsa.other", "proxy")  # type: ignore[arg-type]
