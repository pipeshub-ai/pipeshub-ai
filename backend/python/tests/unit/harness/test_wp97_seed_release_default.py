"""WP97-FU-1: the human-test seed (``deployment/e2e/seed_config.py human``) enforces the release default for the class
rules of the approvals document. The stack's document held ``writes: ask`` at some point, so every engine write to
the person's own space asked there and its research runs measured a stricter rule, not the default. The seed's merge
of the stored rule is what is under test, over an in-memory configuration service.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants
from tests.support.config_service import FakeConfigurationService

if TYPE_CHECKING:
    import pytest

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "deployment" / "e2e"))
for name, value in {
    "E2E_API": "http://127.0.0.1:9", "E2E_ADMIN_EMAIL": "admin@acme-demo.example", "E2E_ADMIN_PASSWORD": "x",
    "E2E_STATE": os.devnull, "E2E_MEMBER_FILE": os.devnull,
}.items():
    os.environ.setdefault(name, value)
seed_config = importlib.import_module("seed_config")
seed_human = importlib.import_module("seed_human")

INSTANCE = "f6cf3be4"
TOOL = f"mcp@{INSTANCE}/create_ticket"
KEY = config_node_constants.AGENT_HARNESS_APPROVALS.value


def _config(default: dict[str, Any] | None) -> FakeConfigurationService:
    config = FakeConfigurationService()
    if default is not None:
        config.put(KEY, {"default": default})
    return config


def _default(config: FakeConfigurationService) -> dict[str, Any]:
    return dict(config._nodes[KEY]["default"])  # pyright: ignore[reportPrivateUsage]


async def test_wp97_seed_1_a_class_rule_the_seed_did_not_set_is_removed_and_logged(capsys: pytest.CaptureFixture[str]) -> None:
    config = _config({"tools": {TOOL: "ask"}, "writes": "ask", "flow": {"level": "ask"}})
    assert await seed_config.write_human(INSTANCE, keep_class_rules=False, config=config) == TOOL
    assert _default(config) == {"tools": {TOOL: "ask"}, "flow": {"level": "ask"}}, "the tool rule and flow stay"
    (logged,) = [json.loads(line) for line in capsys.readouterr().err.splitlines() if "seed.approvals_reset" in line]
    assert logged == {"event": "seed.approvals_reset", "removed": {"writes": "ask"}}


async def test_wp97_seed_2_a_session_that_tests_stricter_rules_keeps_them() -> None:
    config = _config({"tools": {TOOL: "ask"}, "writes": "ask"})
    _ = await seed_config.write_human(INSTANCE, keep_class_rules=True, config=config)
    assert _default(config)["writes"] == "ask"


def test_wp97_seed_3_every_class_rule_goes_and_a_stale_tool_rule_is_replaced() -> None:
    merged, removed = seed_config.human_rules(
        {"tools": {"mcp@old/create_ticket": "ask", "web@x/fetch": "deny"}, "classes": {"write": "ask"}, "writes": "deny"},
        TOOL,
        keep_class_rules=False,
    )
    assert merged == {"tools": {"web@x/fetch": "deny", TOOL: "ask"}}
    assert removed == {"classes": {"write": "ask"}, "writes": "deny"}


async def test_wp97_seed_4_a_seeded_default_is_left_alone() -> None:
    config = _config({"tools": {TOOL: "ask"}})
    writes = config.set_config
    calls: list[str] = []

    async def counted(key: str, value: Any) -> bool:  # noqa: ANN401
        calls.append(key)
        return await writes(key, value)

    config.set_config = counted  # type: ignore[method-assign]
    _ = await seed_config.write_human(INSTANCE, keep_class_rules=False, config=config)
    assert calls == []


async def test_wp97_seed_5_the_durable_lane_asks_before_writes_as_the_deployment_s_rule() -> None:
    config = _config(None)
    await seed_config.write_approvals("org-1", config=config)
    assert _default(config) == {"writes": "ask"}


async def test_wp97_seed_7_a_rows_rules_replace_their_keys_and_a_null_puts_the_release_default_back() -> None:
    """QA-6's rows set the deployment's rule per row (``seed_config.py rules``): a key the row leaves out is kept."""
    config = _config({"writes": "ask", "tools": {TOOL: "ask"}})
    stored = await seed_config.write_rules({"writes": None, "flow": {"level": "deny"}}, config=config)
    assert stored == _default(config) == {"tools": {TOOL: "ask"}, "flow": {"level": "deny"}}
    await seed_config.write_rules({"writes": "ask", "flow": None}, config=config)
    assert _default(config) == {"tools": {TOOL: "ask"}, "writes": "ask"}


def test_wp97_seed_6_the_person_s_seed_records_the_tool_id_and_writes_no_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(seed_human, "save_state", lambda state: saved.append(dict(state)))
    seed_human.approvals({"mcpInstanceId": INSTANCE, "approvalsSet": True})
    assert saved == [{"mcpInstanceId": INSTANCE, "askToolId": TOOL}]
