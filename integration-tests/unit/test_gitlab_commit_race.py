"""Unit tests for the GitLab commit retry on a lost branch race (no live services)."""

from __future__ import annotations

import asyncio

import pytest

from connectors.gitlab import gitlab_test_utils as gl
from connectors.gitlab.constants import GL_IT_PATH_ROOT, GL_IT_RUN_ID

pytestmark = pytest.mark.unit

# GitLab's body for a Gitaly reference-update conflict (Commits::CreateService).
RACE = gl.GitLabHTTPError(
    400,
    '{"message":"Could not update refs/heads/main. Please refresh and try again."}',
)
MISSING = gl.GitLabHTTPError(400, '{"message":"A file with this name doesn\'t exist"}')


def test_only_the_ref_update_conflict_counts_as_a_race() -> None:
    assert gl.lost_commit_race(RACE)
    assert not gl.lost_commit_race(MISSING)
    assert not gl.lost_commit_race(gl.GitLabHTTPError(409, "Please refresh and try again"))


class _Rest:
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.calls = 0

    async def post_json(self, path: str, body: object) -> dict:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return {"id": "abc"}


def _commit(rest: _Rest) -> dict:
    path = f"{GL_IT_PATH_ROOT}/{GL_IT_RUN_ID}/f.txt"
    return asyncio.run(gl.commit_actions(
        rest, "group/proj", "main", "msg",
        [{"action": "update", "file_path": path, "content": "x"}],
    ))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_: float) -> None:
        return None
    monkeypatch.setattr(gl.asyncio, "sleep", _sleep)


def test_a_lost_race_is_retried() -> None:
    rest = _Rest([RACE, RACE])
    assert _commit(rest) == {"id": "abc"}
    assert rest.calls == 3


def test_other_client_errors_are_not_retried() -> None:
    rest = _Rest([MISSING])
    with pytest.raises(gl.GitLabHTTPError):
        _commit(rest)
    assert rest.calls == 1


def test_a_race_lost_on_every_attempt_raises() -> None:
    rest = _Rest([RACE, RACE, RACE])
    with pytest.raises(gl.GitLabHTTPError):
        _commit(rest)
    assert rest.calls == 3
