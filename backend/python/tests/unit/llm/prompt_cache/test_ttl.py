from __future__ import annotations

import pytest

from app.llm.prompt_cache.ttl import TTLOrderingError, validate_ttl_ordering


class TestValidOrderings:
    def test_all_five_minute(self) -> None:
        validate_ttl_ordering(["5m", "5m", "5m"])

    def test_empty(self) -> None:
        validate_ttl_ordering([])

    def test_one_hour_before_five_minute(self) -> None:
        validate_ttl_ordering(["1h", "1h", "5m", "5m"])

    def test_all_one_hour(self) -> None:
        validate_ttl_ordering(["1h", "1h"])


class TestInvalidOrderings:
    def test_five_minute_before_one_hour_raises(self) -> None:
        with pytest.raises(TTLOrderingError):
            validate_ttl_ordering(["5m", "1h"])

    def test_one_hour_after_multiple_five_minute_raises(self) -> None:
        with pytest.raises(TTLOrderingError):
            validate_ttl_ordering(["5m", "5m", "1h"])
