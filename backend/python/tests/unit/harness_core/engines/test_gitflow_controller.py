"""GIT-FLOW in the engine controller (GT-01, SEC-EG-11 / C6.10): the setup phase runs, and is switched off, before
the engine starts.

The fake broker stands in for the sandbox; its ``sh`` answers the clone and the check as the real scripts would
(the scripts themselves run against real git in ``test_gitflow_git.py``). What is held here is the order: the
sandbox is created under the setup policy, cloned into, re-bound to the agent policy, checked, and only then is the
engine launched, with the push placeholder and never the setup one. A workspace that already holds the clone skips
the setup phase, so a later turn never sees the read credential's route at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.git_checkout import (
    CLONE_SCRIPT,
    VERIFY_SCRIPT,
    GitCheckout,
    GitSetupFailed,
    basic_auth,
    session_branch,
)
from harness_core.sandbox.types import CredentialInjection, GitAccess
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS
from tests.unit.harness_core.engines.test_controller import (
    SESSION,
    WatchingAdapter,
    World,
    task,
)

if TYPE_CHECKING:
    from harness_core.testing.sandbox.fake import FakeProcessContext

URL = "https://git.example.test/acme/widgets.git"
HEAD = "c" * 40


class PhaseRefs:
    """Stands in for sealing: one ref per phase, recording the placeholder each was asked for."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def credential_ref(self, phase: str, placeholder: str) -> str:
        self.asked.append((phase, placeholder))
        return {"setup": "sealed-read", "agent": "sealed-push"}[phase]


class LaunchAdapter(WatchingAdapter):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.envs: list[dict[str, str]] = []

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.envs.append(dict(launch.env))
        return await super().open(sandbox, launch, restore, correlation=correlation)


class GitWorld:
    def __init__(self, *, clone_exit: int = 0, residue: bool = False) -> None:
        self.world = World(adapter=LaunchAdapter())
        self.steps: list[tuple[str, int]] = []
        self.envs: list[dict[str, str]] = []
        shell = FAKE_PROGRAMS["sh"]

        async def sh(context: FakeProcessContext) -> int:
            argv = context.argv
            script = argv[2] if len(argv) > 2 and argv[1] == "-c" else ""
            acquired = len(self.world.broker.acquired)
            if script.endswith(CLONE_SCRIPT):
                self.steps.append(("clone", acquired))
                self.envs.append(context.env)
                if clone_exit:
                    await context.emit(b"fatal: repository not found\n", stderr=True)
                    return clone_exit
                context.write_file(f"{argv[4]}/.git/HEAD", f"ref: refs/heads/{argv[6]}\n".encode())
                await context.emit(f"{HEAD}\n".encode())
                return 0
            if script == VERIFY_SCRIPT:
                self.steps.append(("verify", acquired))
                return 4 if residue else 0
            if "gitmodules" in " ".join(argv):
                return 0
            if script.endswith("|| exit $?; exit 0"):
                self.steps.append(("trust", acquired))
                return 0
            return await shell(context)

        self.world.broker.programs["sh"] = sh
        self.checkout = GitCheckout(
            url=URL, session_branch=session_branch(SESSION), credentials=PhaseRefs()
        )

    @property
    def adapter(self) -> LaunchAdapter:
        assert isinstance(self.world.adapter, LaunchAdapter)
        return self.world.adapter

    async def turn(self, index: int = 0, attempt: int = 1) -> str:
        outcome = await self.world.controller.run_turn(self.world.run(attempt, checkout=self.checkout), task(index, "say ok"))
        return outcome.reason


def _git_destination(spec):  # type: ignore[no-untyped-def] # noqa: ANN202
    [destination] = [d for d in spec.egress.destinations if d.git is not None]
    return destination


