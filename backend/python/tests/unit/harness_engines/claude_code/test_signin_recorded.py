"""SP-1..SP-7 (SIGNIN-1) on the recorded run of the admitted Claude Code against the fake Anthropic (fixtures/signin)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import harness_engines
from tests.unit.harness_engines.claude_code.signin_lab.conformance import CHECKS, Check
from tests.unit.harness_engines.claude_code.signin_lab.lab import FIXTURES


def _recorded() -> dict[str, Any]:
    return json.loads((FIXTURES / "recorded.json").read_text())


@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.__name__)
def test_recorded_login_flow(check: Check) -> None:
    check(_recorded())


def test_provenance_names_the_admitted_version() -> None:
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    allowlist = json.loads(
        (Path(harness_engines.__file__).parent / "allowlist.json").read_text()
    )
    claude = next(e for e in allowlist["engines"] if e["kind"] == "claude_code")
    assert provenance["version"] == claude["version"]
    assert provenance["binaryDigest"] == claude["binaryDigest"]
