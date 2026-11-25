"""WP-81: a worker without git refuses a clone in plain words and tells the operator why (WP81-GIT-2).

In a research run both approved clones were refused "Repository fetch is not available on this
deployment" and the worker logged only ``repository.refused reasonCode=repository_unavailable``: nothing said that
the stack's app image had no git (``deployment/e2e/Dockerfile.app``, fixed with this test's cohesion twin).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.repos.fetch import FetchConfig, RepositoryRefused
from tests.unit.harness.test_gitf_repository_fetch import (
    LOCATION,
    _LoopbackFetcher,
    _Resolver,
)

if TYPE_CHECKING:
    from pathlib import Path


async def test_wp81_git_2_a_missing_git_is_refused_for_the_person_and_named_for_the_operator(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "no-such-git"
    fetcher = _LoopbackFetcher(FetchConfig(cache_dir=tmp_path / "cache", git_bin=str(missing)), _Resolver({}))
    with caplog.at_level(logging.ERROR), pytest.raises(RepositoryRefused) as refused:
        _ = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    assert refused.value.reason == "repository_unavailable"
    [line] = [r for r in caplog.records if getattr(r, "harness_event", "") == "repository.git_unavailable"]
    assert line.harness["gitBin"] == str(missing)  # type: ignore[attr-defined]
    assert line.harness["errorType"] == "FileNotFoundError" and line.harness["errno"] == 2  # type: ignore[attr-defined]
