"""R32ENGFU-FU-1: a command whose engine can lose its output never reads as having printed nothing.

Qwen Code 0.26.5 reports about one short command in 35 as empty when it printed something (its own ``exit``-before-
``data`` race, R31JFIX-FU-2, upstream). The step then said "No output.", the same words a silent command gets: a false
claim one time in 35. An empty result from such an engine is unverified, so its profile says so
(``outputMayBeLost``), the mapper carries it on the command's start, and the command's record and live start carry it to
the activity panel, which says "No output was captured." (true for a silent command too). No other engine is marked.

* OL-1: Qwen's packaged profile is marked, no other one; the field is a boolean or refused.
* OL-2: the rendered session policy carries it to the mapper, whose command start and progress line carry it.
* OL-3: the command's record and its live start carry it (valid against ``x.pipeshub.command.finished``); an unmarked
  engine's carry nothing.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from typing import Any, cast

import pytest

from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from app.harness.engines.progress import progress_for
from harness_core.activity.types import COMMAND_STARTED, CommandSpec
from harness_core.contracts import validate_contract
from harness_core.engines.events import CommandStarted
from harness_engines.acp.mapper import AcpEventMapper
from harness_engines.acp.permissions import SessionPolicy
from harness_engines.acp.profiles import AcpProfileInvalid, parse_profile
from harness_engines.acp.registry import packaged_acp_agents

from .test_file_changes import POLICY

LOSES = dataclasses.replace(POLICY, output_may_be_lost=True)


def _raw_agents() -> list[dict[str, Any]]:
    from importlib import resources

    text = resources.files("harness_engines").joinpath("acp_agents.json").read_text()
    return cast("list[dict[str, Any]]", json.loads(text)["agents"])


def test_ol_1_only_qwens_profile_says_its_output_may_be_lost() -> None:
    marked = sorted(a.id for a in packaged_acp_agents(unsigned_ok=True) if a.output_may_be_lost)
    assert marked == ["qwen"]
    qwen = next(a for a in _raw_agents() if a["id"] == "qwen")
    wrong = copy.deepcopy(qwen)
    wrong["outputMayBeLost"] = "yes"
    with pytest.raises(AcpProfileInvalid, match="outputMayBeLost"):
        parse_profile(wrong)


def _started(policy: SessionPolicy) -> CommandStarted:
    mapper = AcpEventMapper(turn_id="t-1", policy=policy)
    events = mapper.update({"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "bash", "kind": "execute",
                            "status": "pending", "rawInput": {"command": "head -n 4 SKILL.md"}})
    (started,) = [e for e in events if isinstance(e, CommandStarted)]
    return started


def test_ol_2_the_policy_carries_it_to_the_command_start_and_its_progress_line() -> None:
    assert SessionPolicy.from_json(json.dumps({"outputMayBeLost": True}).encode()).output_may_be_lost is True
    assert SessionPolicy.from_json(b"{}").output_may_be_lost is False
    marked, plain = _started(LOSES), _started(POLICY)
    assert (marked.output_may_be_lost, plain.output_may_be_lost) == (True, False)
    shown = progress_for(marked)
    assert shown is not None and shown.data == {"callId": "c1", "outputMayBeLost": True}
    plain_shown = progress_for(plain)
    assert plain_shown is not None and "outputMayBeLost" not in plain_shown.data


async def test_ol_3_the_record_and_the_live_start_carry_it() -> None:
    from harness_core.activity.stream import BoundedCommandActivity
    from harness_core.testing.clock import ManualClock

    offered: list[tuple[str, dict[str, Any]]] = []
    activity = BoundedCommandActivity(None, run_id="r", attempt_id="a", fence=0, clock=ManualClock())

    def offer(kind: str, data: dict[str, Any], *, essential: bool = False) -> bool:
        offered.append((kind, data))
        return True

    activity.offer = offer  # type: ignore[method-assign]
    marked = activity.begin(CommandSpec("c1", "engine", "head -n 4 SKILL.md", output_may_be_lost=True))
    record = await marked.finish(exit_code=0, status="completed")
    data = record.as_data()
    assert data["outputMayBeLost"] is True and data["stdout"]["bytes"] == 0  # type: ignore[index]
    model = EVENT_MODELS["x.pipeshub.command.finished"]
    _ = validate_contract(cast("Any", model.model_fields["data"].annotation), data)
    assert [d.get("outputMayBeLost") for k, d in offered if k == COMMAND_STARTED] == [True]

    plain = await activity.begin(CommandSpec("c2", "engine", "ls")).finish(exit_code=0, status="completed")
    assert "outputMayBeLost" not in plain.as_data()
