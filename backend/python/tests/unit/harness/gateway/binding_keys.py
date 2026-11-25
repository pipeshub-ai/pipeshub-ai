"""The test broker's binding key, the one ``journal-http-server.ts`` verifies with (``support/binding-proof.ts``)."""

from __future__ import annotations

from app.harness.sandbox.binding_proof import BindingAttester, binding_proof

TEST_BINDING_KEY = bytes([7]) * 32
TEST_ATTESTER = BindingAttester(TEST_BINDING_KEY)


def proof_for(org_id: str, run_id: str, attempt_id: str) -> str:
    return binding_proof(TEST_BINDING_KEY, org_id, run_id, attempt_id)


__all__ = ["TEST_ATTESTER", "TEST_BINDING_KEY", "proof_for"]
