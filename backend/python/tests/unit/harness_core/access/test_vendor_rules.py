"""A1 VR (every vendor row) and SG (signed release data): one vector table with Node."""

from __future__ import annotations

import base64
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from harness_core.access.errors import VendorRuleRefused, VendorRulesInvalid
from harness_core.access.types import AccountKind, RouteKind
from harness_core.access.vendor_rules import (
    EXTENSION_SCHEMA,
    OFFERINGS,
    AccessCandidate,
    OrgAccessPolicy,
    VendorRules,
    decide,
    extend_vendor_rules,
    verify_release_document,
    verify_vendor_rules,
)
from harness_engines import vendor_rules as release

PYTHON = Path(__file__).resolve().parents[4]
REPO = PYTHON.parents[1]
VECTORS: dict[str, Any] = json.loads((REPO / "contracts" / "harness" / "vectors" / "vendor-rules.json").read_text())
RULES_FILE = PYTHON / "harness_engines" / "vendor_rules.json"
SIG_FILE = PYTHON / "harness_engines" / "vendor_rules.json.sig"
KEY_ID = "pipeshub-vendor-rules-a1"
# ARCHITECTURE §4.6.3's rows and the policy rules under it: each is the `row` of at least one case.
ARCHITECTURE_ROWS = {
    "OpenAI API key (org or user)",
    "ChatGPT Plus/Pro via Sign in with ChatGPT",
    "ChatGPT Business/Enterprise Codex access tokens",
    "Codex app-server chatgptAuthTokens mode",
    "Anthropic API key (org or user), gateway",
    "Anthropic API key, vendor-direct injected",
    "Bedrock / Vertex / Foundry for Claude: gateway only",
    "Claude plan in unmodified Claude Code, in-sandbox login",
    "Claude plan held, injected or used by any other harness or by our loop",
    "PipesHub paying for or reselling Claude usage (managed offering)",
    "Gemini API key, Vertex",
    "Gemini personal login outside Gemini CLI",
    "Gemini personal or Code Assist login in hosted Gemini CLI",
    "Consumer-plan routes off by default for enterprise tenants",
}


def _shipped() -> VendorRules:
    return release.release_vendor_rules()


def _candidate(raw: dict[str, str]) -> AccessCandidate:
    return AccessCandidate(raw["provider"], AccountKind(raw["accountKind"]), RouteKind(raw["route"]), raw["engine"])


def _policy(raw: dict[str, Any]) -> OrgAccessPolicy:
    return OrgAccessPolicy(frozenset(raw["disabledRules"]), bool(raw["consumerPlans"]))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_vr_every_vendor_row(case: dict[str, Any]) -> None:
    expect = case["expect"]
    candidate, policy = _candidate(case["candidate"]), _policy(case["policy"])
    if "allowed" in expect:
        assert decide(_shipped(), candidate, case["offering"], policy).id == expect["allowed"]
        return
    with pytest.raises(VendorRuleRefused) as refused:
        decide(_shipped(), candidate, case["offering"], policy)
    assert (refused.value.reason_code, refused.value.detail) == (expect["refused"], expect["rule"])


def test_vr19_every_row_decides_a_case_and_every_architecture_row_is_a_case() -> None:
    deciding = {c["expect"].get("allowed") or c["expect"].get("rule") for c in VECTORS["cases"]}
    assert _shipped().ids() <= deciding
    assert ARCHITECTURE_ROWS <= {c["row"] for c in VECTORS["cases"]}
    assert len({c["id"] for c in VECTORS["cases"]}) == len(VECTORS["cases"])


def _keypair() -> tuple[Ed25519PrivateKey, bytes]:
    private = Ed25519PrivateKey.generate()
    return private, private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _sign(private: Ed25519PrivateKey, document: bytes, key_id: str = KEY_ID) -> bytes:
    sig = base64.b64encode(private.sign(document)).decode()
    return json.dumps({"alg": "Ed25519", "keyId": key_id, "sig": sig}).encode()


