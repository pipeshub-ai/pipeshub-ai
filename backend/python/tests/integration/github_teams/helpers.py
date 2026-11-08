"""Small helpers shared by the scenarios."""

from collections.abc import Callable
from typing import Any

from tests.integration.github_teams.sim.model import Fault


def on(fragment: str, method: str = "GET", **query: str) -> Callable[[str, str, dict[str, str]], bool]:
    """Match requests whose host+path contains ``fragment`` and whose query has these values."""
    def match(m: str, where: str, q: dict[str, str]) -> bool:
        return m == method and fragment in where and all(q.get(k) == str(v) for k, v in query.items())
    return match


def fail(world: Any, fragment: str, status: int = 500, times: int | None = None, **query: str) -> Fault:  # noqa: ANN401
    fault = Fault(on(fragment, **query), kind="status", status=status, times=times)
    world.gh.faults.append(fault)
    return fault


def hook(world: Any, fragment: str, action: Callable[[], None], **query: str) -> Fault:  # noqa: ANN401
    fault = Fault(on(fragment, **query), kind="hook", action=action, times=1)
    world.gh.faults.append(fault)
    return fault


def issues_listing(repo: Any) -> str:  # noqa: ANN401
    return f"api.github.com/repos/{repo.full_name}/issues"


def nth(fragment: str, n: int, method: str = "GET") -> Callable[[str, str, dict[str, str]], bool]:
    """Match the n-th request (1-based, counted from now) whose host+path ends with
    ``fragment``, whatever its query: tests must not depend on how the connector pages."""
    seen = {"count": 0}

    def match(m: str, where: str, q: dict[str, str]) -> bool:
        if m != method or not where.endswith(fragment):
            return False
        seen["count"] += 1
        return seen["count"] == n
    return match


def hook_nth(world: Any, fragment: str, n: int, action: Callable[[], None]) -> Fault:  # noqa: ANN401
    fault = Fault(nth(fragment, n), kind="hook", action=action, times=1)
    world.gh.faults.append(fault)
    return fault


def fail_from_nth(world: Any, fragment: str, n: int, status: int = 500, method: str = "GET") -> Fault:  # noqa: ANN401
    """Fail the n-th matching request and every one after it."""
    seen = {"count": 0}

    def match(m: str, where: str, q: dict[str, str]) -> bool:
        if m != method or not where.endswith(fragment):
            return False
        seen["count"] += 1
        return seen["count"] >= n
    fault = Fault(match, kind="status", status=status, times=None)
    world.gh.faults.append(fault)
    return fault
