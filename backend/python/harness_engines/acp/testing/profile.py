"""The fake ACP agent's profile, as a release document carries it (``acp_agents.json`` shape)."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from harness_engines.acp.profiles import DOCUMENT_SCHEMA, parse_profile
from harness_engines.acp.testing.fake_agent import (
    BINARY_PATH,
    CONFIG_ENV,
    DIGEST,
    INSTRUCTIONS_PATH,
    MODEL_ENV,
    MODEL_URL_ENV,
    STORE,
    TOKEN_ENV,
    VERSION,
)

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue
    from harness_engines.acp.profiles import AcpAgentProfile

FAKE_ENTRY: dict[str, JsonValue] = {
    "id": "fake",
    "name": "Fake ACP agent",
    "agent": {"name": "fake-acp", "version": VERSION},
    "binary": {"path": BINARY_PATH, "digest": DIGEST},
    "image": "pxh-fake-acp:" + VERSION,
    "argv": ["acp"],
    "modelApi": "chat_completions",
    "env": {"FAKE_ACP_TELEMETRY": "off"},
    "gatewayEnv": {"modelToken": TOKEN_ENV, "modelBaseUrl": MODEL_URL_ENV, "model": MODEL_ENV},
    "configs": [{"env": CONFIG_ENV, "template": {"model": "${model}", "permission": {}}, "toolRules": ["permission"]}],
    "nativeTools": {
        "read": ["read"],
        "search": ["glob", "grep"],
        "edit": ["edit", "write"],
        "shell": ["bash"],
        "skill": ["skill"],
    },
    "planTool": "todowrite",
    "mcp": {"server": "tools", "toolName": "{server}_{tool}"},
    "instructionsFile": INSTRUCTIONS_PATH,
    "resume": {"mode": "load", "paths": [STORE]},
    "clientFs": True,
    "sessionConfig": {"mode": "code"},
}


def fake_entry(**overrides: JsonValue) -> dict[str, JsonValue]:
    entry = copy.deepcopy(FAKE_ENTRY)
    entry.update(overrides)
    return entry


def fake_profile(**overrides: JsonValue) -> AcpAgentProfile:
    return parse_profile(fake_entry(**overrides))


def fake_document(*entries: dict[str, JsonValue]) -> bytes:
    agents: list[JsonValue] = list(entries) if entries else [FAKE_ENTRY]
    return json.dumps({"schema": DOCUMENT_SCHEMA, "version": "test", "agents": agents}).encode()


__all__ = ["FAKE_ENTRY", "fake_document", "fake_entry", "fake_profile"]
