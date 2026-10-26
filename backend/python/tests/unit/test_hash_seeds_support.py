"""`tests.support.hash_seeds` must not leave seed processes behind."""

from __future__ import annotations

import subprocess

import pytest

from tests.support import hash_seeds


def test_same_output_is_one_value() -> None:
    assert hash_seeds.outputs_across_hash_seeds("print('same')") == {"same"}


def test_hash_dependent_output_differs() -> None:
    assert len(hash_seeds.outputs_across_hash_seeds("print(hash('x'))")) == 3


def test_a_timeout_kills_every_seed_process(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def tracking_popen(*args, **kwargs):  # noqa: ANN202
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(hash_seeds.subprocess, "Popen", tracking_popen)

    with pytest.raises(subprocess.TimeoutExpired):
        hash_seeds.outputs_across_hash_seeds("import time; time.sleep(60)", timeout=0.5)

    assert len(started) == 3
    assert all(proc.poll() is not None for proc in started)


def test_a_failing_seed_still_reaps_the_others() -> None:
    with pytest.raises(AssertionError, match="boom"):
        hash_seeds.outputs_across_hash_seeds("raise SystemExit('boom')")
