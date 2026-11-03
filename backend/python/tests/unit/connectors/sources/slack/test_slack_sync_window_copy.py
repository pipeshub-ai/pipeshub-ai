"""The "Sync Messages From" filter must tell admins what happens when it is not set.

Both Slack connectors fall back to the last 30 days of history when the
``sync_window`` filter is absent (``_compute_sync_window_oldest``). An admin who
clears the filter could reasonably expect "no limit", so the help text shown
next to the filter has to state the fallback. These tests read the fallback from
the code itself, so the copy and the behaviour cannot drift apart silently.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.connectors.sources.slack.team.connector import SlackConnector

SECONDS_PER_DAY = 86400


def _sync_window_field(connector_cls: type) -> dict:
    fields = connector_cls._connector_metadata["config"]["filters"]["sync"]["schema"]["fields"]
    return next(f for f in fields if f["name"] == "sync_window")


def _fallback_days(connector_cls: type) -> int:
    """How many days back the connector syncs when no sync_window filter is set."""
    now = 1_800_000_000.0
    stub = SimpleNamespace(sync_filters=FilterCollection())
    with patch.object(time, "time", return_value=now):
        oldest = float(connector_cls._compute_sync_window_oldest(stub))
    return round((now - oldest) / SECONDS_PER_DAY)


@pytest.mark.parametrize("connector_cls", [SlackIndividualConnector, SlackConnector])
def test_sync_window_help_text_states_the_fallback_window(connector_cls: type) -> None:
    days = _fallback_days(connector_cls)
    description = _sync_window_field(connector_cls)["description"]

    assert f"last {days} days" in description
    assert "not set" in description


@pytest.mark.parametrize("connector_cls", [SlackIndividualConnector, SlackConnector])
def test_sync_window_default_operator_matches_the_fallback(connector_cls: type) -> None:
    """The operator the UI pre-selects is the same window the backend uses when unset."""
    days = _fallback_days(connector_cls)

    assert _sync_window_field(connector_cls)["defaultOperator"] == f"last_{days}_days"


@pytest.mark.parametrize("connector_cls", [SlackIndividualConnector, SlackConnector])
def test_sync_window_fallback_operator_matches_the_fallback(connector_cls: type) -> None:
    """The panel shows ``fallbackOperator`` while the filter is cleared, so it must
    name the window the backend really uses when the filter is not set."""
    days = _fallback_days(connector_cls)

    assert _sync_window_field(connector_cls).get("fallbackOperator") == f"last_{days}_days"
