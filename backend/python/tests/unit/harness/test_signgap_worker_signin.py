"""SIGNGAP: the worker says whether it can carry a person's own vendor sign-in, and only then is one pinned.

The worker had no sign-in tunnels (``EGRESS_CONNECT_TUNNELS`` off), yet the settings offered the
Claude sign-in; a person turned it on and every Claude Code task of theirs failed inside the engine with
``signin_unavailable``. The worker now advertises ``vendor_signin`` exactly when its engine controller would admit a
run pinned on the actor's own plan (the relay, the proxy's tunnels and the release's signed host list), so Node pins
one only where it can run, and otherwise admits the task on the org's model access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.engines import worker as engine_worker
from app.harness.engines.worker import carries_sign_ins, sign_in_hosts
from app.harness.worker.capabilities import (
    VENDOR_SIGNIN,
    WORKER_CAPABILITIES,
    advertised,
)
from harness_core.engines.signin import SignInHosts, SignInRefused
from tests.unit.harness_core.engines.test_controller import task
from tests.unit.harness_core.engines.test_controller_signin import (
    OWN,
    FakeRelay,
    _world,
)

if TYPE_CHECKING:
    from tests.unit.harness_core.engines.test_controller import World

HOSTS = SignInHosts(sandbox=("api.anthropic.com", "platform.claude.com"), link=frozenset({"claude.com"}))


def _features(*, engines: bool, signin: bool) -> set[str]:
    return {f.root for f in advertised(engines=engines, signin=signin).features}


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_worker, "release_sign_in_hosts", lambda: HOSTS)


@pytest.mark.parametrize(
    ("tunnels", "relay", "hosts", "carried"),
    [
        ("true", True, HOSTS, True),
        ("false", True, HOSTS, False),
        ("", True, HOSTS, False),
        ("true", False, HOSTS, False),
        ("true", True, None, False),
    ],
)
def test_sg1_the_worker_carries_sign_ins_only_with_the_relay_the_tunnels_and_the_signed_hosts(
    monkeypatch: pytest.MonkeyPatch, tunnels: str, relay: bool, hosts: SignInHosts | None, carried: bool
) -> None:
    monkeypatch.setenv("EGRESS_CONNECT_TUNNELS", tunnels)
    monkeypatch.setattr(engine_worker, "release_sign_in_hosts", lambda: hosts)
    assert carries_sign_ins(relay=relay) is carried
    assert (sign_in_hosts(relay=relay) is not None) is carried
    assert (VENDOR_SIGNIN in _features(engines=True, signin=carries_sign_ins(relay=relay))) is carried


def test_sg2_vendor_signin_is_a_feature_of_the_build_withheld_unless_the_process_carries_it() -> None:
    assert VENDOR_SIGNIN == "vendor_signin"
    assert VENDOR_SIGNIN in {f.root for f in WORKER_CAPABILITIES.features}
    assert VENDOR_SIGNIN in _features(engines=True, signin=True)
    assert VENDOR_SIGNIN not in _features(engines=True, signin=False)
    # No engine kernel: no engine run at all, so no sign-in either, whatever the process could carry.
    assert VENDOR_SIGNIN not in _features(engines=False, signin=True)
    # Nothing else changes with it.
    assert _features(engines=True, signin=True) - {VENDOR_SIGNIN} == _features(engines=True, signin=False)


def _controller_world(*, relay: bool) -> World:
    """The engine controller this worker builds: the sign-in hosts are the ones ``engine_services`` hands it."""
    return _world(relay=FakeRelay() if relay else None, hosts=sign_in_hosts(relay=relay))


async def test_sg3_a_claude_code_child_on_its_actors_plan_without_tunnels_fails_in_the_engine(
    monkeypatch: pytest.MonkeyPatch, signed: None
) -> None:
    """The recorded failure, reproduced at the controller: sign-in on, no tunnels. Such a run is refused deep in the
    engine, so the worker must say it cannot carry one; Node then never pins it (the Node half of SIGNGAP)."""
    monkeypatch.setenv("EGRESS_CONNECT_TUNNELS", "false")
    world = _controller_world(relay=True)
    with pytest.raises(SignInRefused) as refused:
        _ = await world.controller.run_turn(world.run(own_login=OWN), task(0, "say done"))
    assert refused.value.reason_code == "signin_unavailable"
    assert world.broker.acquired == []
    assert VENDOR_SIGNIN not in _features(engines=True, signin=carries_sign_ins(relay=True))
    # The same worker's org-access run (what Node admits instead) runs.
    org = _controller_world(relay=True)
    outcome = await org.controller.run_turn(org.run(), task(0, "say done"))
    assert outcome.reason == "completed"


async def test_sg4_with_tunnels_on_nothing_changes(monkeypatch: pytest.MonkeyPatch, signed: None) -> None:
    monkeypatch.setenv("EGRESS_CONNECT_TUNNELS", "true")
    assert VENDOR_SIGNIN in _features(engines=True, signin=carries_sign_ins(relay=True))
    world = _controller_world(relay=True)
    outcome = await world.controller.run_turn(world.run(own_login=OWN), task(0, "say done"))
    assert (outcome.reason, outcome.final_text) == ("completed", "done")
