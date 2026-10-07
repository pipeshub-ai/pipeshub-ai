"""The community report on a real standalone Redis: a Slack connector stuck
behind a GitLab backlog because both hashed to the same lane.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d

See ``report_scenario.py`` for the scenario. The cluster run is
``tests/integration/redis_cluster/test_assigned_lanes_report_cluster_it.py``.
"""
from __future__ import annotations

import pytest

from app.services.messaging.config import RedisStreamsConfig
from tests.integration.messaging import report_scenario

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def setup(redis_available, unique_suffix, monkeypatch):  # noqa: ANN201
    host, port = redis_available
    config = RedisStreamsConfig(host=host, port=port)
    topic = f"record-events-report-{unique_suffix}"
    yield config, topic, monkeypatch
    await report_scenario.remove(config, topic)


async def test_with_assigned_lanes_slack_is_not_held_behind_gitlab(setup) -> None:
    config, topic, monkeypatch = setup
    report_scenario.configure(monkeypatch, topic, "assigned")

    outcome = await report_scenario.run(topic, config, group=f"{topic}-group")

    print(outcome.describe())
    report_scenario.assert_fixed(outcome)


async def test_with_hashing_slack_waits_behind_gitlab(setup) -> None:
    """The report as it was, with the switch set back to hashing."""
    config, topic, monkeypatch = setup
    report_scenario.configure(monkeypatch, topic, "hash")

    outcome = await report_scenario.run(topic, config, group=f"{topic}-group")

    print(outcome.describe())
    report_scenario.assert_reproduced(outcome)


async def test_an_upgraded_install_has_its_colliding_connectors_separated(setup) -> None:
    config, topic, monkeypatch = setup

    lanes, outcome = await report_scenario.run_upgrade(monkeypatch, topic, config, group=f"{topic}-group")

    print(lanes, outcome.describe())
    report_scenario.assert_upgrade_separated_them(lanes, outcome)
