"""Unit tests for app.plugins.telemetry.modules.connector_metrics."""

from app.plugins.telemetry.backend import METRICS_BACKEND
from app.plugins.telemetry.modules import connector_metrics
from app.plugins.telemetry.modules.connector_metrics import set_connector_active


class TestSetConnectorActive:
    def test_tuple_keys_carry_connector_and_domain(self):
        set_connector_active({("gmail", "acme.io"): 3})

        assert (
            'pipeshub_connector_active{connector="gmail",domain="acme.io"} 3.0'
            in METRICS_BACKEND.serialize()
        )

    def test_plain_string_key_defaults_domain_to_unknown(self):
        set_connector_active({"slack": 2})

        assert (
            'pipeshub_connector_active{connector="slack",domain="unknown"} 2.0'
            in METRICS_BACKEND.serialize()
        )

    def test_falsy_connector_and_domain_become_unknown(self):
        set_connector_active({("", None): 1})

        assert (
            'pipeshub_connector_active{connector="unknown",domain="unknown"} 1.0'
            in METRICS_BACKEND.serialize()
        )

    def test_refresh_replaces_stale_series(self):
        set_connector_active({("stale_connector", "x.io"): 5})
        set_connector_active({("fresh_connector", "y.io"): 1})

        text = METRICS_BACKEND.serialize()
        assert 'connector="stale_connector"' not in text
        assert (
            'pipeshub_connector_active{connector="fresh_connector",domain="y.io"} 1.0'
            in text
        )


def _records() -> dict[str, float]:
    prefix = "pipeshub_usage_records{"
    return {
        line[len(prefix) - 1:].rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
        for line in METRICS_BACKEND.serialize().splitlines()
        if line.startswith(prefix)
    }


def _series(connector: str, status: str, org: str, domain: str = "unknown") -> str:
    return f'{{connector_type="{connector}",domain="{domain}",indexing_status="{status}",org="{org}"}}'


class TestSetRecords:
    def setup_method(self) -> None:
        connector_metrics.forget_records()

    def teardown_method(self) -> None:
        connector_metrics.forget_records()

    def test_one_series_per_connector_type_and_status_of_an_org(self) -> None:
        connector_metrics.set_records("o1", "acme.io", {("DRIVE", "COMPLETED"): 7, ("DRIVE", "FAILED"): 2, ("KB", "COMPLETED"): 1})

        assert _records() == {
            _series("DRIVE", "COMPLETED", "o1", "acme.io"): 7.0,
            _series("DRIVE", "FAILED", "o1", "acme.io"): 2.0,
            _series("KB", "COMPLETED", "o1", "acme.io"): 1.0,
        }

    def test_a_new_count_replaces_the_orgs_old_one_and_leaves_other_orgs_alone(self) -> None:
        connector_metrics.set_records("o1", "unknown", {("DRIVE", "QUEUED"): 5})
        connector_metrics.set_records("o2", "unknown", {("SLACK", "COMPLETED"): 3})
        connector_metrics.set_records("o1", "unknown", {("DRIVE", "COMPLETED"): 5})

        assert _records() == {_series("DRIVE", "COMPLETED", "o1"): 5.0, _series("SLACK", "COMPLETED", "o2"): 3.0}

    def test_an_org_with_no_records_left_has_no_series(self) -> None:
        connector_metrics.set_records("o1", "unknown", {("DRIVE", "COMPLETED"): 5})
        connector_metrics.set_records("o1", "unknown", {})

        assert _records() == {}

    def test_orgs_past_the_cap_are_added_together_instead_of_each_getting_series(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_metrics, "MAX_ORGS", 2)
        for org in ("o1", "o2", "o3", "o4"):
            connector_metrics.set_records(org, "unknown", {("DRIVE", "COMPLETED"): 10})

        assert _records() == {
            _series("DRIVE", "COMPLETED", "o1"): 10.0,
            _series("DRIVE", "COMPLETED", "o2"): 10.0,
            _series("DRIVE", "COMPLETED", "other", "other"): 20.0,
        }

    def test_an_org_not_counted_for_a_day_drops_out(self, monkeypatch) -> None:
        clock = [1000.0]
        monkeypatch.setattr(connector_metrics.time, "monotonic", lambda: clock[0])
        connector_metrics.set_records("gone", "unknown", {("DRIVE", "COMPLETED"): 4})
        clock[0] += connector_metrics.STALE_AFTER_SECONDS + 1
        connector_metrics.set_records("o1", "unknown", {("KB", "COMPLETED"): 1})

        assert _records() == {_series("KB", "COMPLETED", "o1"): 1.0}

    def test_the_snapshot_event_sets_the_gauge_and_carries_no_record_id(self) -> None:
        from app import platform_events
        from app.platform_events import catalog

        platform_events.emit(
            "records.snapshot.taken",
            org_id="o9", email_domain="acme.io", records_by_connector_status={("JIRA", "IN_PROGRESS"): 12}, complete=False,
        )

        assert _records() == {_series("JIRA", "IN_PROGRESS", "o9", "acme.io"): 12.0}
        assert not [f for f in catalog.EVENTS["records.snapshot.taken"].fields if "record_id" in f]
