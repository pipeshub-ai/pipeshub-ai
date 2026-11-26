"""WP-59 round 3: the tool gateway is declared in Gemini CLI's system settings, sealed above the repository.

A trusted workspace's ``.gemini/settings.json`` merges over Gemini's user settings one MCP server name at a time,
so a repository could redefine the gateway's entry; only the system layer is above it. The profile renders that
layer under ``SEALED_DIR`` from the image's own system settings (kept whole) plus the gateway, seeds it root's into
the read-only mount, and checks that every folder above it is root's alone, as Gemini requires before it trusts
the file. The real binary against a repository's own entry is the docker lane
(``test_e2e3_f6_mcp_startup_real_engines.py::test_wp59_a_repositorys_gemini_settings_never_touch_the_gateway_entry``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.types import SEALED_DIR
from harness_core.logs import HarnessLog
from harness_core.testing.engines.contract import new_engine_sandbox
from harness_core.testing.sandbox.egress import FakeEgress
from harness_engines.acp.profiles import AcpProfileInvalid, parse_profile
from harness_engines.acp.sealed import SealedConfigRefused, merged, seal
from harness_engines.acp.testing.profile import fake_entry

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue
    from harness_core.testing.sandbox.fake import (
        FakeProcessContext,
        FakeProgram,
        FakeSandboxHandle,
    )

BASE = "/etc/agent/settings.json"
SYSTEM = f"{SEALED_DIR}/agent/settings.json"
FLOOR: dict[str, JsonValue] = {"mcp": {"allowed": ["tools"]}, "security": {"auth": {"enforcedType": "gateway"}}}
ENTRY: dict[str, JsonValue] = {"mcpServers": {"tools": {"httpUrl": "http://tool-gateway:8097/mcp", "timeout": 150000, "trust": False}}}
SEALED_CONFIG: dict[str, JsonValue] = {"path": SYSTEM, "base": BASE, "template": ENTRY}


def _log() -> HarnessLog:
    return HarnessLog(logging.getLogger("tests.wp59_sealed"))


def _stat(modes: dict[str, str]) -> FakeProgram:
    """``stat -c '%u %a %n'`` over the paths asked, root's with ``modes`` (default 755, files 444)."""

    async def program(context: FakeProcessContext) -> int:
        for path in context.argv[3:]:
            await context.emit(f"0 {modes.get(path, '444' if path.endswith('.json') else '755')} {path}\n".encode())
        return 0

    return program


async def _sandbox(*, base: dict[str, JsonValue] | None = FLOOR, modes: dict[str, str] | None = None) -> FakeSandboxHandle:
    sandbox = await new_engine_sandbox(FakeEgress())
    if base is not None:
        sandbox.put_file(BASE, json.dumps(base).encode())
    sandbox.install("stat", _stat(modes or {}))
    return sandbox


class TestMerge:
    def test_wp59_the_image_settings_are_kept_whole_and_the_gateway_added_beside_them(self) -> None:
        assert merged(FLOOR, ENTRY) == {**FLOOR, **ENTRY}
        assert merged({"mcp": {"allowed": ["tools"]}}, {"mcp": {"timeout": 1}}) == {"mcp": {"allowed": ["tools"], "timeout": 1}}

    @pytest.mark.parametrize(
        "rendered",
        [{"mcp": {"allowed": ["*"]}}, {"security": {"auth": {"enforcedType": "oauth"}}}, {"mcp": "off"}],
    )
    def test_wp59_a_rendered_setting_never_replaces_one_of_the_images(self, rendered: dict[str, JsonValue]) -> None:
        with pytest.raises(SealedConfigRefused):
            _ = merged(FLOOR, rendered)


class TestProfile:
    def test_wp59_a_sealed_config_lives_under_the_sealed_folder_with_an_object_template(self) -> None:
        profile = parse_profile(fake_entry(configs=[SEALED_CONFIG]))
        (config,) = profile.configs
        assert config.sealed and config.base == BASE

    @pytest.mark.parametrize(
        "config",
        [
            {"path": "/etc/elsewhere.json", "template": ENTRY},
            {"path": "/state/acp/x.json", "base": BASE, "template": ENTRY},
            {"path": SYSTEM, "base": "relative.json", "template": ENTRY},
            {"path": SYSTEM, "base": BASE, "template": "text"},
            {"path": f"{SEALED_DIR}/../etc/x.json", "template": ENTRY},
        ],
    )
    def test_wp59_a_sealed_config_outside_the_schema_is_refused(self, config: dict[str, JsonValue]) -> None:
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(configs=[config]))


class TestSeal:
    async def test_wp59_the_system_settings_are_seeded_roots_and_read_only_with_the_floor_kept(self) -> None:
        sandbox = await _sandbox()
        profile = parse_profile(fake_entry(configs=[SEALED_CONFIG]))
        await seal(sandbox, profile, {SYSTEM: json.dumps(ENTRY).encode()}, _log())
        stored = sandbox.state.files[SYSTEM]
        assert json.loads(stored.data) == {**FLOOR, **ENTRY}
        assert stored.mode == 0o444
        (stat,) = [e.argv for e in sandbox.execs if e.argv[0] == "stat"]
        assert set(stat[3:]) == {SYSTEM, f"{SEALED_DIR}/agent", SEALED_DIR, "/"}

    async def test_wp59_an_image_without_its_system_settings_is_refused(self) -> None:
        sandbox = await _sandbox(base=None)
        profile = parse_profile(fake_entry(configs=[SEALED_CONFIG]))
        with pytest.raises(SealedConfigRefused):
            await seal(sandbox, profile, {SYSTEM: json.dumps(ENTRY).encode()}, _log())
        assert SYSTEM not in sandbox.state.files

    @pytest.mark.parametrize("loose", [SEALED_DIR, "/", f"{SEALED_DIR}/agent"])
    async def test_wp59_a_folder_others_can_write_is_refused_as_gemini_would_skip_the_file(self, loose: str) -> None:
        """A broker that made the sealed mount world-writable (the sticky scratch mode) would leave Gemini without
        its system settings, floor and gateway alike: the driver refuses before the agent starts."""
        sandbox = await _sandbox(modes={loose: "1777"})
        profile = parse_profile(fake_entry(configs=[SEALED_CONFIG]))
        with pytest.raises(SealedConfigRefused):
            await seal(sandbox, profile, {SYSTEM: json.dumps(ENTRY).encode()}, _log())
