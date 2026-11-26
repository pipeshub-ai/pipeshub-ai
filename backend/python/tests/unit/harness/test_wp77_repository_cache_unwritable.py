"""WP77-GITF-1..2: a repository cache the worker cannot write.

Every approved ``repository_fetch`` came back to the person as ``PermissionError: [Errno 13] Permission denied:
'/data/pipeshub/harness'``: the worker's exception text, printed by ``ph git clone`` into the run's activity. The
fetch now refuses in plain words that say who can fix it, and the worker's log names the folder, the errno and the
uid, which is what an operator needs and the person does not.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from typing import TYPE_CHECKING

import pytest

from app.harness.repositories import RepositoryFetchTools
from harness_core.effects.types import ToolContext
from harness_core.kernel.core.messages import ToolCall
from harness_core.repos.fetch import FetchConfig, RepositoryFetcher

if TYPE_CHECKING:
    from pathlib import Path

HOST = "github.com"
CTX = ToolContext(run_id="run-1", org_id="org-1", invocation_id="i1", attempt_id="a1", idempotency_key="k1")

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root writes through any mode bits")


class _Resolver:
    async def resolve(self, host: str) -> tuple[str, ...]:
        raise AssertionError("an unwritable cache is refused before any address is looked up")


class _Shelf:
    async def hand_off(self, archive: object, size_bytes: int) -> str | None:
        raise AssertionError("nothing is handed over")


def _tools(cache_dir: Path) -> RepositoryFetchTools:
    async def hosts() -> list[dict[str, object]]:
        return [{"host": HOST}]

    return RepositoryFetchTools(
        fetcher=RepositoryFetcher(FetchConfig(cache_dir=cache_dir), _Resolver()),  # type: ignore[arg-type]
        hosts=hosts,  # type: ignore[arg-type]
        shelf=_Shelf(),  # type: ignore[arg-type]
    )


def _call() -> ToolCall:
    return ToolCall(id="c1", name="repository_fetch", arguments={"url": "https://github.com/onyx-dot-app/onyx"})


@pytest.fixture
def locked(tmp_path: Path) -> Path:
    """``/data/pipeshub`` as a recorded stack had it: a root-owned 755 folder the worker's uid cannot create in."""
    data = tmp_path / "data-pipeshub"
    data.mkdir()
    data.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        yield data
    finally:
        data.chmod(stat.S_IRWXU)


async def test_wp77_gitf_1_an_unwritable_cache_is_a_plain_refusal_not_an_exception(
    locked: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    done = await _tools(locked / "harness" / "repository-cache").execute(_call(), CTX)
    assert done.status == "error"
    text = str(done.content)
    for leaked in ("PermissionError", "Errno", str(locked)):
        assert leaked not in text, text
    assert "administrator" in text and "repository" in text.lower(), text
    refused = [r for r in caplog.records if "repository.cache_unwritable" in r.getMessage()]
    assert refused, [r.getMessage() for r in caplog.records]
    line = refused[0].getMessage()
    assert f"errno={errno.errorcode[errno.EACCES]}" in line, line
    assert f"path={locked / 'harness'}" in line, line
    assert f"uid={os.geteuid()}" in line, line
    assert refused[0].levelno == logging.ERROR


async def test_wp77_gitf_2_the_refusal_is_not_cached_so_a_fixed_folder_works_without_a_restart(locked: Path) -> None:
    tools = _tools(locked / "harness" / "repository-cache")
    assert (await tools.execute(_call(), CTX)).status == "error"
    locked.chmod(stat.S_IRWXU)
    tools.fetcher.cache.prepare()
    assert (locked / "harness" / "repository-cache").is_dir()
