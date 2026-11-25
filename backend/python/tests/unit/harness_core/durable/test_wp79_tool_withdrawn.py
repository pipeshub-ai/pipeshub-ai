"""WP79-LOST: a run that had a tool on an earlier attempt, whose server's listing answered without it since, fails at
once with words that name the server; an outage (no withdrawal mark) keeps the bounded re-checks (PA-7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.outcome.outcome import Delivered, Failed
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.failpoints import Raise, registry
from harness_core.kernel.durable.capability import (
    NEEDS_CONNECT,
    TOOL_WITHDRAWN,
    capability_changes,
    returned,
    withdrawn_mark,
    withdrawn_servers,
)
from harness_core.testing.durable_ports import StaticCapabilityCheck

from .support import RUN, Crash, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

SEARCH = "tool:mcp@inst-1/search"
DIGEST = "sha256:" + "a" * 64


class _Observed:
    def __init__(self, value: dict[str, JsonValue]) -> None:
        self.value = value

    async def observe(self) -> Mapping[str, JsonValue]:
        return dict(self.value)


async def _recorded(world: World, observed: _Observed | StaticCapabilityCheck) -> object:
    """The first attempt records ``observed`` and crashes after its first model response."""
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    registry.arm("worker.model.after_response_commit", Raise(Crash))
    await world.crash(world.kernel(port, capability_check=observed))
    registry.reset()
    return port


async def test_wp79_lost_1_a_tool_the_run_had_that_its_server_withdrew_fails_at_once_naming_the_server() -> None:
    world = World()
    observed = _Observed({SEARCH: DIGEST, "mcp:inst-1": DIGEST})
    port = await _recorded(world, observed)

    observed.value = {SEARCH: withdrawn_mark("Atlassian"), "mcp:inst-1": "sha256:" + "b" * 64}
    final = await world.run(world.kernel(port, capability_check=observed))

    assert (final.status, final.reason) == ("failed", TOOL_WITHDRAWN)
    assert world.events("run.yielded") == []
    (failed,) = world.events("run.failed")
    assert failed.data["errorType"] == TOOL_WITHDRAWN
    assert "Atlassian" in failed.data["message"]
    assert "mcp@" not in failed.data["message"] and "inst-1" not in failed.data["message"]
    assert world.journal.status(RUN) == "failed"


async def test_wp79_lost_2_a_tool_withheld_since_the_first_attempt_is_no_change() -> None:
    world = World()
    observed = _Observed({SEARCH: withdrawn_mark("Atlassian"), "mcp:inst-1": DIGEST})
    port = await _recorded(world, observed)

    done = await world.run(world.kernel(port, capability_check=observed))

    assert done.status == "completed"
    assert world.events("run.failed") == []


def test_wp79_lost_3_only_a_withdrawal_mark_names_a_server() -> None:
    recorded = {SEARCH: DIGEST, "tool:mcp@inst-2/list": DIGEST}
    observed = {SEARCH: withdrawn_mark("Atlassian"), "tool:mcp@inst-2/list": "sha256:" + "c" * 64}
    changed = capability_changes(recorded, observed)
    assert changed == sorted(recorded)
    assert withdrawn_servers(observed, changed) == ["Atlassian"]
    assert withdrawn_servers({SEARCH: "sha256:" + "c" * 64}, [SEARCH]) == []


def withdrawn_message(servers: list[str]) -> str:
    """WP-89: the words are the outcome presenter's, from the typed failed outcome."""
    return PRESENTER.failure(Failed(TOOL_WITHDRAWN, Delivered(), servers=tuple(servers)))


def test_wp79_lost_4_the_words_name_every_server_plainly() -> None:
    assert withdrawn_message(["Atlassian"]).startswith("Atlassian no longer offers a tool this task was using")
    assert withdrawn_message(["Atlassian", "GitHub"]).startswith("Atlassian and GitHub no longer")
    assert withdrawn_message(["A", "B", "C"]).startswith("A, B and C no longer")
    assert "again" in withdrawn_message(["A"]) and "admin" in withdrawn_message(["A"])


def test_wp79_return_k1_only_a_recorded_withdrawal_that_is_listed_again_has_returned() -> None:
    mark = withdrawn_mark("Atlassian")
    recorded = {SEARCH: mark, "tool:mcp@inst-1/list": DIGEST, "tool:mcp@inst-1/gone": mark}
    observed = {SEARCH: DIGEST, "tool:mcp@inst-1/list": "sha256:" + "b" * 64, "tool:mcp@inst-1/gone": mark}
    assert returned(recorded, observed) == {SEARCH: mark}
    assert returned(recorded, {SEARCH: NEEDS_CONNECT}) == {SEARCH: mark}
    assert returned(recorded, {}) == {}


async def test_wp79_return_k2_the_kernel_withholds_a_returned_tool_and_the_run_goes_on() -> None:
    world = World()
    check = StaticCapabilityCheck({SEARCH: withdrawn_mark("Atlassian"), "mcp:inst-1": DIGEST})
    port = await _recorded(world, check)

    check.digests[SEARCH] = DIGEST
    done = await world.run(world.kernel(port, capability_check=check))

    assert done.status == "completed"
    assert check.withheld == {SEARCH: withdrawn_mark("Atlassian")}
    assert world.events("run.yielded") == [] and world.events("run.failed") == []
