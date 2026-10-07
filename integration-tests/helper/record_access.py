"""Ask the product whether a logged-in person can open one record.

Connector suites prove permissions by counting edges in the graph, which says
what was written but not what anyone can reach. The record endpoint runs the
same access check search and chat rely on (every direct, group, team, org and
inherited path), so asking it as a specific user is the end-to-end answer.
"""

from __future__ import annotations

import time
from typing import Callable

import requests

from helper.second_user import NO_ACCESS_STATUSES, SecondUser

# Graph writes land a few seconds after a sync reports done, and a revoke lands
# on the next sync, so a single read races both.
ACCESS_POLL_TIMEOUT_SEC = 180.0
ACCESS_POLL_INTERVAL_SEC = 5.0


class AccessDenied(AssertionError):
    """The person was expected to open the record, and the product refused.

    Raised only when the poll ends on a real denial, so a test can expect this
    particular failure without also accepting server errors or expired logins.
    """


def record_access_status(user: SecondUser, record_id: str) -> int:
    """HTTP status of opening ``record_id`` as ``user``: 200 when they can."""
    resp = requests.get(
        f"{user.base_url}/api/v1/knowledgeBase/record/{record_id}",
        headers=user.headers,
        timeout=user.timeout,
    )
    return resp.status_code


def access_matches(status: int, expect_access: bool) -> bool:
    """Whether ``status`` settles the question.

    Anything other than 200 or a denial (a 5xx, a 401 from an expired token)
    proves neither answer, so it never counts as "no access".
    """
    if expect_access:
        return status == 200
    return status in NO_ACCESS_STATUSES


def wait_for_record_access(
    user: SecondUser,
    record_id: str,
    *,
    expect_access: bool,
    description: str,
    timeout: float = ACCESS_POLL_TIMEOUT_SEC,
    interval: float = ACCESS_POLL_INTERVAL_SEC,
    probe: Callable[[SecondUser, str], int] = record_access_status,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Poll until ``user`` can (or cannot) open ``record_id``; return that status."""
    deadline = clock() + timeout
    statuses: list[int] = []
    while True:
        status = probe(user, record_id)
        statuses.append(status)
        if access_matches(status, expect_access):
            return status
        if clock() >= deadline:
            wanted = "open" if expect_access else "be refused"
            message = (
                f"{user.email} should {wanted} {description} (record {record_id}), "
                f"but got HTTP {statuses[-1]} for {timeout:.0f}s "
                f"(statuses seen: {sorted(set(statuses))})"
            )
            # The last answer decides: a brief 5xx before the denials still settles
            # on "refused", while a poll that ends on an error has settled nothing.
            if expect_access and statuses[-1] in NO_ACCESS_STATUSES:
                raise AccessDenied(message)
            raise AssertionError(message)
        sleep(interval)
