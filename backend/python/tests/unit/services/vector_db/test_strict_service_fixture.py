"""A backend selected for an integration run fails when it is down; it is not skipped."""

import pytest

from tests.integration.vector_db.helpers import strict_service


class _Request:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
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


def test_other_errors_are_not_hidden() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        strict_service(_Request(RuntimeError("boom")), "redis")