DOCUMENT = RULES_FILE.read_bytes()


def _refusal(document: bytes | None, signature: bytes | None, keys: dict[str, bytes]) -> str:
    with pytest.raises(VendorRulesInvalid) as refused:
        verify_vendor_rules(document, signature, keys)
    assert refused.value.reason_code == "vendor_rules_invalid"
    assert refused.value.http_status == 503
    return refused.value.detail


def test_sg1_a_document_signed_by_a_trusted_key_loads() -> None:
    private, public = _keypair()
    rules = verify_vendor_rules(DOCUMENT, _sign(private, DOCUMENT), {KEY_ID: public})
    assert rules.version == json.loads(DOCUMENT)["version"]
    assert len(rules.rules) == len(json.loads(DOCUMENT)["rules"])


def test_sg2_a_tampered_document_is_refused() -> None:
    private, public = _keypair()
    signature = _sign(private, DOCUMENT)
    flipped = DOCUMENT.replace(b'"prohibited"', b'"allowed"   ', 1)
    assert flipped != DOCUMENT and len(flipped) == len(DOCUMENT)
    assert _refusal(flipped, signature, {KEY_ID: public}) == "bad_signature"
    one_byte = bytearray(DOCUMENT)
    one_byte[len(one_byte) // 2] ^= 0x01
    assert _refusal(bytes(one_byte), signature, {KEY_ID: public}) == "bad_signature"


def test_sg3_another_key_under_the_release_id_and_an_unknown_id_are_refused() -> None:
    private, _ = _keypair()
    _, trusted = _keypair()
    assert _refusal(DOCUMENT, _sign(private, DOCUMENT), {KEY_ID: trusted}) == "bad_signature"
    assert _refusal(DOCUMENT, _sign(private, DOCUMENT, "someone-else"), {KEY_ID: trusted}) == "unknown_key"


@pytest.mark.parametrize(
    ("document", "signature", "detail"),
    [
        (None, b"{}", "missing"),
        (DOCUMENT, None, "unsigned"),
        (DOCUMENT, b"not json", "unsigned"),
        (DOCUMENT, json.dumps({"alg": "RS256", "keyId": KEY_ID, "sig": "AA=="}).encode(), "unsigned"),
        (DOCUMENT, json.dumps({"alg": "Ed25519", "keyId": KEY_ID, "sig": "*not base64*"}).encode(), "unsigned"),
    ],
    ids=["no-document", "no-signature", "signature-not-json", "wrong-algorithm", "signature-not-base64"],
)
def test_sg4_missing_or_unsigned(document: bytes | None, signature: bytes | None, detail: str) -> None:
    _, public = _keypair()
    assert _refusal(document, signature, {KEY_ID: public}) == detail


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "rules": [*d["rules"], d["rules"][0]]},
        lambda d: {**d, "rules": [{**d["rules"][0], "status": "tolerated"}]},
        lambda d: {**d, "rules": [{**d["rules"][0], "accountKind": "org_magic"}]},
        lambda d: {**d, "rules": [{**d["rules"][0], "offering": ["everywhere"]}]},
        lambda d: {**d, "rules": [{**d["rules"][0], "widen": True}]},
        lambda d: {**d, "schema": "something/2"},
        lambda d: [d],
    ],
    ids=["duplicate-id", "unknown-status", "unknown-kind", "unknown-offering", "unknown-field", "schema", "not-object"],
)
def test_sg5_a_signed_document_that_is_not_a_rule_document_is_malformed(mutate: Any) -> None:  # noqa: ANN401
    private, public = _keypair()
    document = json.dumps(mutate(json.loads(DOCUMENT))).encode()
    assert _refusal(document, _sign(private, document), {KEY_ID: public}) == "malformed"


