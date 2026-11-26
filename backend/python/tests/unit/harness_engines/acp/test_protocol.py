"""ACP-P*: the driver against the scripted fake ACP agent, as a protocol peer.

Every case drives a real ``AcpSession`` over the fake agent's stdin and stdout in a fake sandbox: the permission
flow (allowed by policy, refused, approved, timed out, cancelled), cancel, resume by ``session/load`` and by recap,
hostile output (oversized lines, broken JSON-RPC, unknown methods, path escapes in ``fs/*``) and a crash mid-turn.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.errors import (
    EngineBinaryMismatch,
    EngineCrashed,
    EngineStateIncompatible,
    EngineUnhealthy,
)
from harness_core.engines.events import (
    ApprovalRequested,
    EngineError,
    EngineState,
    FileChanged,
    MessageCompleted,
    PlanUpdated,
    ToolCallSeen,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.types import EngineMode, EngineTask, FollowUp
from harness_core.sandbox.tar import CONFIG_MODE
from harness_core.testing.engines.contract import (
    DEFAULT_GRANT,
    ENDPOINTS,
    PLACEHOLDER,
    SKILLS_DIR,
    Rig,
    collect,
    make_profile,
    open_rig,
)
from harness_core.testing.engines.gateway import ConformanceGateway
from harness_core.testing.sandbox.egress import FakeEgress
from harness_engines.acp.launch import SESSION_POLICY_PATH
from harness_engines.acp.protocol import INVALID_REQUEST, METHOD_NOT_FOUND
from harness_engines.acp.session import STOPPED_TURN_NOTICE
from harness_engines.acp.testing.fake_agent import BINARY_PATH, INSTRUCTIONS_PATH, STORE
from harness_engines.acp.testing.profile import fake_profile
from tests.unit.harness_engines.acp.conftest import AcpHarness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import JsonValue
    from harness_engines.acp.testing.fake_agent import (
        _Connection,  # pyright: ignore[reportPrivateUsage]
    )

RUN = "run-acp-protocol"
TIMEOUT_S = 10.0


def task(index: int, prompt: str, instructions: str = "") -> EngineTask:
    return EngineTask(turn_id=turn_id_for(RUN, index), prompt=prompt, instructions=instructions)


def ended(events: list[EngineEvent]) -> TurnCompleted:
    last = events[-1]
    assert isinstance(last, TurnCompleted), [type(e).__name__ for e in events]
    return last


@pytest.fixture
async def rigs() -> AsyncIterator[list[Rig]]:
    opened: list[Rig] = []
    yield opened
    for rig in opened:
        await rig.session.close()


async def opened(harness: AcpHarness, rigs: list[Rig], *, mode: EngineMode = EngineMode.BUILD, **kwargs: object) -> Rig:
    rig = await open_rig(harness, profile=make_profile(harness, mode=mode), **kwargs)  # pyright: ignore[reportArgumentType]
    rigs.append(rig)
    return rig


def agent_side(harness: AcpHarness, rig: Rig) -> _Connection:
    return harness.fakes[rig.sandbox.id].connections[-1]


def replies(connection: _Connection, key: str) -> list[dict[str, JsonValue]]:
    return [dict(m[key]) for m in connection.received if key in m]  # pyright: ignore[reportArgumentType]


class TestLaunchAndOpen:
    async def test_the_agent_is_started_as_pinned_and_told_only_placeholders(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        assert rig.launch.argv == (BINARY_PATH, "acp")
        assert rig.launch.env["FAKE_ACP_TOKEN"] == PLACEHOLDER
        assert rig.launch.env["FAKE_ACP_MODEL_URL"] == ENDPOINTS.model_base_url
        config = json.loads(rig.launch.env["FAKE_ACP_CONFIG"])
        assert config["permission"] == {
            "read": "allow", "glob": "allow", "grep": "allow", "edit": "allow", "write": "allow",
            "bash": "allow", "skill": "allow", "todowrite": "allow",
        }
        # What the agent obeys is root's and read-only.
        assert rig.sandbox.state.files[SESSION_POLICY_PATH].mode == CONFIG_MODE

    async def test_plan_mode_denies_edits_and_the_shell_in_the_agents_config(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs, mode=EngineMode.PLAN)
        rules = json.loads(rig.launch.env["FAKE_ACP_CONFIG"])["permission"]
        assert {name for name, value in rules.items() if value == "deny"} == {"edit", "write", "bash"}
        policy = json.loads(rig.sandbox.files[SESSION_POLICY_PATH])
        assert policy["clientFsWrite"] is False and policy["clientFsRead"] is True

    async def test_initialize_offers_fs_and_no_terminal(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        init = next(m for m in agent_side(harness, rig).received if m.get("method") == "initialize")
        params = dict(init["params"])  # pyright: ignore[reportArgumentType]
        assert params["protocolVersion"] == 1
        assert params["clientCapabilities"] == {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False}

    @pytest.mark.parametrize(
        ("behaviour", "refusal"),
        [
            ({"protocolVersion": 2}, EngineBinaryMismatch),
            ({"agentVersion": "9.9.9"}, EngineBinaryMismatch),
            ({"httpMcp": False}, EngineUnhealthy),
        ],
    )
    async def test_an_agent_that_is_not_the_pinned_one_is_refused_before_any_session(
        self, rigs: list[Rig], behaviour: dict[str, JsonValue], refusal: type[Exception]
    ) -> None:
        harness = AcpHarness(behaviour=behaviour)
        with pytest.raises(refusal):
            await opened(harness, rigs)
        process = harness.sandboxes[-1].processes[-1]
        assert not process.running

    async def test_an_agent_without_agent_info_is_trusted_on_its_binary_digest(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"agentInfo": False})
        rig = await opened(harness, rigs)
        assert ended(await collect(rig.session, task(0, "say hi"))).final_text == "hi"

    async def test_an_ask_before_the_session_exists_is_answered(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"requestOnInit": "session/request_permission"})
        rig = await opened(harness, rigs)
        early = replies(agent_side(harness, rig), "earlyReply")
        assert early[0]["error"]["code"] == METHOD_NOT_FOUND  # pyright: ignore[reportIndexIssue]

    async def test_the_tool_gateway_is_the_one_mcp_server_with_the_tools_placeholder(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        await collect(rig.session, task(0, "say hi"))
        new = next(m for m in agent_side(harness, rig).received if m.get("method") == "session/new")
        assert dict(new["params"])["mcpServers"] == [  # pyright: ignore[reportArgumentType]
            {
                "type": "http",
                "name": "tools",
                "url": ENDPOINTS.tools_mcp_url,
                "headers": [{"name": "Authorization", "value": f"Bearer {PLACEHOLDER}"}],
            }
        ]

    async def test_instructions_reach_the_agent_through_its_root_owned_file(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        await collect(rig.session, task(0, "say hi", "House style MARKER-11"))
        assert rig.sandbox.files[INSTRUCTIONS_PATH] == b"House style MARKER-11"
        assert rig.sandbox.state.files[INSTRUCTIONS_PATH].mode == CONFIG_MODE
        assert "MARKER-11" in rig.gateway.model_calls[0].system
        assert not any("MARKER-11" in m.content for m in rig.gateway.model_calls[0].messages if m.role == "user")

    async def test_without_an_instructions_file_they_travel_with_the_first_prompt(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(profile=fake_profile(instructionsFile=None))
        rig = await opened(harness, rigs)
        await collect(rig.session, task(0, "say hi", "House style MARKER-12"))
        await collect(rig.session, FollowUp(turn_id_for(RUN, 1), "say again"))
        users = [m.content for m in rig.gateway.model_calls[-1].messages if m.role == "user"]
        assert sum("MARKER-12" in u for u in users) == 1


class TestEvents:
    async def test_a_turn_maps_tools_files_plan_and_the_answer(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        events = await collect(
            rig.session,
            task(0, 'todo [{"title": "look", "status": "in_progress"}]\nwrite notes.md hello\ntool search {"q": 1}\nsay done'),
        )
        names = [type(e).__name__ for e in events]
        assert names.count("PlanUpdated") == 1
        plan = next(e for e in events if isinstance(e, PlanUpdated))
        assert [(i.title, i.status) for i in plan.items] == [("look", "in_progress")]
        assert [e.path for e in events if isinstance(e, FileChanged)] == ["notes.md"]
        assert rig.sandbox.files["/workspace/notes.md"] == b"hello"
        seen = [(e.tool, e.status) for e in events if isinstance(e, ToolCallSeen)]
        assert seen == [("write", "started"), ("write", "completed"), ("search", "started"), ("search", "completed")]
        assert ended(events).final_text == "done"
        assert isinstance(events[-3], MessageCompleted) or any(isinstance(e, MessageCompleted) for e in events)
        assert [e.meta.seq for e in events] == list(range(len(events)))

    async def test_the_write_went_through_the_clients_fs_as_the_sandbox_user(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        await collect(rig.session, task(0, "write sub/notes.md hello\nsay done"))
        scripts = [e.argv for e in rig.sandbox.execs if e.argv[:2] == ("sh", "-c") and "fs-write" in e.argv]
        assert scripts and scripts[0][-1] == "/workspace/sub/notes.md"
        assert rig.sandbox.files["/workspace/sub/notes.md"] == b"hello"


class TestPermissions:
    async def test_in_policy_tools_are_allowed_once_never_always(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        events = await collect(rig.session, task(0, 'write a.md x\ntool search {"q": 1}\nsay ok'))
        assert not any(isinstance(e, ApprovalRequested) for e in events)
        answers = [
            m for m in agent_side(harness, rig).received
            if "result" in m and "outcome" in dict(m["result"] or {})  # pyright: ignore[reportArgumentType]
        ]
        assert answers and all(dict(m["result"])["outcome"] == {"outcome": "selected", "optionId": "once"} for m in answers)  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize(
        ("call", "permission"),
        [
            ({"toolCallId": "c1", "title": "webfetch", "kind": "fetch"}, "web"),
            ({"toolCallId": "c2", "title": "mode", "kind": "switch_mode"}, "switch_mode"),
            ({"toolCallId": "c3", "title": "write", "kind": "edit", "locations": [{"path": "/state/acp/session.json"}]}, "outside_workspace"),
            ({"toolCallId": "c4", "title": "mystery", "kind": "other"}, "other"),
        ],
    )
    async def test_out_of_policy_asks_become_approval_requests_and_a_refusal_rejects_once(
        self, rigs: list[Rig], call: dict[str, JsonValue], permission: str
    ) -> None:
        harness = AcpHarness(behaviour={"askOnPrompt": call})
        rig = await opened(harness, rigs)
        seen: list[EngineEvent] = []

        async def run() -> None:
            async for event in rig.session.run_turn(task(0, "say ok")):
                seen.append(event)
                if isinstance(event, ApprovalRequested):
                    await rig.session.answer(event.request_id, "denied")

        await asyncio.wait_for(run(), TIMEOUT_S)
        asks = [e for e in seen if isinstance(e, ApprovalRequested)]
        assert [(a.permission, a.call_id) for a in asks] == [(permission, call["toolCallId"])]
        reply = replies(agent_side(harness, rig), "askReply")[0]
        assert reply["result"] == {"outcome": {"outcome": "selected", "optionId": "no"}}

    async def test_plan_mode_refuses_the_shell(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"askOnPrompt": {"toolCallId": "c1", "title": "bash", "kind": "execute"}})
        rig = await opened(harness, rigs, mode=EngineMode.PLAN)
        events: list[EngineEvent] = []
        async for event in rig.session.run_turn(task(0, "say ok")):
            events.append(event)
            if isinstance(event, ApprovalRequested):
                assert event.permission == "shell"
                await rig.session.answer(event.request_id, "denied")
        assert ended(events).reason == "completed"

    async def test_an_approval_allows_once(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"askOnPrompt": {"toolCallId": "c1", "title": "webfetch", "kind": "fetch"}})
        rig = await opened(harness, rigs)
        async for event in rig.session.run_turn(task(0, "say ok")):
            if isinstance(event, ApprovalRequested):
                await rig.session.answer(event.request_id, "approved")
        reply = replies(agent_side(harness, rig), "askReply")[0]
        assert reply["result"] == {"outcome": {"outcome": "selected", "optionId": "once"}}

    async def test_an_unanswered_ask_is_refused_after_its_timeout(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(
            behaviour={"askOnPrompt": {"toolCallId": "c1", "title": "webfetch", "kind": "fetch"}},
            session_options={"permission_timeout_s": 0.05},
        )
        rig = await opened(harness, rigs)
        events = await collect(rig.session, task(0, "say ok"))
        assert any(isinstance(e, ApprovalRequested) for e in events)
        assert ended(events).reason == "completed"
        reply = replies(agent_side(harness, rig), "askReply")[0]
        assert reply["result"] == {"outcome": {"outcome": "selected", "optionId": "no"}}

    async def test_a_cancel_answers_the_pending_ask_cancelled(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"askOnPrompt": {"toolCallId": "c1", "title": "webfetch", "kind": "fetch"}})
        rig = await opened(harness, rigs)
        events: list[EngineEvent] = []
        async for event in rig.session.run_turn(task(0, "hold\nsay never")):
            events.append(event)
            if isinstance(event, ApprovalRequested):
                await rig.session.interrupt()
        assert ended(events).reason == "interrupted"
        reply = replies(agent_side(harness, rig), "askReply")[0]
        assert reply["result"] == {"outcome": {"outcome": "cancelled"}}


class TestCancel:
    async def test_an_agent_that_ignores_the_cancel_is_stopped_and_the_turn_ends_interrupted(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"ignoreCancel": True}, session_options={"cancel_grace_s": 0.1})
        rig = await opened(harness, rigs)
        runner = asyncio.get_running_loop().create_task(collect(rig.session, task(0, "hold\nsay never")))
        await asyncio.wait_for(rig.gateway.holding.wait(), TIMEOUT_S)
        await rig.session.interrupt()
        events = await asyncio.wait_for(runner, TIMEOUT_S)
        assert ended(events).reason == "interrupted"
        assert not (await rig.session.health()).alive
        rig.gateway.release()

    async def test_cancel_is_a_notification_naming_the_session(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        runner = asyncio.get_running_loop().create_task(collect(rig.session, task(0, "hold\nsay never")))
        await asyncio.wait_for(rig.gateway.holding.wait(), TIMEOUT_S)
        await rig.session.interrupt()
        assert ended(await asyncio.wait_for(runner, TIMEOUT_S)).reason == "interrupted"
        cancel = [m for m in agent_side(harness, rig).received if m.get("method") == "session/cancel"]
        assert len(cancel) == 1 and "id" not in cancel[0]
        assert dict(cancel[0]["params"])["sessionId"] == "fake-session-1"  # pyright: ignore[reportArgumentType]

    async def test_the_prompt_after_a_stopped_turn_says_so_on_lines_of_its_own(self, rigs: list[Rig]) -> None:
        """R23PERF-FU-1: Qwen Code keeps the stopped prompt unanswered and glues the next one to it with no break."""
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        runner = asyncio.get_running_loop().create_task(collect(rig.session, task(0, "hold\nsay never")))
        await asyncio.wait_for(rig.gateway.holding.wait(), TIMEOUT_S)
        await rig.session.interrupt()
        assert ended(await asyncio.wait_for(runner, TIMEOUT_S)).reason == "interrupted"
        assert ended(await collect(rig.session, task(1, "say after"))).reason == "completed"
        assert ended(await collect(rig.session, task(2, "say later"))).reason == "completed"
        prompts = [
            [dict(b)["text"] for b in dict(m["params"])["prompt"]]  # pyright: ignore[reportArgumentType, reportUnknownArgumentType]
            for m in agent_side(harness, rig).received
            if m.get("method") == "session/prompt"
        ]
        assert prompts[1] == [STOPPED_TURN_NOTICE, "say after"]
        assert STOPPED_TURN_NOTICE.startswith("\n\n") and STOPPED_TURN_NOTICE.endswith("\n\n")
        assert prompts[2] == ["say later"], "only the prompt right after the stop carries it"


class TestResume:
    async def test_a_loadable_session_is_loaded_and_its_replay_is_not_this_turns(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        first = await opened(harness, rigs)
        await collect(first.session, task(0, "say alpha\nRESUME-MARKER-2"))
        state = await first.session.capture_state()
        envelope = json.loads(state.payload)
        assert f"{STORE}/sessions/fake-session-1.json" in envelope["files"]
        await first.session.close()
        second = await opened(harness, rigs, restore=state, gateway=first.gateway)
        events = await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say beta"))
        assert ended(events).final_text == "beta"
        assert [type(e).__name__ for e in events].count("TextDelta") == 1
        connection = agent_side(harness, second)
        assert [m.get("method") for m in connection.received if m.get("method", "").startswith("session/")] == [  # pyright: ignore[reportAttributeAccessIssue]
            "session/load",
            "session/set_config_option",
            "session/prompt",
        ]
        assert connection.config_options == {"mode": "code"}
        assert any("RESUME-MARKER-2" in m.content for m in first.gateway.model_calls[-1].messages if m.role == "user")
        # The restored files are the sandbox user's, so the agent can go on writing its session.
        restored = second.sandbox.state.files[f"{STORE}/sessions/fake-session-1.json"]
        assert restored.data

    async def test_an_agent_that_cannot_load_resumes_from_the_recap(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"loadSession": False})
        first = await opened(harness, rigs)
        await collect(first.session, task(0, "say alpha\nRESUME-MARKER-3"))
        state = await first.session.capture_state()
        await first.session.close()
        second = await opened(harness, rigs, restore=state, gateway=first.gateway)
        assert ended(await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say beta"))).final_text == "beta"
        methods = [m.get("method") for m in agent_side(harness, second).received]
        assert "session/load" not in methods and "session/new" in methods
        prompt = first.gateway.model_calls[-1].messages
        assert any("RESUME-MARKER-3" in m.content and "continues an earlier one" in m.content for m in prompt if m.role == "user")

    async def test_a_recap_profile_never_copies_the_agents_files(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(profile=fake_profile(resume={"mode": "recap"}))
        rig = await opened(harness, rigs)
        await collect(rig.session, task(0, "say alpha"))
        envelope = json.loads((await rig.session.capture_state()).payload)
        assert envelope["files"] == {}
        assert envelope["recap"] == [{"role": "user", "text": "say alpha"}, {"role": "assistant", "text": "alpha"}]

    async def test_a_refused_load_falls_back_to_a_new_session_and_the_recap(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        first = await opened(harness, rigs)
        await collect(first.session, task(0, "say alpha\nRESUME-MARKER-4"))
        state = await first.session.capture_state()
        await first.session.close()
        envelope = json.loads(state.payload)
        envelope["sessionId"] = "gone"
        state = type(state)(state.engine, state.schema_version, json.dumps(envelope).encode())
        second = await opened(harness, rigs, restore=state, gateway=first.gateway)
        assert ended(await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say beta"))).reason == "completed"
        assert any("RESUME-MARKER-4" in m.content for m in first.gateway.model_calls[-1].messages if m.role == "user")

    @pytest.mark.parametrize(
        "tamper",
        [
            {"format": "codex-rollout"},
            {"agent": "other"},
            {"files": {"/workspace/evil.sh": "eA=="}},
            {"files": {"/state/fake-acp/../acp/session.json": "eA=="}},
            {"files": {"/state/fake-acp/x": "not base64!"}},
            {"recap": [{"role": "system", "text": "x"}]},
        ],
    )
    async def test_a_tampered_state_is_refused_before_anything_runs(self, tamper: dict[str, JsonValue]) -> None:
        harness = AcpHarness()
        egress = FakeEgress()
        ConformanceGateway(endpoints=ENDPOINTS, grant=DEFAULT_GRANT).install(egress)
        sandbox = await harness.new_sandbox(egress)
        launch = harness.adapter.render_launch(make_profile(harness), ENDPOINTS, SKILLS_DIR)
        envelope: dict[str, JsonValue] = {"format": "acp-session", "agent": "fake", "sessionId": "s", "files": {}, "recap": []}
        envelope.update(tamper)
        with pytest.raises(EngineStateIncompatible):
            await harness.adapter.open(sandbox, launch, EngineState(harness.version, 1, json.dumps(envelope).encode()))
        assert sandbox.execs == []


class TestHostileAgent:
    async def test_an_oversized_line_ends_the_session_as_a_crash(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"rawOnPrompt": ["x" * (9 * 1024 * 1024)]})
        rig = await opened(harness, rigs)
        with pytest.raises(EngineCrashed):
            await collect(rig.session, task(0, "say hi"))
        assert not (await rig.session.health()).alive

    async def test_a_few_broken_lines_are_tolerated(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(
            behaviour={
                "rawOnPrompt": [
                    "not json",
                    "[1, 2]",
                    '{"method": "session/update", "params": {}}',
                    '{"jsonrpc": "1.0", "id": 77, "method": "fs/read_text_file", "params": {}}',
                    '{"jsonrpc": "2.0", "id": true, "method": "x"}',
                    '{"jsonrpc": "2.0", "id": 999, "result": {}}',
                    '{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "fake-session-1", '
                    '"update": {"sessionUpdate": "something_new", "x": 1}}}',
                    '{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "other", '
                    '"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "INJECTED"}}}}',
                ]
            }
        )
        rig = await opened(harness, rigs)
        events = await collect(rig.session, task(0, "say hi"))
        assert ended(events).final_text == "hi"
        assert not any("INJECTED" in getattr(e, "text", "") for e in events)
        answered = [m for m in agent_side(harness, rig).received if m.get("id") == 77]
        assert dict(answered[0]["error"])["code"] == INVALID_REQUEST  # pyright: ignore[reportArgumentType]

    async def test_a_flood_of_broken_lines_ends_the_session(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"rawOnPrompt": ['{"no": "jsonrpc"}'] * 70})
        rig = await opened(harness, rigs)
        with pytest.raises(EngineCrashed):
            await collect(rig.session, task(0, "say hi"))

    @pytest.mark.parametrize("method", ["terminal/create", "terminal/output", "elicitation/create", "x/unknown"])
    async def test_methods_the_client_does_not_serve_are_refused(self, rigs: list[Rig], method: str) -> None:
        harness = AcpHarness(behaviour={"requestsOnPrompt": [method]})
        rig = await opened(harness, rigs)
        assert ended(await collect(rig.session, task(0, "say hi"))).reason == "completed"
        reply = replies(agent_side(harness, rig), "methodReply")[0]
        assert reply["error"]["code"] == METHOD_NOT_FOUND  # pyright: ignore[reportIndexIssue]

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "/workspace/../etc/passwd",
            "/workspace/./a.md",
            "workspace/a.md",
            "/workspace/a\x00.md",
            "/workspace/link/session.json",
            "/workspace/file-link",
            "/state/acp/session.json",
        ],
    )
    async def test_fs_reads_never_leave_the_workspace(self, rigs: list[Rig], path: str) -> None:
        harness = AcpHarness(behaviour={"fsRead": path})
        rig_holder: list[Rig] = []
        rig = await opened(harness, rig_holder)
        rigs.extend(rig_holder)
        rig.sandbox.put_symlink("/workspace/link", "/state/acp")
        rig.sandbox.put_symlink("/workspace/file-link", "/state/acp/session.json")
        assert ended(await collect(rig.session, task(0, "say hi"))).reason == "completed"
        reply = replies(agent_side(harness, rig), "fsReadReply")[0]
        assert "error" in reply and "result" not in reply, reply

    @pytest.mark.parametrize(
        "path", ["/state/acp/session.json", "/workspace/link/x.md", "/workspace/file-link", "/tmp/x", "/workspace/../x"]
    )
    async def test_fs_writes_never_leave_the_workspace(self, rigs: list[Rig], path: str) -> None:
        harness = AcpHarness(behaviour={"fsWrite": path})
        rig = await opened(harness, rigs)
        rig.sandbox.put_symlink("/workspace/link", "/state/acp")
        rig.sandbox.put_symlink("/workspace/file-link", "/state/acp/session.json")
        before = dict(rig.sandbox.files)
        assert ended(await collect(rig.session, task(0, "say hi"))).reason == "completed"
        reply = replies(agent_side(harness, rig), "fsWriteReply")[0]
        assert "error" in reply, reply
        assert all(rig.sandbox.files.get(p) == before[p] for p in before if not p.startswith(STORE))
        assert b"pwned" not in b"".join(rig.sandbox.files.values())

    async def test_fs_reads_inside_the_workspace_honour_line_and_limit(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"fsRead": "/workspace/link-to-notes"})
        rig = await opened(harness, rigs)
        rig.sandbox.put_file("/workspace/notes.md", b"one\ntwo\nthree\n")
        rig.sandbox.put_symlink("/workspace/link-to-notes", "/workspace/notes.md")
        await collect(rig.session, task(0, "say hi"))
        reply = replies(agent_side(harness, rig), "fsReadReply")[0]
        assert reply["result"] == {"content": "one\ntwo\nthree\n"}

    async def test_fs_writes_are_refused_in_plan_mode(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"fsWrite": "/workspace/a.md"})
        rig = await opened(harness, rigs, mode=EngineMode.PLAN)
        await collect(rig.session, task(0, "say hi"))
        reply = replies(agent_side(harness, rig), "fsWriteReply")[0]
        assert reply["error"]["code"] == METHOD_NOT_FOUND  # pyright: ignore[reportIndexIssue]
        assert "/workspace/a.md" not in rig.sandbox.files


class TestCrash:
    async def test_a_crash_mid_turn_is_a_lost_turn_and_a_dead_session(self, rigs: list[Rig]) -> None:
        harness = AcpHarness(behaviour={"crashAfterUpdates": 2})
        rig = await opened(harness, rigs)
        seen: list[EngineEvent] = []

        async def run() -> None:
            async for event in rig.session.run_turn(task(0, 'tool search {"q": 1}\nsay never')):
                seen.append(event)  # noqa: PERF401 - kept as they arrive, before the crash

        with pytest.raises(EngineCrashed):
            await run()
        assert not any(isinstance(e, TurnCompleted) for e in seen)
        health = await rig.session.health()
        assert not health.alive
        with pytest.raises(EngineCrashed):
            await collect(rig.session, task(1, "say again"))

    async def test_a_prompt_error_fails_the_turn_with_the_gateways_code(self, rigs: list[Rig]) -> None:
        harness = AcpHarness()
        rig = await opened(harness, rigs)
        await collect(rig.session, task(0, "say hi"))
        connection = agent_side(harness, rig)
        connection.sessions.clear()
        events = await collect(rig.session, task(1, "say again"))
        assert ended(events).reason == "failed"
        errors = [e for e in events if isinstance(e, EngineError)]
        assert [e.code for e in errors] == ["acp_error"]
