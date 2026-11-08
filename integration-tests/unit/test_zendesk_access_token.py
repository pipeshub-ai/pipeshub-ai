"""How the Zendesk suite reads the token the workflow minted."""

from __future__ import annotations

from pathlib import Path

import pytest

from connectors.zendesk import conftest as zendesk
from helper.source_credentials import REQUIRE_ENV


def test_a_minted_token_is_read_from_its_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "zendesk-access-token"
    token_file.write_text("minted-token\n")
    monkeypatch.setenv("ZENDESK_TEST_ACCESS_TOKEN_FILE", str(token_file))

    assert zendesk._access_token() == "minted-token"


def test_a_failed_mint_fails_the_suite_and_points_at_the_mint_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "ZENDESK_TEST_ACCESS_TOKEN_FILE", str(tmp_path / "never-written")
    )
    monkeypatch.setenv(REQUIRE_ENV, "1")

    with pytest.raises(pytest.fail.Exception) as failure:
        zendesk._access_token()

    message = str(failure.value)
    assert "was not minted" in message
    assert "'Mint Zendesk test token'" in message
    assert "ZENDESK_TEST_CLIENT_SECRET" in message


def test_a_failed_mint_skips_the_suite_outside_a_required_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "ZENDESK_TEST_ACCESS_TOKEN_FILE", str(tmp_path / "never-written")
    )
    monkeypatch.delenv(REQUIRE_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        zendesk._access_token()
