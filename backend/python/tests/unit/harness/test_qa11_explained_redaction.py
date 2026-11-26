"""QA-11 QA11-4 (IX-APR-PRJ-3): the ``interaction.explained`` reason never journals a secret from the vector.

The reason is read by whoever decides, who may be an approver other than the run's owner; its destination label
comes from the call's arguments and its source titles from what the run read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.harness.flow_destination import MAX_LABEL_CHARS, pipeshub_destination
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    FLOW_RULE,
    AskDestination,
    destination_key,
    explained_ask,
)
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.kernel.core.messages import ToolCall
from harness_core.security.labels import AskSource
from tests.support.token_vector import EXAMPLES, MASK, VectorEntry, holding, strings_in

if TYPE_CHECKING:
    from pydantic import JsonValue

POST = ToolEffect(tool_id_for("send_message", source="slack"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
EXAMPLE_IDS = [i for i, _, _ in EXAMPLES]
PARAMS = [(example, entry) for _, example, entry in EXAMPLES]


@dataclass(frozen=True)
class Ledger:
    described: tuple[AskSource, ...]

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return frozenset({"rec-1"})

    def describe_sources(self, run_id: str) -> tuple[AskSource, ...]:
        return self.described


def reason_for(call: ToolCall, title: str) -> dict[str, JsonValue]:
    where = pipeshub_destination(call, POST)
    ledger = Ledger((AskSource(title, "rec-1"),))
    return explained_ask(FLOW_RULE, POST, ledger, "run-1", where, grantable=True)  # type: ignore[arg-type]


@pytest.mark.parametrize(("secret", "entry"), PARAMS, ids=EXAMPLE_IDS)
def test_qa11_4_a_secret_in_the_destination_or_a_source_title_is_masked(secret: str, entry: VectorEntry) -> None:
    call = ToolCall(id="c", name="send_message", arguments={"to": [f"ops {secret}"], "text": "hi"})
    reason = reason_for(call, f"Runbook {secret}")
    assert not holding(secret, reason)
    assert strings_in(reason["destination"])[0] == f"ops {MASK}"
    assert strings_in(reason["sources"])[0] == f"Runbook {MASK}"


@pytest.mark.parametrize(("secret", "entry"), PARAMS, ids=EXAMPLE_IDS)
def test_qa11_4_a_secret_cut_by_the_label_limit_shows_no_part_of_itself(secret: str, entry: VectorEntry) -> None:
    # The label keeps MAX_LABEL_CHARS - 1 characters: the lead, ", " and about half of the secret.
    lead = "a" * (MAX_LABEL_CHARS - 3 - len(secret) // 2)
    call = ToolCall(id="c", name="send_message", arguments={"to": [lead, secret]})
    (label, _key) = strings_in(reason_for(call, "Plan")["destination"])
    assert secret[:8] not in label


def test_qa11_4_masked_destinations_still_have_their_own_grant_keys() -> None:
    (one, _), (two, _) = PARAMS[2], PARAMS[3]
    first = pipeshub_destination(ToolCall(id="c", name="send_message", arguments={"to": [one]}), POST)
    second = pipeshub_destination(ToolCall(id="c", name="send_message", arguments={"to": [two]}), POST)
    assert first is not None and second is not None
    assert first.label == second.label == MASK
    assert first.key != second.key


@pytest.mark.parametrize(("secret", "entry"), PARAMS, ids=EXAMPLE_IDS)
def test_qa11_4_the_core_builder_masks_a_label_from_any_host(secret: str, entry: VectorEntry) -> None:
    where = AskDestination(f"#{secret}", destination_key(POST.tool_id, secret))
    reason = explained_ask(FLOW_RULE, POST, Ledger(()), "run-1", where, grantable=True)  # type: ignore[arg-type]
    assert not holding(secret, reason)
