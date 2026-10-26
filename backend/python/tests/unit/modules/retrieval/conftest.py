import pytest


@pytest.fixture
def narrow_fallback_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container search with its own wider retry and no move to the
    record-id filter: what these tests describe. The move is covered in
    test_narrow_access_fallback.py."""
    monkeypatch.setenv("SEARCH_NARROW_ACCESS_FALLBACK", "false")
