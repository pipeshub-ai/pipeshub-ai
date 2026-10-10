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

# Publishing 20,000 events and then watching for 20 seconds takes longer
# than the default 30-second test timeout the cluster job runs with.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(180)]


@pytest.fixture
async def setup(redis_available, unique_suffix, monkeypatch):  # noqa: ANN201
    host, port = redis_available
    config = RedisStreamsConfig(host=host, port=port)
    # The lane map is kept for record-events only, so the scenario uses it,
    # starting and ending with its streams and lane map gone.
    topic = "record-events"
    await report_scenario.remove(config, topic)
    yield config, topic, monkeypatch
    await report_scenario.remove(config, topic)


async def test_with_assigned_lanes_slack_is_not_held_behind_gitlab(setup, unique_suffix) -> None:
    config, topic, monkeypatch = setup
    report_scenario.configure(monkeypatch, topic, "assigned")

    outcome = await report_scenario.run(topic, config, group=f"{topic}-{unique_suffix}")

    print(outcome.describe())
    report_scenario.assert_fixed(outcome)


async def test_with_hashing_slack_waits_behind_gitlab(setup, unique_suffix) -> None:
    """The report as it was, with the switch set back to hashing."""
    config, topic, monkeypatch = setup
    report_scenario.configure(monkeypatch, topic, "hash")

    outcome = await report_scenario.run(topic, config, group=f"{topic}-{unique_suffix}")

    print(outcome.describe())
    report_scenario.assert_reproduced(outcome)


async def test_an_upgraded_install_spreads_new_work_from_the_first_publish(setup, unique_suffix) -> None:
    config, topic, monkeypatch = setup

    lanes, outcome = await report_scenario.run_upgrade(monkeypatch, topic, config, group=f"{topic}-{unique_suffix}")

    print(lanes, outcome.describe())
    report_scenario.assert_upgrade_separated_them(lanes, outcome)
