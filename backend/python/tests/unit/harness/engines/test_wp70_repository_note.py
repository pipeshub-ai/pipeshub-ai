"""WP70-CLONE: an engine whose run may clone public repositories is told so, and how.

A manual deep-research run had github.com's public preset on; the engine's instructions never named ``ph git clone``, so it read
GitHub one page at a time and through the anonymous API.
"""

from __future__ import annotations

from typing import Any

from app.harness.engines.durable import run_repository_hosts, told_repository_hosts
from app.harness.repositories import repository_instructions
from harness_core.logs import HarnessLog

LOG = HarnessLog.named("tests.wp70")


def test_wp70_clone_the_note_names_the_hosts_and_the_command() -> None:
    note = repository_instructions([{"host": "github.com", "preset": "github-public"}, {"host": "gitlab.com"}])
    assert "may clone public repositories from github.com, gitlab.com" in note
    assert "run `ph git clone https://github.com/<owner>/<repo>`; PipesHub fetches it after the person allows it" in note
    assert "raw.githubusercontent.com" in note and "refuses anonymous requests" in note
    gitlab = repository_instructions([{"host": "gitlab.com"}])
    assert "`ph git clone https://gitlab.com/<owner>/<repo>`" in gitlab and "raw.githubusercontent.com" not in gitlab


def test_wp70_clone_no_allowed_host_no_note() -> None:
    assert repository_instructions([]) == ""
    assert repository_instructions([{"preset": "x"}]) == ""


async def test_wp70_clone_the_hosts_are_read_once_and_a_failed_read_tells_nothing() -> None:
    class Policy:
        async def allowed_repository_hosts(self, *, web: bool = False) -> list[dict[str, Any]]:
            return [{"host": "github.com", "web": web}]

    class Broken:
        async def allowed_repository_hosts(self, *, web: bool = False) -> list[dict[str, Any]]:
            raise RuntimeError("node down")

    assert await told_repository_hosts(run_repository_hosts(Policy(), web=True), LOG) == [{"host": "github.com", "web": True}]
    assert await told_repository_hosts(run_repository_hosts(Policy(), web=False), LOG) == [{"host": "github.com", "web": False}]
    assert await told_repository_hosts(run_repository_hosts(Broken(), web=True), LOG) == []
    assert run_repository_hosts(object(), web=True) is None
    assert await told_repository_hosts(None, LOG) == []
