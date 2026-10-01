"""Tests for app.utils.build_info."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import build_info
from app.utils.build_info import get_build_info, resolve_build_info

COMMIT = "84614464bb120e9f8e8c85420001260e709d99bb"


@pytest.fixture
def no_build_env(monkeypatch):
    for key in ("APP_VERSION", "GIT_COMMIT", "BUILD_TIME"):
        monkeypatch.delenv(key, raising=False)


def _git_answers(answers: dict[str, str]):
    def run(cmd, **_kwargs):
        out = answers.get(cmd[1])
        return SimpleNamespace(returncode=0 if out else 128, stdout=out or "")

    return run


class TestEnvironment:
    def test_values_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "0.9.1")
        monkeypatch.setenv("GIT_COMMIT", COMMIT)
        monkeypatch.setenv("BUILD_TIME", "2026-09-30T10:12:00Z")

        with patch.object(build_info.subprocess, "run") as run:
            info = resolve_build_info()

        assert info == {
            "version": "0.9.1",
            "commitId": COMMIT,
            "buildTime": "2026-09-30T10:12:00Z",
        }
        run.assert_not_called()

    def test_empty_variables_give_none_and_never_call_git(
        self, monkeypatch, no_build_env
    ):
        # What a plain `docker build .` produces: defined, but empty.
        monkeypatch.setenv("APP_VERSION", "")
        monkeypatch.setenv("GIT_COMMIT", "")
        monkeypatch.setenv("BUILD_TIME", "")

        with patch.object(build_info.subprocess, "run") as run:
            info = resolve_build_info()

        assert info == {"version": None, "commitId": None, "buildTime": None}
        run.assert_not_called()


class TestGitFallback:
    def test_reads_tag_and_commit_from_git(self, no_build_env):
        answers = {"describe": "v0.9.1\n", "rev-parse": f"{COMMIT}\n"}
        with patch.object(build_info.subprocess, "run", _git_answers(answers)):
            info = resolve_build_info()

        assert info == {"version": "0.9.1", "commitId": COMMIT, "buildTime": None}

    def test_checkout_without_tags_still_reports_the_commit(self, no_build_env):
        with patch.object(
            build_info.subprocess, "run", _git_answers({"rev-parse": COMMIT})
        ):
            info = resolve_build_info()

        assert info == {"version": None, "commitId": COMMIT, "buildTime": None}

    @pytest.mark.parametrize(
        "error",
        [
            FileNotFoundError("git"),
            subprocess.TimeoutExpired(cmd="git", timeout=2),
        ],
    )
    def test_git_failure_gives_none(self, no_build_env, error):
        with patch.object(build_info.subprocess, "run", side_effect=error):
            info = resolve_build_info()

        assert info == {"version": None, "commitId": None, "buildTime": None}


def test_get_build_info_returns_the_value_resolved_at_import():
    with patch.object(build_info.subprocess, "run") as run:
        assert get_build_info() is get_build_info()
    run.assert_not_called()
    assert set(get_build_info()) == {"version", "commitId", "buildTime"}