def test_sg6_the_shipped_rules_verify_against_the_shipped_key() -> None:
    rules = verify_vendor_rules(DOCUMENT, SIG_FILE.read_bytes(), release.RELEASE_KEYS)
    for name in release.extension_resources():
        signed = verify_release_document(release.packaged(name), release.packaged(f"{name}.sig"), release.RELEASE_KEYS)
        rules = extend_vendor_rules(rules, signed)
    assert rules == _shipped()
    assert json.loads(SIG_FILE.read_bytes())["keyId"] in release.RELEASE_KEYS


def test_sg7_the_release_key_is_code_not_configuration() -> None:
    """Importing the release module, loading the packaged rules and refusing a forged document read no environment."""
    assert list(inspect.signature(release.load_vendor_rules).parameters) == ["document", "signature"]
    probe = (
        "import json, os, sys\n"
        "read = []\n"
        "class Watched(dict):\n"
        "    def _note(self, key):\n"
        "        frame = sys._getframe(2)\n"
        "        while frame.f_code.co_filename.endswith(('os.py', '<frozen os>')):\n"
        "            frame = frame.f_back\n"
        "        name = frame.f_code.co_filename\n"
        "        if name.endswith('vendor_rules.py') or '/harness_core/access/' in name:\n"
        "            read.append(key)\n"
        "    def __getitem__(self, key):\n"
        "        self._note(key)\n"
        "        return super().__getitem__(key)\n"
        "    def get(self, key, default=None):\n"
        "        self._note(key)\n"
        "        return super().get(key, default)\n"
        "os.environ = Watched(os.environ)\n"
        "from harness_engines import vendor_rules as release\n"
        "from harness_core.access.errors import VendorRulesInvalid\n"
        "rules = release.release_vendor_rules()\n"
        "try:\n"
        "    release.load_vendor_rules(sys.argv[1].encode(), sys.argv[2].encode())\n"
        "    forged = 'accepted'\n"
        "except VendorRulesInvalid:\n"
        "    forged = 'refused'\n"
        "print(json.dumps({'read': read, 'forged': forged, 'rules': len(rules.ids()),"
        " 'config': sorted(m for m in sys.modules if 'configuration_service' in m)}))\n"
    )
    private, public = _keypair()
    document = DOCUMENT
    signature = _sign(private, document)
    env = {**os.environ, "PYTHONPATH": str(PYTHON), "VENDOR_RULES_KEY": base64.b64encode(public).decode(),
           "HARNESS_VENDOR_RULES_PUBLIC_KEY": base64.b64encode(public).decode()}
    done = subprocess.run(
        [sys.executable, "-c", probe, document.decode(), signature.decode()],
        capture_output=True, text=True, check=False, env=env, cwd=PYTHON,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen == {"read": [], "forged": "refused", "rules": len(_shipped().ids()), "config": []}
    node = (REPO / "backend/nodejs/apps/src/modules/agent-harness/domain/vendor-rules-release.ts").read_text()
    keys = dict(re.findall(r"'([a-z0-9-]+)':\s*'([A-Za-z0-9+/=]+)'", node))
    assert {k: base64.b64decode(v) for k, v in keys.items()} == dict(release.RELEASE_KEYS)


# An offering only an edition's overlay would declare: the core enumeration never names it.
EDITION_OFFERING = "edition_test_offering"


def _extension(**fields: Any) -> bytes:  # noqa: ANN401 - JSON fields
    body = {"schema": EXTENSION_SCHEMA, "version": "x1", "offering": EDITION_OFFERING, "rules": [], **fields}
    return json.dumps(body).encode()


@pytest.fixture
def edition_offering(monkeypatch: pytest.MonkeyPatch) -> str:
    from harness_core.access import vendor_rules as core

    monkeypatch.setattr(core, "OFFERINGS", (*OFFERINGS, EDITION_OFFERING))
    return EDITION_OFFERING


def test_sg8_an_extension_opens_only_its_rows_to_its_own_offering(edition_offering: str) -> None:
    base = _shipped()
    row = "anthropic-platform-paid-claude-code"
    extended = extend_vendor_rules(base, _extension(rules=[row]))
    assert extended.version == f"{base.version}+x1"
    assert extended.ids() == base.ids()
    changed = {r.id for r, b in zip(extended.rules, base.rules, strict=True) if r != b}
    assert changed == {row}
    candidate = AccessCandidate("anthropic", AccountKind.ORG_API_KEY, RouteKind.GATEWAY, "claude_code")
    assert decide(extended, candidate, edition_offering).id == row  # type: ignore[arg-type]
    for case in VECTORS["cases"]:
        policy, cand = _policy(case["policy"]), _candidate(case["candidate"])
        try:
            before = decide(base, cand, case["offering"], policy).id
        except VendorRuleRefused as refused:
            before = (refused.reason_code, refused.detail)
        try:
            after = decide(extended, cand, case["offering"], policy).id
        except VendorRuleRefused as refused:
            after = (refused.reason_code, refused.detail)
        assert after == before, case["id"]


@pytest.mark.parametrize(
    "document",
    [
        b"not json",
        _extension(rules=["anthropic-api-key-org"], widen=True),
        _extension(schema="something/2", rules=["anthropic-api-key-org"]),
        _extension(version="", rules=["anthropic-api-key-org"]),
        _extension(offering="oss", rules=["anthropic-api-key-org"]),
        _extension(offering="never_declared", rules=["anthropic-api-key-org"]),
        _extension(rules=[]),
        _extension(rules=["anthropic-api-key-org", "anthropic-api-key-org"]),
        _extension(rules=["a-row-the-release-lacks"]),
    ],
    ids=["not-json", "unknown-field", "schema", "no-version", "core-offering", "undeclared-offering", "no-rows",
         "duplicate-row", "unknown-row"],
)
def test_sg9_an_extension_that_could_change_a_core_answer_is_malformed(edition_offering: str, document: bytes) -> None:
    del edition_offering
    with pytest.raises(VendorRulesInvalid) as refused:
        extend_vendor_rules(_shipped(), document)
    assert refused.value.detail == "malformed"


def test_sg9_a_second_extension_for_the_same_offering_is_malformed(edition_offering: str) -> None:
    del edition_offering
    once = extend_vendor_rules(_shipped(), _extension(rules=["anthropic-api-key-org"]))
    with pytest.raises(VendorRulesInvalid):
        extend_vendor_rules(once, _extension(rules=["openai-api-key-org"]))


def test_sg10_the_release_loads_extensions_and_refuses_an_unsigned_one(
    edition_offering: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = _keypair()
    extension = _extension(rules=["anthropic-api-key-org"])
    files = {
        release.RULES_RESOURCE: DOCUMENT,
        release.SIGNATURE_RESOURCE: _sign(private, DOCUMENT),
        "vendor_rules.edition.json": extension,
        "vendor_rules.edition.json.sig": _sign(private, extension),
    }
    monkeypatch.setattr(release, "packaged", files.get)
    monkeypatch.setattr(release, "extension_resources", lambda: ("vendor_rules.edition.json",))
    monkeypatch.setattr("harness_engines.release_keys.trusted_release_keys", lambda: {KEY_ID: public})
    release._release.cache_clear()  # the release is cached per key set
    try:
        rules = release.release_vendor_rules()
        assert edition_offering in next(r for r in rules.rules if r.id == "anthropic-api-key-org").offering
        release._release.cache_clear()
        del files["vendor_rules.edition.json.sig"]
        with pytest.raises(VendorRulesInvalid) as refused:
            release.release_vendor_rules()
        assert refused.value.detail == "unsigned"
    finally:
        release._release.cache_clear()


def test_sg10_the_pattern_finds_extensions_and_not_the_rules() -> None:
    import fnmatch

    names = ["vendor_rules.json", "vendor_rules.json.sig", "vendor_rules.edition.json", "vendor_rules.edition.json.sig"]
    assert [n for n in names if fnmatch.fnmatchcase(n, release.EXTENSION_PATTERN)] == ["vendor_rules.edition.json"]
