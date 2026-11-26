"""P-4 across languages: the worker reads the spec Node pinned, with the same digest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.harness.contracts.generated.pipeshub.run_spec import PipeshubRunSpecExtension
from app.harness.contracts.state import PinnedRunSpec
from app.harness.journal_node import NodeJournal
from harness_core.contracts import canonical_digest, dump_contract, validate_contract
from harness_core.contracts.generated.capabilities import Capabilities
from harness_core.kernel.fold import FOLD_VERSION

GOLDEN = (
    Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "pinned-run-spec.json"
)


def _pinned() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text())["pinned"]


def _run() -> dict[str, Any]:
    return {
        "runId": "run-1",
        "orgId": "org-1",
        "sessionId": "session-1",
        "actorId": "user-1",
        "userMessageKey": "message-1",
        "idempotencyKey": "submit-1",
        "payloadHash": "sha256:one",
        "harnessMajor": 2,
        "profile": "dispatched",
        "status": "running",
        "outcome": None,
        "revision": 1,
        "fence": 1,
        "attemptId": "attempt-1",
        "workerId": "worker-1",
        "leaseExpiresAt": 2_000,
        "absoluteDeadlineAt": 10_000,
        "checkpoint": None,
        "createdAt": 1_000,
        "updatedAt": 1_000,
    }


def test_node_pins_the_fold_version_this_worker_folds() -> None:
    # The golden pin is Node's output byte for byte (spec-pinning.test.ts), so this ties the two constants.
    assert _pinned()["spec"]["foldVersion"] == FOLD_VERSION


def test_python_computes_the_digest_node_pinned() -> None:
    pinned = _pinned()
    assert canonical_digest(pinned["spec"]) == pinned["specDigest"]
    assert canonical_digest(pinned["capabilities"]) == pinned["capabilitiesDigest"]
    assert pinned["spec"]["capabilitiesDigest"] == pinned["capabilitiesDigest"]


def test_the_pinned_spec_and_its_extension_validate_in_python() -> None:
    pinned = _pinned()
    spec = PinnedRunSpec.model_validate(pinned).run_spec()
    assert spec.harness_major == 2
    assert spec.principal_ref == "p_user-1"
    extension = validate_contract(PipeshubRunSpecExtension, pinned["spec"]["extensions"]["x.pipeshub"])
    assert extension.agent is not None and extension.agent.agent_key == "agent-1"
    assert extension.via == "pat"
    capabilities = validate_contract(Capabilities, pinned["capabilities"])
    assert capabilities.journal_durable is True


def test_a_malformed_digest_is_refused() -> None:
    pinned = {**_pinned(), "specDigest": "md5:nope"}
    with pytest.raises(ValidationError):
        PinnedRunSpec.model_validate(pinned)


async def test_the_journal_client_adopts_the_pin_from_the_claim() -> None:
    class Credentials:
        async def worker_credential(self) -> str:
            return "phw_credential"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "run": _run(),
                "journal": {"lastSeq": {"session-1": 3}},
                "pinned": _pinned(),
                "attemptToken": {"token": "attempt-token", "expiresAt": 90_000},
            },
        )

    async with httpx.AsyncClient(
        base_url="http://state.local", transport=httpx.MockTransport(handler)
    ) as http:
        journal = NodeJournal(
            base_url="http://ignored.local", credentials=Credentials(), http_client=http
        )
        claim = await journal.claim("run-1", worker_id="worker-1", org_id="org-1")

    assert claim is not None
    assert canonical_digest(dump_contract(claim.spec)) == _pinned()["specDigest"]
    assert claim.capabilities == _pinned()["capabilities"]
    assert "attempt-token" not in repr(claim)
