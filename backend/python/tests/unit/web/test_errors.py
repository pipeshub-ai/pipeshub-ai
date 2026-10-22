import pytest

from app.web.deadline import Deadline
from app.web.errors import (
    AuthFailed,
    QuotaExceeded,
    RateLimited,
    UpstreamError,
    WebError,
    error_for_status,
)


class TestErrorForStatus:
    @pytest.mark.parametrize("status", [200, 204, 301, 399])
    def test_success_is_none(self, status: int) -> None:
        assert error_for_status(status, {}, source="S") is None

    @pytest.mark.parametrize(
        ("status", "error", "retryable"),
        [
            (401, AuthFailed, False),
            (403, AuthFailed, False),
            (402, QuotaExceeded, False),
            (429, RateLimited, True),
            (400, UpstreamError, False),
            (404, UpstreamError, False),
            (500, UpstreamError, True),
            (503, UpstreamError, True),
        ],
    )
    def test_mapping(self, status: int, error: type[WebError], retryable: bool) -> None:
        result = error_for_status(status, {}, source="Serper")
        assert type(result) is error
        assert result.retryable is retryable
        assert result.status == status
        assert result.source == "Serper"

    def test_retry_after_seconds_any_header_case(self) -> None:
        assert error_for_status(429, {"Retry-After": "3"}, source="S").retry_after_s == 3.0
        assert error_for_status(429, {"retry-after": "4"}, source="S").retry_after_s == 4.0

    def test_retry_after_http_date_in_the_past_is_zero(self) -> None:
        error = error_for_status(429, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, source="S")
        assert error.retry_after_s == 0.0

    def test_unparseable_retry_after_is_none(self) -> None:
        assert error_for_status(429, {"Retry-After": "soon"}, source="S").retry_after_s is None


def test_tool_payload_shape() -> None:
    assert RateLimited("slow down").to_tool_payload() == {
        "ok": False,
        "error_type": "rate_limited",
        "error": "slow down",
        "retryable": True,
    }


class TestDeadline:
    def test_remaining_and_cap(self) -> None:
        now = [100.0]
        deadline = Deadline.after(8, clock=lambda: now[0])
        assert deadline.remaining() == 8
        assert deadline.cap(2) == 2
        now[0] = 105.0
        assert deadline.cap(10) == 3
        assert not deadline.expired
        now[0] = 200.0
        assert deadline.remaining() == 0
        assert deadline.cap(1) == 0
        assert deadline.expired
