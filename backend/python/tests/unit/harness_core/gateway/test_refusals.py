"""RB-1: one refusal base across engines, sandbox and gateway; gateway codes are unique."""

from __future__ import annotations

from harness_core.engines.errors import EngineRefusal, GrantTooLarge
from harness_core.gateway import (
    TOKEN_REFUSAL_CODES,
    BindingMismatch,
    ForbiddenArguments,
    GatewayRefusal,
    TokenRefused,
)
from harness_core.refusals import HarnessRefusal
from harness_core.sandbox.errors import ImageNotAllowed, SandboxRefusal


def test_rb1_every_family_is_a_harness_refusal_with_a_reason_code() -> None:
    refusals: list[HarnessRefusal] = [
        GrantTooLarge(3, 2),
        ImageNotAllowed("no", reason="image_not_allowed"),
        TokenRefused("token_expired", "expired"),
        BindingMismatch("another run"),
        ForbiddenArguments(("jwt",)),
    ]
    assert [r.reason_code for r in refusals] == [
        "grant_too_large",
        "image_not_allowed",
        "token_expired",
        "binding_mismatch",
        "forbidden_arguments",
    ]
    assert all(isinstance(r, HarnessRefusal) for r in refusals)
    assert issubclass(EngineRefusal, HarnessRefusal)
    assert issubclass(SandboxRefusal, HarnessRefusal)
    assert issubclass(GatewayRefusal, HarnessRefusal)


def test_rb1_gateway_codes_are_unique() -> None:
    codes = [*TOKEN_REFUSAL_CODES, BindingMismatch.reason_code, ForbiddenArguments.reason_code]
    assert len(codes) == len(set(codes))
