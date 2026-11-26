"""FIXPDFSKILL-FU-1: Codex looks at the page images the document checker renders (``view_image``), and only at files
in the run's workspace.

Codex 0.160 runs under ``danger-full-access``, so its own file sandbox confines nothing: ``view_image`` reads any path
the process can. It is offered only with the managed policy hook, which Codex runs before every function tool
(``registry.rs``: ``pre_tool_use_payload`` for any function call, ``tool_name`` ``view_image``, ``tool_input``
``{"path"}``); ``ph policy check --hook`` resolves the path in the sandbox, through every symlink, and the Tool
Gateway's native-call policy refuses a read that lands outside ``/workspace``. The docker lane
(``test_real_codex_conformance``) runs it on the pinned binary.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

from app.harness.contracts.generated.pipeshub.policy_data import TOOL_DISPLAY
from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.events import ToolCallSeen
from harness_core.engines.native_policy import (
    OUTSIDE_WORKSPACE,
    NativeCall,
    NativeCallPolicy,
    native_call_from_wire,
)
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
    RunLimits,
)
from harness_core.gateway.responses_wire import parse_responses_request
from harness_core.kernel.core.messages import ImagePart, ToolMessage
from harness_engines.codex import launch as codex
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.pipeshub import POLICY_HOOK_PATH, ph
from tests.unit.harness_engines.pipeshub import test_ph_cli as cli
from tests.unit.harness_engines.pipeshub.test_ph_cli import Gateway, run

gateway = cli.gateway

LOCALES = Path(__file__).resolve().parents[6] / "frontend" / "lib" / "i18n" / "locales"
THREAD = "11111111-1111-1111-1111-111111111111"
CODEX_TURN = "22222222-2222-2222-2222-222222222222"
PAGE = "/workspace/layout_check/report/report-page-2.png"


def _endpoints(*, hook: str | None = POLICY_HOOK_PATH) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url="http://model-gateway:8097/v1",
        model_api=ModelApi.RESPONSES,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder="pxh-model-placeholder-0123",
        tools_token_placeholder="pxh-tools-placeholder-4567",
        policy_hook=hook,
    )


def _config(mode: EngineMode = EngineMode.BUILD, *, hook: str | None = POLICY_HOOK_PATH) -> dict[str, Any]:
    profile = EngineProfile(
        engine=EngineVersion("codex", "0.160.0", "sha256:" + "d" * 64),
        mode=mode,
        access=AccessRouteRef("route-1", "d1"),
        limits=RunLimits(max_steps=30),
    )
    launch = codex.render_launch(profile, _endpoints(hook=hook), "/skills")
    return tomllib.loads(launch.files[codex.CONFIG_PATH].decode())


class TestCodexIsOfferedItsImageViewerOnlyUnderTheHook:
    @pytest.mark.parametrize("mode", list(EngineMode))
    def test_with_the_managed_hook_view_image_is_on_and_the_hook_checks_it(self, mode: EngineMode) -> None:
        config = _config(mode)
        assert codex.IMAGE_TOOL == "view_image" not in codex.FEATURES_OFF
        assert config["features"]["view_image"] is True
        ((group,),) = [config["hooks"]["PreToolUse"]]
        assert set(group["matcher"].split("|")) == {"Bash", codex.IMAGE_TOOL}

    def test_without_the_hook_nothing_would_confine_it_so_it_stays_off(self) -> None:
        config = _config(hook=None)
        assert config["features"]["view_image"] is False
        assert "hooks" not in config

    def test_it_is_one_of_codexs_reads(self) -> None:
        assert codex.IMAGE_TOOL in codex.NATIVE_TOOLS[NativeTool.READ]
        assert all(codex.IMAGE_TOOL not in names for tool, names in codex.NATIVE_TOOLS.items() if tool is not NativeTool.READ)


class TestTheHookSendsWhereTheReadLands:
    def test_a_relative_path_is_the_engines_working_folders(self, tmp_path: Path) -> None:
        (tmp_path / "pages").mkdir()
        call = ph.hook_call({"tool_name": "view_image", "cwd": str(tmp_path), "tool_input": {"path": "pages/p1.png"}})
        assert call == {"tool": "read", "name": "view_image", "paths": [str(tmp_path / "pages" / "p1.png")]}

    def test_a_symlink_in_the_workspace_is_sent_as_its_target(self, tmp_path: Path) -> None:
        workspace, outside = tmp_path / "workspace", tmp_path / "secrets"
        workspace.mkdir()
        outside.mkdir()
        (outside / "key.png").write_bytes(b"\x89PNG")
        os.symlink(outside / "key.png", workspace / "page.png")
        os.symlink(outside, workspace / "pages")
        for name in ("page.png", "pages/key.png"):
            call = ph.hook_call({"tool_name": "view_image", "cwd": str(workspace), "tool_input": {"path": name}})
            assert call["paths"] == [str(outside / "key.png")]
            verdict = NativeCallPolicy(frozenset({NativeTool.READ}), str(workspace)).check(call)
            assert (verdict.decision, verdict.code) == ("deny", OUTSIDE_WORKSPACE)

    def test_dot_dot_is_resolved_before_the_check(self) -> None:
        call = ph.hook_call({"tool_name": "view_image", "cwd": "/workspace", "tool_input": {"path": "../state/codex/x.png"}})
        assert call["paths"] == ["/state/codex/x.png"]

    @pytest.mark.parametrize("given", [{}, {"path": ""}, {"path": 7}, {"detail": "high"}])
    def test_a_view_image_that_names_no_file_is_refused(self, given: dict[str, Any]) -> None:
        with pytest.raises(ph.PhError, match="names no file"):
            ph.hook_call({"tool_name": "view_image", "cwd": "/workspace", "tool_input": given})

    def test_the_hook_refuses_it_through_the_cli_fail_closed(self, gateway: tuple[Gateway, str]) -> None:
        state, url = gateway
        code, out, err = run(["policy", "check", "--hook"], url, stdin=json.dumps({"tool_name": "view_image", "tool_input": {}}))
        assert (code, out) == (ph.EX_REFUSED, "")
        assert "refused" in err
        assert state.requests == []

    def test_the_hook_asks_the_gateway_with_the_resolved_path(self, gateway: tuple[Gateway, str]) -> None:
        state, url = gateway
        payload = {"tool_name": "view_image", "cwd": "/workspace", "tool_input": {"path": "layout_check/report/report-page-2.png"}}
        code, out, err = run(["policy", "check", "--hook"], url, stdin=json.dumps(payload))
        assert (code, out, err) == (0, "", "")
        assert state.requests[-1][1]["params"]["arguments"] == {"tool": "read", "name": "view_image", "paths": [PAGE]}


class TestThePolicyKeepsANamedReadInTheWorkspace:
    POLICY = NativeCallPolicy(frozenset({NativeTool.READ, NativeTool.SEARCH}))

    def test_a_page_in_the_workspace_is_allowed(self) -> None:
        assert self.POLICY.verdict(NativeCall(NativeTool.READ, (PAGE,), "view_image")).allowed

    @pytest.mark.parametrize(
        "path",
        [
            "/proc/self/environ",
            "/state/codex/config.toml",
            "/pipeshub/session/gateway.json",
            "/skills/pdf/SKILL.md",
            "/etc/passwd",
            "/workspace/../state/x.png",
            "/workspaces/x.png",
            "layout_check/p1.png",
        ],
    )
    def test_anything_else_is_refused_with_the_reason(self, path: str) -> None:
        verdict = self.POLICY.verdict(NativeCall(NativeTool.READ, (path,), "view_image"))
        assert (verdict.decision, verdict.code) == ("deny", OUTSIDE_WORKSPACE)
        assert verdict.reason == f"{path} is outside the workspace: view_image opens only files under /workspace."

    def test_a_read_that_names_no_file_is_unchanged(self) -> None:
        # Claude Code's Read is checked by mode alone: it reads skills under /skills.
        assert self.POLICY.verdict(NativeCall(NativeTool.READ, (), "Read")).allowed

    def test_the_wire_form_is_read(self) -> None:
        call = native_call_from_wire({"tool": "read", "name": "view_image", "paths": ["/proc/1/root/x.png"]})
        assert call == NativeCall(NativeTool.READ, ("/proc/1/root/x.png",), "view_image")
        assert not self.POLICY.check({"tool": "read", "name": "view_image", "paths": ["/proc/1/root/x.png"]}).allowed

    def test_a_mode_without_reads_refuses_it_before_its_path(self) -> None:
        verdict = NativeCallPolicy(frozenset({NativeTool.SHELL})).verdict(NativeCall(NativeTool.READ, (PAGE,), "view_image"))
        assert verdict.code == "not_permitted"


def _mapper() -> CodexEventMapper:
    mapper = CodexEventMapper(thread_id=THREAD, turn_id="trn_fu1")
    mapper.begin(CODEX_TURN)
    return mapper


def _item(method: str, thread: str = THREAD) -> dict[str, Any]:
    item = {"type": "imageView", "id": "call_view", "path": PAGE}
    return {"method": method, "params": {"threadId": thread, "turnId": CODEX_TURN, "item": item}}


class TestALookIsAStepWithAPlainLabel:
    def test_an_image_view_is_a_view_image_step_with_its_path(self) -> None:
        mapper = _mapper()
        started = mapper.feed(_item("item/started"))
        completed = mapper.feed(_item("item/completed"))
        assert [type(e) for e in (*started, *completed)] == [ToolCallSeen, ToolCallSeen]
        (first,), (last,) = started, completed
        assert isinstance(first, ToolCallSeen) and isinstance(last, ToolCallSeen)
        assert (first.call_id, first.tool, first.status, first.arguments) == ("call_view", "view_image", "started", {"path": PAGE})
        assert (last.tool, last.status) == ("view_image", "completed")

    def test_a_helpers_look_is_its_step(self) -> None:
        mapper = _mapper()
        mapper.children.add("child-thread")
        (event,) = mapper.feed(_item("item/completed", thread="child-thread"))
        assert isinstance(event, ToolCallSeen) and event.sub_session_id == "child-thread"

    def test_the_label_names_the_action_never_the_tool_id(self) -> None:
        (builtin,) = [b for b in TOOL_DISPLAY.builtins if "view_image" in {t.root for t in b.tools}]
        assert (builtin.key, builtin.present, builtin.past) == ("viewImage", "Looking at an image", "Looked at an image")

    @pytest.mark.parametrize("locale", sorted(p.name for p in LOCALES.glob("*.json")))
    def test_every_locale_names_it(self, locale: str) -> None:
        names = json.loads((LOCALES / locale).read_text(encoding="utf-8"))["toolNames"]
        assert set(names["viewImage"]) == {"present", "past"}
        assert all(names["viewImage"].values())


def test_the_responses_wire_carries_codexs_image_result_to_the_model() -> None:
    """What 0.160 sends for a ``view_image`` result (``ViewImageOutput.to_response_item``): an ``input_image`` with an
    inline data URL and a detail hint, as the output of the function call."""
    parsed = parse_responses_request(
        {
            "model": "engine-model",
            "input": [
                {"role": "user", "content": "look at page 2"},
                {"type": "function_call", "call_id": "call_view", "name": "view_image", "arguments": json.dumps({"path": PAGE})},
                {
                    "type": "function_call_output",
                    "call_id": "call_view",
                    "output": [{"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo=", "detail": "high"}],
                },
            ],
        }
    )
    result = parsed.messages[-1]
    assert isinstance(result, ToolMessage) and result.tool_call_id == "call_view"
    assert isinstance(result.content, list)
    (part,) = result.content
    assert isinstance(part, ImagePart)
    assert (part.source.media_type, part.source.data) == ("image/png", "iVBORw0KGgo=")