async def test_gt01_sec_eg_11_setup_then_switch_then_check_then_engine() -> None:
    git = GitWorld()
    assert await git.turn() == "completed"

    [(setup, key1), (agent, key2)] = git.world.broker.acquired
    assert key1 == key2, "the agent phase is the same sandbox, re-bound"
    assert git.steps == [("clone", 1), ("verify", 2)]
    assert _git_destination(setup).git == GitAccess("acme/widgets")
    # GITF-19 (WP-63 rule): the agent phase fetches only, with the read credential; the push credential reaches
    # the proxy only in a one-push grant a person's approval registers.
    assert _git_destination(agent).git == GitAccess("acme/widgets")
    setup_routes = {r.placeholder: r for r in setup.egress.token_routes if r.injection is CredentialInjection.BASIC}
    agent_routes = {r.placeholder: r for r in agent.egress.token_routes if r.injection is CredentialInjection.BASIC}
    assert {r.credential_ref for r in setup_routes.values()} == {"sealed-read"}
    assert {r.credential_ref for r in agent_routes.values()} == {"sealed-read"}
    [setup_ph] = setup_routes
    [push_ph] = agent_routes
    assert setup_ph != push_ph
    # Each credential is sealed for the one placeholder its route carries (the proxy opens it only there).
    assert isinstance(git.checkout.credentials, PhaseRefs)
    assert sorted(git.checkout.credentials.asked) == sorted([("setup", push_ph), ("setup", setup_ph)])
    # Only the gateways' routes and the git route differ between the phases.
    assert {d.host for d in setup.egress.destinations} == {d.host for d in agent.egress.destinations}

    # The clone saw only the setup placeholder; the engine only its fetch placeholder, which can never push.
    [clone_env] = git.envs
    assert clone_env["PXH_GIT_AUTH"] == basic_auth(setup_ph)
    assert push_ph not in " ".join(clone_env.values()) and "sealed-read" not in " ".join(clone_env.values())
    [engine_env] = git.adapter.envs
    joined = " ".join(engine_env.values())
    assert setup_ph not in joined and "sealed" not in joined
    assert engine_env["GIT_CONFIG_KEY_4"] == f"http.{URL}.extraHeader"
    assert engine_env["GIT_CONFIG_VALUE_4"] == f"Authorization: Basic {basic_auth(push_ph)}"
    assert (engine_env["GIT_CONFIG_KEY_0"], engine_env["GIT_CONFIG_VALUE_0"]) == ("http.proxy", "http://egress-proxy:8097")
    assert basic_auth(setup_ph) not in joined
    # Only git goes through the proxy by name; the engine's own traffic keeps its route.
    assert "HTTPS_PROXY" not in engine_env and "NODE_USE_ENV_PROXY" not in engine_env


async def test_gt01_a_workspace_holding_the_clone_skips_the_setup_phase() -> None:
    git = GitWorld()
    await git.world.seed({"repo/.git/HEAD": b"ref: refs/heads/x\n"})
    assert await git.turn() == "completed"
    [(spec, _)] = git.world.broker.acquired
    assert git.steps == [("trust", 1)]
    # The agent phase alone: its fetch route with the read credential; no push credential ever (GITF-19).
    assert _git_destination(spec).git == GitAccess("acme/widgets")
    assert {r.credential_ref for r in spec.egress.token_routes if r.injection is CredentialInjection.BASIC} == {"sealed-read"}


async def test_gt01_the_next_turn_finds_the_clone_the_first_saved() -> None:
    git = GitWorld()
    assert await git.turn(0, attempt=1) == "completed"
    assert "repo/.git/HEAD" in await git.world.head_files()
    git.world.broker.acquired.clear()
    assert await git.turn(1, attempt=2) == "completed"
    assert len(git.world.broker.acquired) == 1 and git.steps == [("clone", 1), ("verify", 2), ("trust", 1)]


@pytest.mark.parametrize(("clone_exit", "residue", "reason"), [(128, False, "git_clone_failed"), (0, True, "git_setup_residue")])
async def test_gt01_a_failed_setup_never_starts_the_engine(clone_exit: int, residue: bool, reason: str) -> None:
    git = GitWorld(clone_exit=clone_exit, residue=residue)
    with pytest.raises(GitSetupFailed) as failed:
        _ = await git.turn()
    assert failed.value.reason_code == reason
    assert git.adapter.envs == []
    assert len(git.world.broker.released) == 1


async def test_a_run_without_a_repository_is_unchanged() -> None:
    world = World()
    _ = await world.controller.run_turn(world.run(), task(0, "say ok"))
    [(spec, _)] = world.broker.acquired
    assert all(d.git is None for d in spec.egress.destinations)
