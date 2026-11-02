"""Small helpers shared by the scenarios."""

import ipaddress
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import pytest

from app.agents.mcp import url_guard
from app.sources.client.http import address_guard
from app.utils import url_fetcher
from tests.integration.gitlab_simulated.glsim.model import Fault
from tests.integration.gitlab_simulated.glsim.server import HOST


def allow_the_simulator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the connector reach the simulator, which listens on loopback where connectors may not go.

    Only a URL whose host is the simulator's literal address passes, pinned to it. Every other URL
    gets the real check, so loopback stays refused in any other spelling (IPv4-mapped, 6to4,
    Teredo) and the guard still checks and pins each request. ``_ip_is_blocked`` is not patched:
    it calls itself for embedded addresses, and a patch would answer those calls too.
    """
    real = url_fetcher.resolve_public_http_target

    def resolve(url: str, **policy: bool) -> url_fetcher.PublicTarget:
        parts = urlsplit(url)
        if parts.hostname == HOST:
            return url_fetcher.PublicTarget(parts.scheme, HOST, parts.port or 80, (ipaddress.ip_address(HOST),))
        return real(url, **policy)

    # The two places the GitLab client's requests are checked: its requests session and its httpx downloads.
    monkeypatch.setattr(address_guard, "resolve_public_http_target", resolve)
    monkeypatch.setattr(url_guard, "resolve_public_http_target", resolve)


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
