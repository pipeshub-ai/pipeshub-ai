"""A Jira account in a legacy zone name (Asia/Calcutta) gets its own timezone, not UTC.

Debian 13 moved the legacy names to tzdata-legacy, which the images do not install, so the system
tz database has Asia/Kolkata but not Asia/Calcutta. zoneinfo then needs the ``tzdata`` package,
which used to come in only through kombu 5.6. These tests hide the system database, as on Debian 13.
"""

import logging
import zoneinfo
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector

LEGACY_NAMES = ["Asia/Calcutta", "US/Pacific", "Asia/Saigon", "Europe/Kiev"]


@pytest.fixture
def no_system_tz_database() -> Iterator[None]:
    saved = zoneinfo.TZPATH
    zoneinfo.reset_tzpath(to=[])
    zoneinfo.ZoneInfo.clear_cache()
    try:
        yield
    finally:
        zoneinfo.reset_tzpath(to=saved)
        zoneinfo.ZoneInfo.clear_cache()


def _connector() -> JiraConnector:
    return JiraConnector(
        logging.getLogger("test.jira.tz"), MagicMock(), MagicMock(), MagicMock(get_config=AsyncMock()),
        "conn-jira-1", "team", "user-1",
    )


def _myself(tz_name: str) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.json = MagicMock(return_value={"emailAddress": "a@b.com", "accountId": "me", "timeZone": tz_name})
    resp.text = MagicMock(return_value="")
    return resp


@pytest.mark.parametrize("name", LEGACY_NAMES)
def test_a_legacy_zone_name_resolves_without_the_system_database(no_system_tz_database: None, name: str) -> None:
    assert zoneinfo.ZoneInfo(name).key == name


def test_an_account_in_asia_calcutta_gets_its_own_timezone(no_system_tz_database: None) -> None:
    connector = _connector()

    connector._cache_authenticated_jira_profile(_myself("Asia/Calcutta"))

    assert connector._jql_timezone_known is True
    assert datetime(2026, 1, 1, tzinfo=connector._jql_timezone).utcoffset() == timedelta(hours=5, minutes=30)


def test_its_jql_cut_is_written_in_ist_with_no_widening(no_system_tz_database: None) -> None:
    connector = _connector()
    connector._cache_authenticated_jira_profile(_myself("Asia/Calcutta"))
    epoch_ms = int(datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc).timestamp() * 1000)

    assert connector._jql_datetime(epoch_ms) == "2026-11-21 11:30"
