"""BD-1..BD-7: an endpoint serves exactly one binding; NA-8's digest from the Python side."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from harness_core.gateway import (
    BindingMismatch,
    GatewayTokenVerifier,
    RunAttemptBinding,
    grant_digest,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.tool_endpoint import BINDING, build_endpoint
from harness_core.testing.gateway import TokenMint

VECTOR = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "gateway-token.json").read_text()
)


def test_na8_grant_digests_match_nodes() -> None:
    assert grant_digest(VECTOR["toolIds"]) == VECTOR["toolIdsDigest"]
    assert grant_digest(reversed(VECTOR["toolIds"])) == VECTOR["toolIdsDigest"]
    assert grant_digest(()) == VECTOR["routesDigest"]


async def _through_a_token(binding: object) -> None:
    clock = ManualClock()
    mint = TokenMint(clock)
    claims = await GatewayTokenVerifier(mint.source, clock).verify(mint.mint(binding))  # type: ignore[arg-type]
    await build_endpoint().endpoint.list_tools(claims)


async def test_bd1_this_attempts_token_is_accepted() -> None:
    await _through_a_token(BINDING)


@pytest.mark.parametrize(
    "other",
    [
        dataclasses.replace(BINDING, run_id="run-9"),
        dataclasses.replace(BINDING, attempt_id="run-1.a1"),
        dataclasses.replace(BINDING, fence=1),
        dataclasses.replace(BINDING, tenant="org-2"),
        dataclasses.replace(BINDING, scope="session-2"),
        dataclasses.replace(BINDING, parent_run_id=None),
        dataclasses.replace(BINDING, tool_ids_digest=grant_digest(["crm@inst-1/delete"])),
        dataclasses.replace(BINDING, routes_digest=grant_digest(["route-1"])),
    ],
    ids=[
        "bd2-another-run",
        "bd3-earlier-attempt",
        "bd3-earlier-fence",
        "bd4-another-org",
        "bd4-another-session",
        "bd4-another-parent",
        "bd5-another-grant",
        "bd5-other-routes",
    ],
)
async def test_bd2_to_bd5_a_token_for_another_binding_is_refused(other: RunAttemptBinding) -> None:
    with pytest.raises(BindingMismatch):
        await _through_a_token(other)
