"""A backend selected for an integration run fails when it is down; it is not skipped."""

import pytest

from tests.integration.vector_db.helpers import strict_service


class _Config:
    def __init__(self, markexpr: str) -> None:
        self.markexpr = markexpr

    def getoption(self, name: str, default: str = "") -> str:
        assert name == "markexpr"
        return self.markexpr


class _Request:
    def __init__(self, outcome, markexpr: str = "integration") -> None:
        self.outcome = outcome
        self.config = _Config(markexpr)
        self.asked: list[str] = []

    def getfixturevalue(self, name: str) -> object:
        self.asked.append(name)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def test_a_reachable_service_is_returned() -> None:
    request = _Request("service")
    assert strict_service(request, "qdrant") == "service"
    assert request.asked == ["qdrant_service"]


def test_an_unreachable_service_fails_the_test_instead_of_skipping_it() -> None:
    with pytest.raises(pytest.fail.Exception, match="qdrant service was selected but is unavailable"):
        strict_service(_Request(pytest.skip.Exception("Qdrant not available at localhost:6334")), "qdrant")


@pytest.mark.parametrize("markexpr", ["", "not integration"])
def test_a_run_that_does_not_select_integration_tests_still_skips(markexpr) -> None:
    with pytest.raises(pytest.skip.Exception):
        strict_service(_Request(pytest.skip.Exception("down"), markexpr), "qdrant")


def test_other_errors_are_not_hidden() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        strict_service(_Request(RuntimeError("boom")), "redis")
