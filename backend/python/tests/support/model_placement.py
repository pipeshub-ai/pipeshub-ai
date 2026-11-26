"""MG-15 test seam: model calls that no placement rule refuses, for tests about something else (``app/utils/llm.py``
asks the placement verdicts before it builds any model)."""

from __future__ import annotations

import pytest

from app.utils.model_placement import ALLOW_EVERY_MODEL


def no_placement_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every model built in the test may be called."""
    reader = ALLOW_EVERY_MODEL.reader(None)
    monkeypatch.setattr("app.utils.llm.model_placement", lambda _config_service: reader)


@pytest.fixture(autouse=True)
def published_placement_without_pins(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """MG-15: every model built through ``app/utils/llm.py`` asks the placement verdicts first. A test about
    something else runs where no rule refuses a call; a module that tests placement itself sets
    ``PLACEMENT_UNDER_TEST = True``. ``tests/conftest.py`` applies the same default to the unit and eval trees; a
    suite outside ``tests/`` imports this one."""
    if getattr(request.module, "PLACEMENT_UNDER_TEST", False):
        return
    no_placement_pin(monkeypatch)
