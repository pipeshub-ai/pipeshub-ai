"""Strict OpenAPI audit of POST /api/v1/crawlingManager/:connector/:connectorId/schedule."""

from __future__ import annotations

from typing import Any

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    NO_JOB_BODY,
    REPEATING_SCHEDULE_TYPES,
    SCHEDULE_CONFIG_DEFAULTS,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    WRONG_CONNECTOR_SEGMENT,
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    bearer,
    error_code,
    error_message,
    once_schedule_body,
    repeating_schedule_config,
    request_as,
    schedule_body,
    validation_error_fields,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/schedule"

UNSCHEDULABLE = "This schedule can't be used: it never produces a run time."


def _config(schedule_type: str, **overrides: Any) -> dict[str, Any]:
    """A scheduleConfig of one type with fields overridden; a value of ... drops the field."""
    base = (
        once_schedule_body()["scheduleConfig"]
        if schedule_type == "once"
        else repeating_schedule_config(schedule_type)
    )
    merged = {**base, **overrides}
    return {key: value for key, value in merged.items() if value is not ...}


def _with_config(schedule_type: str, **overrides: Any) -> dict[str, Any]:
    return {"scheduleConfig": _config(schedule_type, **overrides)}


def test_schedule_once_job_is_created(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    body = once_schedule_body(priority=3, maxRetries=2, timeout=600000)

    resp = crawling_manager_client.schedule(seeded.connector_type, seeded.connector_id, body)

    assert resp.status_code == 201, resp.text[:500]
    payload = resp.json()
    assert payload["success"] is True
    data = payload["data"]
    assert data["connectorId"] == seeded.connector_id
    assert data["connector"] == seeded.connector_type
    assert data["jobId"]
    assert data["scheduleConfig"] == {**SCHEDULE_CONFIG_DEFAULTS, **body["scheduleConfig"]}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("schedule_type", REPEATING_SCHEDULE_TYPES)
def test_schedule_repeating_job_is_created(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
    schedule_type: str,
) -> None:
    seeded = seed_connector()
    body = schedule_body(schedule_type)
    expected_config = {**SCHEDULE_CONFIG_DEFAULTS, **body["scheduleConfig"]}

    resp = crawling_manager_client.schedule(seeded.connector_type, seeded.connector_id, body)

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    data = resp.json()["data"]
    assert data["scheduleConfig"] == expected_config
    # A repeating schedule's job id is BullMQ's, not the crawl-<type>-<id>-<org> of a one-time job.
    assert data["jobId"].startswith("repeat:")

    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 200, status.text[:500]
    job = status.json()["data"]
    assert job["id"] == data["jobId"]
    assert job["state"] == "delayed"
    assert job["delay"] > 0
    assert job["data"]["scheduleConfig"] == expected_config


def test_optional_fields_take_the_validator_defaults(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    body = _with_config("hourly", interval=...)

    resp = crawling_manager_client.schedule(seeded.connector_type, seeded.connector_id, body)

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    echoed = resp.json()["data"]["scheduleConfig"]
    assert echoed["interval"] == 1
    assert echoed["isEnabled"] is True
    assert echoed["timezone"] == "UTC"


def test_interval_takes_its_own_timezone_beside_the_minutes(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    inner = {"intervalMinutes": 525600, "timezone": "Europe/London"}

    resp = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, _with_config("interval", scheduleConfig=inner)
    )

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    echoed = resp.json()["data"]["scheduleConfig"]
    assert echoed["scheduleConfig"] == inner
    # The outer default is still filled in next to it.
    assert echoed["timezone"] == "UTC"


def test_a_new_schedule_replaces_the_existing_one(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector

    resp = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, schedule_body("daily")
    )

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    listed = crawling_manager_client.list_all()
    assert listed.status_code == 200, listed.text[:500]
    mine = [j for j in listed.json()["data"] if j["data"]["connectorId"] == seeded.connector_id]
    assert [j["data"]["scheduleConfig"]["scheduleType"] for j in mine] == ["daily"]


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"maxRetries": 2.5}, id="maxRetries"),
        # Validated as a number between 1000 and 600000, then never read.
        pytest.param({"timeout": 1000.5}, id="timeout"),
    ],
)
def test_fractional_number_fields_are_accepted(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
    extra: dict[str, Any],
) -> None:
    seeded = seed_connector()

    resp = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, once_schedule_body(**extra)
    )

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_fractional_priority_is_an_internal_error_and_drops_the_existing_schedule(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    # API bug: the validator takes any number from 1 to 10, the queue refuses a fraction,
    # and by then the connector's previous schedule has already been removed.
    seeded = scheduled_connector

    resp = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, once_schedule_body(priority=1.5)
    )

    assert resp.status_code == 500, resp.text[:500]
    assert error_code(resp) == "INTERNAL_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)

    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 404, status.text[:500]
    assert status.json() == NO_JOB_BODY


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(once_schedule_body(specAudit="x"), id="top-level"),
        pytest.param(_with_config("once", specAudit="x"), id="in-schedule-config"),
        # The spec's own shape for an interval before round 2: read by nothing, dropped silently.
        pytest.param(_with_config("interval", intervalMinutes=5), id="interval-minutes-one-level-up"),
        pytest.param(
            _with_config("interval", scheduleConfig={"intervalMinutes": 525600, "specAudit": "x"}),
            id="in-nested-interval-config",
        ),
    ],
)
def test_unknown_body_fields_are_dropped(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
    body: dict[str, Any],
) -> None:
    seeded = seed_connector()

    with outside_request_contract("zod objects strip keys they do not declare"):
        resp = crawling_manager_client.schedule(seeded.connector_type, seeded.connector_id, body)
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)

    echoed = resp.json()["data"]["scheduleConfig"]
    assert "specAudit" not in echoed
    assert "intervalMinutes" not in echoed
    assert "specAudit" not in echoed.get("scheduleConfig", {})


