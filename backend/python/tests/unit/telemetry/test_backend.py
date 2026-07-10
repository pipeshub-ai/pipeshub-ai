"""Unit tests for app.telemetry.backend — the metrics backend abstraction."""

from app.telemetry.backend import METRICS_BACKEND, MetricsBackend, PrometheusBackend


class TestPrometheusBackendCounter:
    def test_inc_defaults_to_one(self):
        backend = PrometheusBackend()
        counter = backend.counter("t_counter_a", "help", ["kind"])

        counter.inc("x")

        assert 't_counter_a_total{kind="x"} 1.0' in backend.serialize()

    def test_inc_with_explicit_value(self):
        backend = PrometheusBackend()
        counter = backend.counter("t_counter_b", "help", ["kind"])

        counter.inc("x", value=2.5)
        counter.inc("x", value=0.5)

        assert 't_counter_b_total{kind="x"} 3.0' in backend.serialize()

    def test_series_are_independent_per_label_value(self):
        backend = PrometheusBackend()
        counter = backend.counter("t_counter_c", "help", ["kind"])

        counter.inc("a")
        counter.inc("b", value=2)

        text = backend.serialize()
        assert 't_counter_c_total{kind="a"} 1.0' in text
        assert 't_counter_c_total{kind="b"} 2.0' in text


class TestPrometheusBackendGauge:
    def test_set_value(self):
        backend = PrometheusBackend()
        gauge = backend.gauge("t_gauge_a", "help", ["org"])

        gauge.set("o1", value=7)
        gauge.set("o2", value=0)

        text = backend.serialize()
        assert 't_gauge_a{org="o1"} 7.0' in text
        assert 't_gauge_a{org="o2"} 0.0' in text

    def test_clear_drops_all_series(self):
        backend = PrometheusBackend()
        gauge = backend.gauge("t_gauge_b", "help", ["org"])

        gauge.set("stale", value=1)
        gauge.clear()
        gauge.set("fresh", value=2)

        text = backend.serialize()
        assert 'org="stale"' not in text
        assert 't_gauge_b{org="fresh"} 2.0' in text


class TestPrometheusBackendHistogram:
    def test_observe_fills_buckets_count_and_sum(self):
        backend = PrometheusBackend()
        histogram = backend.histogram(
            "t_hist_a", "help", ["route"], buckets=(0.1, 1.0, 10.0)
        )

        histogram.observe("/x", value=0.05)
        histogram.observe("/x", value=5.0)

        text = backend.serialize()
        bucket_lines = [
            line for line in text.splitlines() if line.startswith("t_hist_a_bucket")
        ]
        le_01 = next(line for line in bucket_lines if 'le="0.1"' in line)
        le_10 = next(line for line in bucket_lines if 'le="10.0"' in line)
        assert 'route="/x"' in le_01 and le_01.endswith(" 1.0")
        assert 'route="/x"' in le_10 and le_10.endswith(" 2.0")
        assert 't_hist_a_count{route="/x"} 2.0' in text
        assert 't_hist_a_sum{route="/x"} 5.05' in text


class TestRegistryIsolation:
    def test_each_backend_has_its_own_registry(self):
        one = PrometheusBackend()
        other = PrometheusBackend()

        one.counter("t_isolated", "help", ["k"]).inc("v")

        assert "t_isolated_total" in one.serialize()
        assert "t_isolated_total" not in other.serialize()

    def test_same_metric_name_in_two_backends_does_not_raise(self):
        # A double import must not raise duplicate-registration errors.
        PrometheusBackend().counter("t_dup", "help", [])
        PrometheusBackend().counter("t_dup", "help", [])

    def test_module_level_singleton_is_a_metrics_backend(self):
        assert isinstance(METRICS_BACKEND, MetricsBackend)
