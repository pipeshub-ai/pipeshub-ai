"""C1.03: which runtime the engines' real lanes put their sandboxes on (``HARNESS_TEST_SANDBOX_RUNTIME``).

Unset, a lane runs where production would (the strongest runtime the daemon offers); naming one lets a single host
compare gVisor with runc, and a name the lane does not know fails rather than quietly running on runc."""

from __future__ import annotations

import re

import pytest

from harness_core.sandbox.types import RuntimeMode
from tests.cohesion.support import REPO_ROOT
from tests.unit.harness.engines import real_stack
from tests.unit.harness.engines.real_stack import RUNTIME_ENV, lane_mode


@pytest.fixture
def daemon(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv(RUNTIME_ENV, raising=False)
    return monkeypatch


@pytest.mark.parametrize(("runsc", "expected"), [(True, RuntimeMode.GVISOR), (False, RuntimeMode.DOCKER_HARDENED)])
def test_unset_the_lane_runs_on_the_strongest_runtime_the_daemon_offers(
    daemon: pytest.MonkeyPatch, runsc: bool, expected: RuntimeMode
) -> None:
    daemon.setattr(real_stack, "daemon_has_runsc", lambda: runsc)
    assert lane_mode() is expected


@pytest.mark.parametrize(
    ("named", "expected"),
    [("runc", RuntimeMode.DOCKER_HARDENED), ("runsc", RuntimeMode.GVISOR), (" gVisor ", RuntimeMode.GVISOR)],
)
def test_a_named_runtime_wins_over_what_the_daemon_offers(
    daemon: pytest.MonkeyPatch, named: str, expected: RuntimeMode
) -> None:
    daemon.setattr(real_stack, "daemon_has_runsc", lambda: True)
    daemon.setenv(RUNTIME_ENV, named)
    assert lane_mode() is expected


def test_an_unknown_runtime_name_fails_the_lane_instead_of_running_it_on_runc(daemon: pytest.MonkeyPatch) -> None:
    daemon.setenv(RUNTIME_ENV, "kata")
    with pytest.raises(ValueError, match="expected one of"):
        _ = lane_mode()


def test_auto_is_the_strongest_runtime_as_unset_is(daemon: pytest.MonkeyPatch) -> None:
    daemon.setattr(real_stack, "daemon_has_runsc", lambda: True)
    daemon.setenv(RUNTIME_ENV, "auto")
    assert lane_mode() is RuntimeMode.GVISOR


def test_r31gveng_fu4_every_word_of_the_stacks_switch_names_the_same_runtime_on_the_pytest_lanes(
    daemon: pytest.MonkeyPatch,
) -> None:
    """One switch (R31GVENG-FU-4): run.sh's ``E2E_SANDBOX_RUNTIME`` words are the pytest lanes' words, so the runc side
    of a comparison needs no lane-local override (R31-GVENG's ``docker`` shim and ``broker_runc.py`` are gone)."""
    run_sh = (REPO_ROOT / "deployment" / "e2e" / "run.sh").read_text(encoding="utf-8")
    body = run_sh[run_sh.index("\nsandbox_runtime_env() {") :]
    body = body[: body.index("\n}\n")]
    words = set(re.findall(r"^\s+([a-z]+)\)", body, re.MULTILINE))
    assert words == {"auto", "runsc", "runc"}, words
    daemon.setattr(real_stack, "daemon_has_runsc", lambda: True)
    expected = {"auto": RuntimeMode.GVISOR, "runsc": RuntimeMode.GVISOR, "runc": RuntimeMode.DOCKER_HARDENED}
    for word in words:
        daemon.setenv(RUNTIME_ENV, word)
        assert lane_mode() is expected[word], word
