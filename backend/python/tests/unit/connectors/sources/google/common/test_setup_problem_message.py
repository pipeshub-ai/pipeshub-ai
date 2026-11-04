"""Every Google setup problem has words an admin can act on; the others have none."""

import pytest

from app.connectors.sources.google.common.access_problems import (
    AccessProblem,
    setup_problem_message,
)

_APIS = "Gmail API and the Admin SDK API"


_CASES = [
    (AccessProblem.DELEGATION, "Domain-wide delegation, and authorize the service account's client ID (1234)"),
    (AccessProblem.SERVICE_ACCOUNT_KEY, "Create a new key for the service account"),
    (AccessProblem.UNREADABLE_KEY, "not a valid JSON key file"),
    (AccessProblem.ADMIN_ACCOUNT, "(admin@acme.example) must be an active Google Workspace administrator"),
    (AccessProblem.API_DISABLED, f"The {_APIS} must be turned on"),
    (AccessProblem.MISSING_SCOPE, None),
    (AccessProblem.SIGNED_OUT, None),
    (AccessProblem.USER_UNAVAILABLE, None),
    (None, None),
]


@pytest.mark.parametrize(("problem", "expected"), _CASES)
def test_setup_problem_message(problem: AccessProblem | None, expected: str | None) -> None:
    message = setup_problem_message(problem, apis=_APIS, admin_email="admin@acme.example", client_id="1234")

    if expected is None:
        assert message is None
    else:
        assert expected in message


def test_every_problem_is_covered() -> None:
    assert {problem for problem, _ in _CASES if problem} == set(AccessProblem)
