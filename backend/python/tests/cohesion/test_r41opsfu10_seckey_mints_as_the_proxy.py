"""R41OPS-FU-10: the SECKEY lane's gateway helper asks Node for a token the way the egress proxy does.

``deployment/e2e/seckey_gateway.py`` mints gateway tokens with the product's ``NodeGatewayTokens.for_attempt``. When
R38IDENT-FU-7 added the broker's binding proof to that signature the helper kept the old call, raised ``TypeError``
before printing a line, and the lane reported 16 "printed nothing" failures while 96 of its gateway checks stopped
running. These tests hold the helper to the signature it calls, so the next change to it fails here, not on a stack.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.harness.gateway.tokens import IssuedGatewayToken, NodeGatewayTokens
from app.harness.sandbox.binding_proof import BindingAttester, binding_proof

ROOT = Path(__file__).resolve().parents[4]
HELPER = ROOT / "deployment" / "e2e" / "seckey_gateway.py"
sys.path.insert(0, str(HELPER.parent))
import seckey_gateway  # noqa: E402

if TYPE_CHECKING:
    import pytest

SIGNATURE = inspect.signature(NodeGatewayTokens.for_attempt)


class _Recording:
    """Binds every call against the real method's signature, as the real method would."""

    def __init__(self) -> None:
        self.calls: list[inspect.BoundArguments] = []

    async def for_attempt(self, *args: object, **kwargs: object) -> IssuedGatewayToken:
        self.calls.append(SIGNATURE.bind(self, *args, **kwargs))
        return IssuedGatewayToken("minted", datetime.now(UTC))


def _for_attempt_calls(source: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "for_attempt"
    ]


def test_every_call_site_binds_to_the_signature() -> None:
    calls = _for_attempt_calls(HELPER.read_text(encoding="utf-8"))
    assert calls, "seckey_gateway.py no longer calls for_attempt: the lane mints no token"
    required = [
        p.name
        for p in list(SIGNATURE.parameters.values())[1:]
        if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    for call in calls:
        assert not any(isinstance(a, ast.Starred) for a in call.args), "a starred call cannot be checked here"
        named = {k.arg for k in call.keywords}
        missing = [n for n in required[len(call.args) :] if n not in named]
        assert not missing, f"line {call.lineno}: for_attempt called without {missing}"
        assert len(call.args) + len(named) <= len(SIGNATURE.parameters) - 1, f"line {call.lineno}: too many arguments"


def test_mint_passes_the_proof_through() -> None:
    tokens = _Recording()
    minted = asyncio.run(seckey_gateway._mint(tokens, "org-1", "run-1", "run-1.a1", "v1.proof"))  # type: ignore[arg-type]
    assert minted == ("minted", "")
    (bound,) = tokens.calls
    assert dict(bound.arguments) | {"self": None} == {
        "self": None,
        "org_id": "org-1",
        "run_id": "run-1",
        "attempt_id": "run-1.a1",
        "binding_proof": "v1.proof",
    }


def test_attest_signs_with_the_brokers_attester(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The proofs come from ``BindingAttester``, the broker's own code, and name exactly the attempt asked for."""
    key = bytes([7]) * 32
    monkeypatch.setattr(BindingAttester, "from_env", classmethod(lambda cls: cls(key)))
    seckey_gateway.attest({"bindings": {"child": {"orgId": "o", "runId": "r", "attemptId": "a"}}})
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed == {"proofs": {"child": binding_proof(key, "o", "r", "a")}}
