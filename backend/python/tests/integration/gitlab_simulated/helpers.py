"""Small helpers shared by the scenarios."""

from collections.abc import Callable
from typing import Any

from tests.integration.gitlab_simulated.glsim.model import Fault


def on(fragment: str, method: str = "GET", **query: str) -> Callable[[str, str, dict[str, str]], bool]:
    """Match requests whose path contains ``fragment`` and whose query has these values."""
    def match(m: str, where: str, q: dict[str, str]) -> bool:
        return m == method and fragment in where and all(q.get(k) == str(v) for k, v in query.items())
    return match


def ends(fragment: str, method: str = "GET") -> Callable[[str, str, dict[str, str]], bool]:
    def match(m: str, where: str, q: dict[str, str]) -> bool:
        return m == method and where.endswith(fragment)
    return match


def fail(world: Any, matcher: Callable[[str, str, dict[str, str]], bool], status: Any = 500, times: int = 1,  # noqa: ANN401
         skip: int = 0, **headers: str) -> Fault:
    fault = Fault(matcher, kind=status, times=times, skip=skip, headers=headers)
    world.gl.faults.append(fault)
    return fault


def hook(world: Any, matcher: Callable[[str, str, dict[str, str]], bool], action: Callable[[], None],  # noqa: ANN401
         skip: int = 0) -> Fault:
    fault = Fault(matcher, kind="hook", times=1, skip=skip, action=action)
    world.gl.faults.append(fault)
    return fault


def pid(project: Any) -> str:  # noqa: ANN401
    return f"/api/v4/projects/{project.id}"
