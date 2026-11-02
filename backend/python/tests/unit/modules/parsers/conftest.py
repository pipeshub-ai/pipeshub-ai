import pytest


@pytest.fixture(autouse=True)
def _no_table_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parser tests run the rule-based table path; the learned detector needs a model download."""
    monkeypatch.setenv("PDF_TABLE_MODEL", "off")
