"""WP84-WORDS: a run that fails ``mcp_tool_withdrawn`` carries the labels of the servers that withdrew the tool, as data
beside its English message, so the web app words the failure in the reader's language and still names the server
(WP-79 open item 1: no locale string could name the server while only the worker's English carried the label)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.journal_node.client import transition_wire
from harness_core.contracts.generated.events.run_failed import RunFailedData
from harness_core.failpoints import Raise, registry
from harness_core.journal.types import Fail
from harness_core.kernel.durable.capability import TOOL_WITHDRAWN, withdrawn_mark

from .support import Crash, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

SEARCH = "tool:mcp@inst-1/search"
LIST = "tool:mcp@inst-2/list"
DIGEST = "sha256:" + "a" * 64


class _Observed:
    def __init__(self, value: dict[str, JsonValue]) -> None:
        self.value = value

    async def observe(self) -> Mapping[str, JsonValue]:
        return dict(self.value)


async def test_wp84_words_1_the_failure_carries_every_withdrawing_server_by_its_label() -> None:
    world = World()
    observed = _Observed({SEARCH: DIGEST, LIST: DIGEST})
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    registry.arm("worker.model.after_response_commit", Raise(Crash))
    await world.crash(world.kernel(port, capability_check=observed))
    registry.reset()

    observed.value = {SEARCH: withdrawn_mark("Atlassian"), LIST: withdrawn_mark("GitHub")}
    final = await world.run(world.kernel(port, capability_check=observed))

    assert (final.status, final.reason) == ("failed", TOOL_WITHDRAWN)
    (failed,) = world.events("run.failed")
    assert failed.data["servers"] == ["Atlassian", "GitHub"]
    assert "Atlassian and GitHub" in failed.data["message"]


def test_wp84_words_2_the_wire_and_the_contract_carry_the_servers() -> None:
    wire = transition_wire(Fail(TOOL_WITHDRAWN, "Atlassian no longer offers…", servers=("Atlassian",)))
    assert wire == {
        "status": "failed",
        "errorType": TOOL_WITHDRAWN,
        "detail": {"kind": "fail", "message": "Atlassian no longer offers…", "servers": ["Atlassian"]},
    }
    assert "servers" not in transition_wire(Fail("engine_failed"))["detail"]  # type: ignore[operator]
    data = RunFailedData.model_validate({"errorType": TOOL_WITHDRAWN, "servers": ["Atlassian"]})
    assert [label.root for label in data.servers or ()] == ["Atlassian"]