def test_connector_path_segment_is_ignored_in_favour_of_the_instance_type(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()

    resp = crawling_manager_client.schedule(
        WRONG_CONNECTOR_SEGMENT, seeded.connector_id, once_schedule_body()
    )

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["data"]["connector"] == seeded.connector_type
    # The job is stored under the instance's type, so only that segment finds it afterwards.
    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 200, status.text[:500]


def test_member_schedules_own_personal_connector(
    second_user: SecondUser, member_personal_connector: SeededConnector
) -> None:
    seeded = member_personal_connector

    resp = request_as(
        second_user,
        "POST",
        f"/{seeded.connector_type}/{seeded.connector_id}/schedule",
        json=once_schedule_body(),
    )

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["data"]["connectorId"] == seeded.connector_id


def test_schedule_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.schedule(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, once_schedule_body(), auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_schedule_without_crawl_write_scope_is_forbidden(
    crawling_manager_client: CrawlingManagerClient, token_without_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.schedule(
        SEED_CONNECTOR_TYPE,
        MISSING_CONNECTOR_ID,
        once_schedule_body(),
        auth=False,
        headers=bearer(token_without_crawl_scopes),
    )

    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:write" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE)


_VALIDATOR_REFUSALS = [
    pytest.param({}, "body.scheduleConfig", id="empty-object"),
    pytest.param({"priority": 5}, "body.scheduleConfig", id="schedule-config-missing"),
    pytest.param({"scheduleConfig": "daily"}, "body.scheduleConfig", id="schedule-config-string"),
    pytest.param([once_schedule_body()], "body", id="array"),
    pytest.param(None, "body.scheduleConfig", id="no-body"),
    pytest.param(
        {"scheduleConfig": {"scheduleType": "yearly"}},
        "body.scheduleConfig.scheduleType",
        id="schedule-type-unknown",
    ),
    pytest.param(
        {"scheduleConfig": {"hour": 2, "minute": 0}},
        "body.scheduleConfig.scheduleType",
        id="schedule-type-missing",
    ),
    pytest.param(once_schedule_body(priority=0), "body.priority", id="priority-below-min"),
    pytest.param(once_schedule_body(priority=11), "body.priority", id="priority-above-max"),
    pytest.param(once_schedule_body(priority="3"), "body.priority", id="priority-string"),
    pytest.param(once_schedule_body(maxRetries=-1), "body.maxRetries", id="max-retries-below-min"),
    pytest.param(once_schedule_body(maxRetries=11), "body.maxRetries", id="max-retries-above-max"),
    pytest.param(once_schedule_body(timeout=999), "body.timeout", id="timeout-below-min"),
    pytest.param(once_schedule_body(timeout=600001), "body.timeout", id="timeout-above-max"),
    pytest.param(
        _with_config("daily", timezone=5), "body.scheduleConfig.timezone", id="timezone-number"
    ),
    pytest.param(
        _with_config("daily", isEnabled="yes"), "body.scheduleConfig.isEnabled", id="is-enabled-string"
    ),
    pytest.param(_with_config("hourly", minute=...), "body.scheduleConfig.minute", id="hourly-minute-missing"),
    pytest.param(_with_config("hourly", minute=60), "body.scheduleConfig.minute", id="hourly-minute-60"),
    pytest.param(_with_config("hourly", interval=0), "body.scheduleConfig.interval", id="hourly-interval-0"),
    pytest.param(_with_config("hourly", interval=25), "body.scheduleConfig.interval", id="hourly-interval-25"),
    pytest.param(_with_config("daily", hour=...), "body.scheduleConfig.hour", id="daily-hour-missing"),
    pytest.param(_with_config("daily", hour=24), "body.scheduleConfig.hour", id="daily-hour-24"),
    pytest.param(_with_config("daily", hour=-1), "body.scheduleConfig.hour", id="daily-hour-negative"),
    pytest.param(
        _with_config("weekly", daysOfWeek=...), "body.scheduleConfig.daysOfWeek", id="weekly-days-missing"
    ),
    pytest.param(
        _with_config("weekly", daysOfWeek=[]), "body.scheduleConfig.daysOfWeek", id="weekly-days-empty"
    ),
    pytest.param(
        _with_config("weekly", daysOfWeek=[7]), "body.scheduleConfig.daysOfWeek.0", id="weekly-day-7"
    ),
    pytest.param(
        _with_config("monthly", dayOfMonth=0), "body.scheduleConfig.dayOfMonth", id="monthly-day-0"
    ),
    pytest.param(
        _with_config("monthly", dayOfMonth=32), "body.scheduleConfig.dayOfMonth", id="monthly-day-32"
    ),
    pytest.param(
        _with_config("custom", cronExpression=...),
        "body.scheduleConfig.cronExpression",
        id="custom-cron-missing",
    ),
    pytest.param(
        _with_config("custom", cronExpression="0 0 1 1"),
        "body.scheduleConfig.cronExpression",
        id="custom-cron-four-fields",
    ),
    pytest.param(
        _with_config("custom", cronExpression="0 0 0 1 1 *"),
        "body.scheduleConfig.cronExpression",
        id="custom-cron-six-fields",
    ),
    pytest.param(
        _with_config("once", scheduledTime=...),
        "body.scheduleConfig.scheduledTime",
        id="once-time-missing",
    ),
    # An offset is valid ISO 8601 but the validator takes the "Z" form only.
    pytest.param(
        _with_config("once", scheduledTime="2099-01-01T00:00:00+00:00"),
        "body.scheduleConfig.scheduledTime",
        id="once-time-with-offset",
    ),
    pytest.param(
        _with_config("once", scheduledTime="2099-01-01"),
        "body.scheduleConfig.scheduledTime",
        id="once-date-only",
    ),
    pytest.param(
        _with_config("once", scheduledTime="tomorrow"),
        "body.scheduleConfig.scheduledTime",
        id="once-not-a-date",
    ),
    pytest.param(
        {"scheduleConfig": {"scheduleType": "interval", "intervalMinutes": 5}},
        "body.scheduleConfig.scheduleConfig",
        id="interval-minutes-one-level-up-only",
    ),
    pytest.param(
        _with_config("interval", scheduleConfig={}),
        "body.scheduleConfig.scheduleConfig.intervalMinutes",
        id="interval-minutes-missing",
    ),
    pytest.param(
        _with_config("interval", scheduleConfig={"intervalMinutes": 0}),
        "body.scheduleConfig.scheduleConfig.intervalMinutes",
        id="interval-minutes-0",
    ),
    pytest.param(
        _with_config("interval", scheduleConfig={"intervalMinutes": 1.5}),
        "body.scheduleConfig.scheduleConfig.intervalMinutes",
        id="interval-minutes-fraction",
    ),
    pytest.param(
        _with_config("interval", scheduleConfig={"intervalMinutes": 525601}),
        "body.scheduleConfig.scheduleConfig.intervalMinutes",
        id="interval-minutes-above-max",
    ),
    pytest.param(
        _with_config("interval", scheduleConfig={"intervalMinutes": 5, "timezone": 5}),
        "body.scheduleConfig.scheduleConfig.timezone",
        id="interval-timezone-number",
    ),
]


@pytest.mark.parametrize(("body", "field"), _VALIDATOR_REFUSALS)
def test_validator_refuses_body(
    crawling_manager_client: CrawlingManagerClient, body: Any, field: str
) -> None:
    # The validator runs before the connector is looked up, so no connector has to exist.
    resp = crawling_manager_client.schedule(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, body)

    assert resp.status_code == 400, resp.text[:500]
    assert validation_error_fields(resp) == [field], resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "message", "spec_forbids"),
    [
        pytest.param(
            once_schedule_body(days_ahead=-1),
            "Scheduled time must be in the future",
            False,
            id="scheduled-time-in-the-past",
        ),
        pytest.param(
            _with_config("daily", timezone="Mars/Phobos"), UNSCHEDULABLE, False, id="timezone-unknown"
        ),
        pytest.param(
            _with_config("custom", cronExpression="a b c d e"), UNSCHEDULABLE, False, id="cron-unparseable"
        ),
        # The validator takes any number in range; a fraction then makes an unusable cron pattern.
        pytest.param(_with_config("hourly", minute=30.5), UNSCHEDULABLE, True, id="hourly-minute-fraction"),
        pytest.param(_with_config("hourly", interval=1.5), UNSCHEDULABLE, True, id="hourly-interval-fraction"),
        pytest.param(_with_config("daily", hour=1.5), UNSCHEDULABLE, True, id="daily-hour-fraction"),
        pytest.param(
            _with_config("weekly", daysOfWeek=[1.5]), UNSCHEDULABLE, True, id="weekly-day-fraction"
        ),
        pytest.param(
            _with_config("monthly", dayOfMonth=1.5), UNSCHEDULABLE, True, id="monthly-day-fraction"
        ),
    ],
)
def test_scheduler_refuses_schedule_and_keeps_the_existing_one(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    body: dict[str, Any],
    message: str,
    spec_forbids: bool,
) -> None:
    seeded = scheduled_connector

    resp = crawling_manager_client.schedule(seeded.connector_type, seeded.connector_id, body)

    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert (error_message(resp) or "").startswith(message), resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    if spec_forbids:
        assert_spec_forbids_request(resp, ROUTE)

    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 200, status.text[:500]
    assert status.json()["data"]["data"]["scheduleConfig"]["scheduleType"] == "once"


def test_disabled_schedule_is_refused_and_removes_the_existing_one(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector

    resp = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, _with_config("daily", isEnabled=False)
    )

    assert resp.status_code == 400, resp.text[:500]
    assert error_message(resp) == "Cannot schedule a disabled job"
    assert_strict_openapi_exchange(resp, ROUTE)

    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 404, status.text[:500]
    assert status.json() == NO_JOB_BODY


def test_schedule_for_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.schedule(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, once_schedule_body()
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_scheduling_admin_team_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
    second_user: SecondUser,
) -> None:
    # The connector service hides another user's team connector from a member as a 404,
    # so Node's own "team connectors need an admin" 403 is never reached.
    seeded = seed_connector()

    resp = request_as(
        second_user,
        "POST",
        f"/{seeded.connector_type}/{seeded.connector_id}/schedule",
        json=once_schedule_body(),
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 404, status.text[:500]
    assert status.json() == NO_JOB_BODY


def test_admin_scheduling_a_members_personal_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient, member_personal_connector: SeededConnector
) -> None:
    # Same for the "personal connectors belong to their creator" 403: the lookup 404s first.
    seeded = member_personal_connector

    resp = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, once_schedule_body()
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("connector", "connector_id"),
    [(SEED_CONNECTOR_TYPE, UNSAFE_CONNECTOR_ID), (UNSAFE_CONNECTOR_ID, MISSING_CONNECTOR_ID)],
    ids=["connector-id", "connector"],
)
def test_unsafe_path_segment_is_rejected_before_auth(
    crawling_manager_client: CrawlingManagerClient, connector: str, connector_id: str
) -> None:
    resp = crawling_manager_client.schedule(
        connector, connector_id, once_schedule_body(), auth=False
    )

    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
