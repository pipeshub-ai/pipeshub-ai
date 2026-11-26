"""Node admits to the durable loop exactly the chat modes MODE_CATALOG runs as react, plus auto."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.agent_loop.modes import MODE_CATALOG
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS

VECTOR = (
    Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "durable-chat-modes.json"
)


def test_node_allowlist_is_auto_plus_every_mode_the_durable_kernel_runs() -> None:
    """RO-1 (K-L): react's loop and deep's orchestrator (its sub-agents are child
    runs); planExecute's phase loop has no durable director yet."""
    durable_modes = {
        key.lower()
        for mode in MODE_CATALOG
        if mode.loop_kind in ("react", "orchestrator")
        for key in (mode.name, *mode.aliases)
    }
    vector = json.loads(VECTOR.read_text())["durableChatModes"]
    assert {m.lower() for m in vector} == {"auto"} | durable_modes
    assert "planexecute" not in {m.lower() for m in vector}


def test_node_assistant_allowlist_is_the_canonical_chat_mode_policies() -> None:
    """The built-in assistant is durable only in the modes legacy serves from
    `/chat/stream` by name: Node sends a mode naming `agent` to the agent
    route (Universal Agent Mode), and other values fall back in ways the pin
    does not record."""
    from app.agents.chat_modes.policy import _CANONICAL_POLICIES

    vector = json.loads(VECTOR.read_text())["assistantChatModes"]
    assert set(vector) == {name for name in _CANONICAL_POLICIES if "agent" not in name}


def test_cpd10_8_the_release_layer_holds_the_vectors_modes() -> None:
    """`rollout.durable.*` of the release layer is what Node routes on; the catalog checks above hold it."""
    vector = json.loads(VECTOR.read_text())
    durable = RELEASE_DEFAULTS.rollout.durable
    assert [mode.root for mode in durable.modes] == vector["durableChatModes"]
    assert [mode.root for mode in durable.assistant_modes] == vector["assistantChatModes"]
    assert durable.model_families is None
