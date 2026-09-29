"""A pack waiting on unmerged demo data is not this checkout breaking that pack."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from integration_test_examples import (  # noqa: E402
    ERROR_LINE,
    _pull_request_is_open,
    _waits_on_unmerged_demo_data,
)

pytestmark = pytest.mark.unit

PULL = "https://github.com/pipeshub-ai/pipeshub-ai/pull/3585"
PINNED = f"| Jira | FIN-37, with the demo data in [pipeshub-ai#3585]({PULL}) |\n"
STDOUT = (
    "packs/finance: 3 cited records checked against acme-corp.yaml\n"
    "::error::packs/finance: cites FIN-37, which is not in the demo data (acme-corp.yaml)\n"
    "::error::packs/engineering: cites INC-2031, which is not in the demo data (acme-corp.yaml)\n"
    "::error::sdk-starter/README.md: broken link ./python/main.py\n"
    "3 problem(s)\n"
)


@pytest.fixture
def packs(tmp_path: Path) -> Path:
    for name, readme in (("finance", PINNED), ("engineering", "| Jira | INC-2031 |\n")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "README.md").write_text(readme, encoding="utf-8")
    return tmp_path


def this_checkouts(packs: Path) -> list[str]:
    return [p for p in ERROR_LINE.findall(STDOUT) if not _waits_on_unmerged_demo_data(packs, p)]


def test_an_open_pull_request_owns_its_packs_problems(packs, monkeypatch) -> None:
    monkeypatch.setattr("integration_test_examples._pull_request_is_open", lambda _number: True)
    assert this_checkouts(packs) == [
        "packs/engineering: cites INC-2031, which is not in the demo data (acme-corp.yaml)",
        "sdk-starter/README.md: broken link ./python/main.py",
    ]


def test_a_closed_pull_request_leaves_its_pack_to_this_checkout(packs, monkeypatch) -> None:
    monkeypatch.setattr("integration_test_examples._pull_request_is_open", lambda _number: False)
    assert len(this_checkouts(packs)) == 3


def test_unreachable_github_does_not_blame_this_checkout(monkeypatch) -> None:
    def refuse(*_args, **_kwargs) -> None:
        raise requests.RequestException("no network")

    monkeypatch.setattr("integration_test_examples.requests.get", refuse)
    _pull_request_is_open.cache_clear()
    try:
        assert _pull_request_is_open("3585") is True
    finally:
        _pull_request_is_open.cache_clear()
