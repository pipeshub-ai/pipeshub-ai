"""Unit tests for the per-user record access poll (no live services).

The permission suites assert "this person can't open it" by waiting for a
denial. These pin that only a real denial ends that wait: an error status or an
expired login must not be read as "access removed".
"""

from __future__ import annotations

import pytest

from helper.record_access import AccessDenied, access_matches, wait_for_record_access
from helper.second_user import SecondUser

pytestmark = pytest.mark.unit

USER = SecondUser(
    user_id="u1", graph_id="g1", email="reader@example.com",
    token="t", base_url="http://pipeshub.test", timeout=5,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _probe(statuses: list[int]):
    calls: list[str] = []

    def probe(_user: SecondUser, record_id: str) -> int:
        calls.append(record_id)
        return statuses[min(len(calls), len(statuses)) - 1]

    return probe, calls


class TestAccessMatches:
    @pytest.mark.parametrize("status", [403, 404])
    def test_denials_count_as_no_access(self, status):
        assert access_matches(status, expect_access=False)
        assert not access_matches(status, expect_access=True)

    @pytest.mark.parametrize("status", [401, 500, 502, 503])
    def test_errors_prove_neither_answer(self, status):
        assert not access_matches(status, expect_access=False)
        assert not access_matches(status, expect_access=True)

    def test_only_200_counts_as_access(self):
        assert access_matches(200, expect_access=True)
        assert not access_matches(200, expect_access=False)


class TestWaitForRecordAccess:
    def test_returns_once_access_is_revoked(self):
        clock = _Clock()
        probe, calls = _probe([200, 200, 404])
        status = wait_for_record_access(
            USER, "rec-1", expect_access=False, description="the file",
            timeout=60, interval=5, probe=probe, sleep=clock.sleep, clock=clock,
        )
        assert status == 404
        assert calls == ["rec-1"] * 3

    def test_a_server_error_never_passes_for_a_denial(self):
        clock = _Clock()
        probe, _ = _probe([500])
        with pytest.raises(AssertionError, match="should be refused.*HTTP 500"):
            wait_for_record_access(
                USER, "rec-1", expect_access=False, description="the file",
                timeout=20, interval=5, probe=probe, sleep=clock.sleep, clock=clock,
            )

    def test_timeout_names_the_user_and_what_was_seen(self):
        clock = _Clock()
        probe, _ = _probe([404, 403])
        with pytest.raises(AssertionError) as excinfo:
            wait_for_record_access(
                USER, "rec-9", expect_access=True, description="the shared file",
                timeout=10, interval=5, probe=probe, sleep=clock.sleep, clock=clock,
            )
        message = str(excinfo.value)
        assert "reader@example.com should open the shared file (record rec-9)" in message
        assert "[403, 404]" in message

    @pytest.mark.parametrize("denial", [403, 404])
    def test_expected_access_that_ends_refused_raises_access_denied(self, denial):
        clock = _Clock()
        probe, _ = _probe([503, denial])
        with pytest.raises(AccessDenied, match=f"HTTP {denial}"):
            wait_for_record_access(
                USER, "rec-1", expect_access=True, description="the file",
                timeout=10, interval=5, probe=probe, sleep=clock.sleep, clock=clock,
            )

    def test_expected_access_that_ends_on_errors_is_not_a_denial(self):
        clock = _Clock()
        probe, _ = _probe([404, 500, 502])
        with pytest.raises(AssertionError) as excinfo:
            wait_for_record_access(
                USER, "rec-1", expect_access=True, description="the file",
                timeout=10, interval=5, probe=probe, sleep=clock.sleep, clock=clock,
            )
        assert not isinstance(excinfo.value, AccessDenied)
        assert "HTTP 502" in str(excinfo.value)

    def test_still_open_when_it_should_be_refused_is_not_a_denial(self):
        clock = _Clock()
        probe, _ = _probe([200])
        with pytest.raises(AssertionError) as excinfo:
            wait_for_record_access(
                USER, "rec-1", expect_access=False, description="the file",
                timeout=10, interval=5, probe=probe, sleep=clock.sleep, clock=clock,
            )
        assert not isinstance(excinfo.value, AccessDenied)
