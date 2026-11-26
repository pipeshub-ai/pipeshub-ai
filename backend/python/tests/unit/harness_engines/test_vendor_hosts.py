"""EG-1..EG-6 (SIGNIN-2): the signed sign-in hosts, and what a run pinned on a person's own plan carries.

The host list is release data signed with the vendor rules' key (plan §2.5): unsigned or tampered, there are no hosts
and no own sign-in runs; only DNS names can be listed. The packaged list is the one SIGNIN-1 recorded (SP-7)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import harness_engines
from app.harness.engines.durable import own_login_of
from app.harness.engines.progress import progress_for
from harness_core.access.errors import VendorRulesInvalid
from harness_core.engines.events import EngineEventMeta, SignInProgress
from harness_core.engines.signin import SignInRefused
from harness_engines.vendor_hosts import (
    CLAUDE_PLAN_ROW,
    HOSTS_SIGNATURE_RESOURCE,
    load_vendor_hosts,
    parse_vendor_hosts,
    release_sign_in_hosts,
)
from harness_engines.vendor_rules import packaged

HOSTS_FILE = Path(harness_engines.__file__).parent / "vendor_hosts.json"
KEY_ID = "test-release-key"
RECORDED = Path(__file__).parent / "claude_code" / "fixtures" / "signin" / "recorded.json"


def _signed(document: bytes) -> tuple[bytes, dict[str, bytes]]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sig = base64.b64encode(private.sign(document)).decode()
    return json.dumps({"alg": "Ed25519", "keyId": KEY_ID, "sig": sig}).encode(), {KEY_ID: public}


def test_eg1_the_packaged_hosts_are_what_signin_1_recorded() -> None:
    hosts = parse_vendor_hosts(HOSTS_FILE.read_bytes())[CLAUDE_PLAN_ROW]
    recorded = json.loads(RECORDED.read_text())
    contacted = {r["host"] for key in ("login_requests", "prompt_expired_requests", "logout_requests") for r in recorded[key]}
    assert set(hosts.sandbox) == contacted == {"api.anthropic.com", "platform.claude.com"}
    assert "claude.com" in hosts.link
    assert recorded["login"]["url"].startswith("https://claude.com/")


def test_eg2_signed_with_a_release_key_the_hosts_load() -> None:
    document = HOSTS_FILE.read_bytes()
    signature, keys = _signed(document)
    assert load_vendor_hosts(document, signature, keys)[CLAUDE_PLAN_ROW].sandbox == (
        "api.anthropic.com", "platform.claude.com")


@pytest.mark.parametrize("case", ["unsigned", "tampered", "unknown_key"])
def test_eg3_unsigned_tampered_or_foreign_hosts_are_refused(case: str) -> None:
    document = HOSTS_FILE.read_bytes()
    signature, keys = _signed(document)
    if case == "unsigned":
        signature = None  # type: ignore[assignment]
    elif case == "tampered":
        document = document.replace(b"platform.claude.com", b"evil.example.com\x20\x20\x20")
    else:
        keys = {"another-key": next(iter(keys.values()))}
    with pytest.raises(VendorRulesInvalid):
        _ = load_vendor_hosts(document, signature, keys)


def test_eg4_the_shipped_list_is_signed_with_a_release_key() -> None:
    """SIGNKEY signed the list: the packaged hosts load, and are exactly the file's."""
    assert packaged(HOSTS_SIGNATURE_RESOURCE) is not None
    assert release_sign_in_hosts() == parse_vendor_hosts(HOSTS_FILE.read_bytes())[CLAUDE_PLAN_ROW]


@pytest.mark.parametrize(
    "host", ["10.0.0.1", "*.anthropic.com", "api.anthropic.com:443", "https://api.anthropic.com", "localhost", "",
             "API.anthropic.com"]
)
def test_eg5_only_host_names_can_be_listed(host: str) -> None:
    doc: dict[str, Any] = json.loads(HOSTS_FILE.read_text())
    doc["rows"][CLAUDE_PLAN_ROW]["sandbox"] = [host]
    with pytest.raises(VendorRulesInvalid):
        _ = parse_vendor_hosts(json.dumps(doc).encode())


class _Claim:
    def __init__(
        self, route: str | None, account: str | None, actor: str | None = "user-1", model: str = "plan-default"
    ) -> None:
        access = None if route is None else type("A", (), {"route": route, "account_id": account})()
        entry = type("P", (), {"access": access, "model": model})()
        self.spec = type("S", (), {"model": type("M", (), {"policy": [entry]})()})()
        self.run = None if actor is None else type("R", (), {"actor_id": actor})()


def test_eg6_only_the_actors_own_plan_pin_signs_in() -> None:
    own = own_login_of(_Claim("vendor_sandbox_login", "users/user-1/claude-plan"))  # type: ignore[arg-type]
    assert own is not None and (own.user_id, own.account_id) == ("user-1", "users/user-1/claude-plan")
    assert own_login_of(_Claim("gateway", None)) is None  # type: ignore[arg-type]
    assert own_login_of(_Claim(None, None)) is None  # type: ignore[arg-type]
    for claim in (_Claim("vendor_sandbox_login", "users/user-2/claude-plan"), _Claim("vendor_sandbox_login", None),
                  _Claim("vendor_sandbox_login", "users/user-1/claude-plan", actor=None)):
        with pytest.raises(SignInRefused):
            _ = own_login_of(claim)  # type: ignore[arg-type]


def test_r38_the_plan_runs_the_configured_claude_model_node_pinned() -> None:
    """ADR-MA-01 (R38-AUTHZ): the org's model groups hold on a person's own plan; an older pin keeps Claude's default."""
    claim = _Claim("vendor_sandbox_login", "users/user-1/claude-plan", model="claude-sonnet-4-5")
    own = own_login_of(claim)  # type: ignore[arg-type]
    assert own is not None and own.model == "claude-sonnet-4-5"
    legacy = own_login_of(_Claim("vendor_sandbox_login", "users/user-1/claude-plan"))  # type: ignore[arg-type]
    assert legacy is not None and legacy.model is None


def test_eg7_the_chat_line_carries_ids_state_reason_and_plan_only() -> None:
    line = progress_for(SignInProgress(EngineEventMeta("t", 1_000_001), "sin_1", "failed", "plan_not_allowed", "max"))
    assert line is not None
    assert (line.kind, line.text, dict(line.data)) == (
        "signin", "", {"attemptId": "sin_1", "state": "failed", "reasonCode": "plan_not_allowed", "plan": "max"})
