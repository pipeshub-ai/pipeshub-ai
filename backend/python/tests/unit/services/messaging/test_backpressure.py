import app.services.messaging.backpressure as backpressure_module
from app.services.messaging.backpressure import (
    BackpressureCoordinator,
    get_default_backpressure_coordinator,
    set_default_backpressure_coordinator,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestSignal:
    def test_not_paused_initially(self) -> None:
        coordinator = BackpressureCoordinator(clock=_FakeClock())
        assert coordinator.is_paused() is False
        assert coordinator.pause_remaining() == 0.0
        assert coordinator.paused_services == frozenset()

    def test_signal_pauses_for_retry_after_seconds(self) -> None:
        clock = _FakeClock()
        coordinator = BackpressureCoordinator(clock=clock)
        coordinator.signal("parsing", retry_after=5.0)
        assert coordinator.is_paused() is True
        assert coordinator.pause_remaining() == 5.0
        assert coordinator.paused_services == frozenset({"parsing"})

    def test_non_positive_retry_after_is_a_noop(self) -> None:
        coordinator = BackpressureCoordinator(clock=_FakeClock())
        coordinator.signal("parsing", retry_after=0.0)
        coordinator.signal("parsing", retry_after=-1.0)
        assert coordinator.is_paused() is False

    def test_pause_expires_after_retry_after_elapses(self) -> None:
        clock = _FakeClock()
        coordinator = BackpressureCoordinator(clock=clock)
        coordinator.signal("parsing", retry_after=5.0)

        clock.now += 5.0
        assert coordinator.is_paused() is False
        assert coordinator.pause_remaining() == 0.0

    def test_shorter_signal_does_not_shorten_an_active_pause(self) -> None:
        """A later, shorter Retry-After from the same service must not cut
        short a longer pause already in effect — the service could still be
        saturated even if this particular response asked for less."""
        clock = _FakeClock()
        coordinator = BackpressureCoordinator(clock=clock)
        coordinator.signal("parsing", retry_after=10.0)

        clock.now += 1.0
        coordinator.signal("parsing", retry_after=2.0)

        assert coordinator.pause_remaining() == 9.0  # still the original 10s deadline

    def test_longer_signal_extends_an_active_pause(self) -> None:
        clock = _FakeClock()
        coordinator = BackpressureCoordinator(clock=clock)
        coordinator.signal("parsing", retry_after=2.0)

        clock.now += 1.0
        coordinator.signal("parsing", retry_after=10.0)

        assert coordinator.pause_remaining() == 10.0

    def test_multiple_services_track_independently_max_wins(self) -> None:
        """Two different saturated services must keep the consumer paused
        until the *later* of the two deadlines, not just the most recent
        signal — resuming early would just re-admit work for whichever
        service is still saturated."""
        clock = _FakeClock()
        coordinator = BackpressureCoordinator(clock=clock)
        coordinator.signal("parsing", retry_after=3.0)
        coordinator.signal("docling", retry_after=10.0)

        assert coordinator.paused_services == frozenset({"parsing", "docling"})
        assert coordinator.pause_remaining() == 10.0

        clock.now += 3.0
        # parsing's pause has expired; docling's has not.
        assert coordinator.paused_services == frozenset({"docling"})
        assert coordinator.is_paused() is True

        clock.now += 7.0
        assert coordinator.is_paused() is False
        assert coordinator.paused_services == frozenset()


class TestDefaultCoordinator:
    def setup_method(self) -> None:
        set_default_backpressure_coordinator(None)

    def teardown_method(self) -> None:
        set_default_backpressure_coordinator(None)

    def test_creates_one_on_first_use(self) -> None:
        assert backpressure_module._default_coordinator is None
        coordinator = get_default_backpressure_coordinator()
        assert isinstance(coordinator, BackpressureCoordinator)

    def test_returns_the_same_instance_across_calls(self) -> None:
        """ParsingClient, DoclingClient, and EmbeddingServerEmbeddings must
        all observe signals from one another, so repeated lookups (from
        unrelated factories) must resolve to the same object."""
        first = get_default_backpressure_coordinator()
        second = get_default_backpressure_coordinator()
        assert first is second

    def test_set_default_overrides_the_singleton(self) -> None:
        override = BackpressureCoordinator()
        set_default_backpressure_coordinator(override)
        assert get_default_backpressure_coordinator() is override
