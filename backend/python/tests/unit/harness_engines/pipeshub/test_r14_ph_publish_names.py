"""R14-PUB-4 (WP-67 ``ph publish`` x WP-69 SEC-FS-02): ``ph`` holds a published name to the shared safe-name rules.

``ph`` runs in the sandbox on the standard library alone, so it cannot import ``harness_core.workspace.safe_names``;
it carries the same rules, and the shared vectors (``contracts/harness/vectors/safe-names.json``) pin both, as they
pin Node. A refused name is refused before anything is copied or asked, with the same reason the gateway gives.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core.workspace.safe_names import UnsafeName, check_name
from harness_engines.pipeshub import ph
from tests.unit.harness_engines.pipeshub.test_c312_ph_ask_publish import (
    run_folder,  # noqa: F401 - the fixture
)
from tests.unit.harness_engines.pipeshub.test_ph_cli import (
    Gateway,
    folder,  # noqa: F401 - the fixture
    gateway,  # noqa: F401 - the fixture
    run,
)

VECTORS = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/safe-names.json").read_text(encoding="utf-8")
)
# One name is a one-segment path: every name vector without a forward slash holds for ph's check too.
NAMES = [v for v in VECTORS["names"]["accepted"] if "/" not in v[0]]
NAMES_REFUSED = [v for v in VECTORS["names"]["refused"] if "/" not in v[0]]


@pytest.mark.parametrize(("raw", "canonical"), [*VECTORS["outputPaths"]["accepted"], *NAMES])
def test_r14_pub_4_ph_takes_what_the_shared_check_takes_in_nfc(raw: str, canonical: str) -> None:
    assert ph.output_name("ignored", raw) == canonical


@pytest.mark.parametrize(("raw", "reason"), [*VECTORS["outputPaths"]["refused"], *NAMES_REFUSED])
def test_r14_pub_4_ph_refuses_what_the_shared_check_refuses_with_its_reason(raw: str, reason: str) -> None:
    assert ph.unsafe_output_reason(raw) == reason
    with pytest.raises(ph.PhError) as refused:
        _ = ph.output_name("ignored", raw)
    assert refused.value.code == ph.EX_USAGE


@pytest.mark.parametrize("raw", ["CON", "con.txt", "a b.md", "x" * 255, "x" * 256, "Nul.tar.gz", "COM¹", "．．"])
def test_r14_pub_4_ph_and_the_shared_check_agree_beyond_the_vectors(raw: str) -> None:
    try:
        expected: str | None = check_name(raw)
    except UnsafeName as refused:
        expected = None
        assert ph.unsafe_output_reason(raw) == refused.reason
    if expected is not None:
        assert ph.output_name("ignored", raw) == expected


@pytest.mark.parametrize(("name", "said"), [("CON.txt", "device name on Windows"), ("report.", "ends with a dot")])
def test_r14_pub_4_ph_publish_refuses_before_copying_or_asking(
    gateway: tuple[Gateway, str], run_folder: Path, name: str, said: str  # noqa: F811
) -> None:
    state, url = gateway
    (run_folder / "workspace" / "report.md").write_text("x")
    code, out, err = run(["publish", "report.md", "--as", name], url)
    assert (code, out) == (ph.EX_USAGE, "")
    assert said in err, err
    assert state.requests == []
    assert not (run_folder / "workspace" / "out").exists()
