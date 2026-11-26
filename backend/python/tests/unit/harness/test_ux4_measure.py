"""UX4 ES-12: on the real durable tool catalog (P2.1's enumerators, as UX1's CL-10 measured it), how many writes and
executes an engine run's default asks about, before UX4 (every one) and after (only those leaving the user's own
space), through ``engine_guarded`` as the assembler composes it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.approvals import engine_guarded
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects.flow import GrantingLedger, external_write
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    ApprovalRules,
    LayeredApprovalPolicy,
    in_own_sandbox,
)
from harness_core.engines.types import ProfileApprovals
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from tests.unit.harness.test_effect_tags_complete import ENUMERATORS

if TYPE_CHECKING:
    from harness_core.effects.types import ToolEffect

LOG = HarnessLog.named("tests.ux4.measure")
# UX1 CL-3..CL-6: the deployment's own storage and the user's own space.
OWN_SPACE = {
    "artifacts__save_artifact",
    "artifacts__update_artifact",
    "artifacts__promote_artifact",
    "image_generator__generate_image",
    "skill_manage",
    "run_code",
    "gmail__draft_email",
    "drive__create_folder",
    "onedrive__create_onenote_notebook",
}


@pytest.fixture(scope="module")
def catalog() -> dict[str, ToolEffect]:
    return {t.name: classify_tool_effect(t.name, t.tags) for enumerate_ in ENUMERATORS.values() for t in enumerate_()}


def test_es12_engine_writes_that_ask_before_and_after(catalog: dict[str, ToolEffect], monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)
    default = LayeredApprovalPolicy((*ENGINE_APPROVALS.layers, ApprovalRules(classes=ProfileApprovals().as_mapping())))
    after = engine_guarded(
        default, LayeredApprovalPolicy(), rules=flow_rules(LOG), ledger=GrantingLedger(SessionSourceLedger()), run_id="r", log=LOG
    )
    call = ToolCall(id="c", name="t", arguments={})
    writes = {name: e for name, e in catalog.items() if e.effect in ("write", "execute")}
    asked_before = {name for name, e in writes.items() if default(call, e) == "ask"}
    asked_after = {name for name, e in writes.items() if after(call, e) == "ask"}
    stopped = asked_before - asked_after
    print(
        f"\nUX4-ES12 tools={len(catalog)} write_or_execute={len(writes)} engine_asks_before={len(asked_before)}"
        f" engine_asks_after={len(asked_after)} no_longer_ask={sorted(stopped)}"
    )
    # CODEAUTO: the class rules themselves no longer ask about code in the run's own sandbox (offline run_code).
    own_sandbox = {name for name, e in writes.items() if in_own_sandbox(e)}
    assert own_sandbox == {"run_code"}
    assert asked_before == set(writes) - own_sandbox
    assert asked_after <= asked_before
    assert stopped == OWN_SPACE - own_sandbox
    # One rule for loop and engines: the default now asks exactly where the flow rule may.
    assert asked_after == {name for name, e in writes.items() if external_write(e)}


def test_rt5_tools_that_can_ask_in_the_replay_world_and_the_catalog(catalog: dict[str, ToolEffect]) -> None:
    """UX1's CL-10 count over the replay world the deterministic and live lanes run on, after its tools carry the
    product's effect tags (before: every untagged read counted as an external write)."""
    from tests.cohesion.test_c30_read_tools import EXCEPTIONS, replay_world

    world = {t.name: classify_tool_effect(t.name, t.tags) for t in replay_world()}
    world_may_ask = sorted(name for name, e in world.items() if external_write(e))
    catalog_may_ask = {name for name, e in catalog.items() if external_write(e)}
    print(
        f"\nUX4-RT5 world_tools={len(world)} world_may_ask={len(world_may_ask)} {world_may_ask}"
        f" catalog_tools={len(catalog)} catalog_may_ask={len(catalog_may_ask)} read_named_writes={sorted(EXCEPTIONS)}"
    )
    assert world_may_ask == ["drive__share_file", "gmail__send_email", "jira__add_comment", "jira__create_issue", "slack__send_message"]
    assert len(catalog_may_ask) == 145  # 133 (SINK1), main's 8 docs and sheets writes (#4221), its 4 add_attachments writes
    assert set(EXCEPTIONS) <= catalog_may_ask
