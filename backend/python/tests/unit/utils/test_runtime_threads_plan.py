"""``runtime_threads`` drops blank capacity knobs, applies the plan's thread env, and keeps operator values."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[3]
THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def _run(env: dict[str, str], body: str, tmp_path: Path | None = None) -> str:
    base = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PYTHON_ROOT)}
    if tmp_path is not None:
        base["PYTHONPATH"] = f"{tmp_path}:{PYTHON_ROOT}"
    out = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", textwrap.dedent(body)],
        cwd=PYTHON_ROOT, capture_output=True, text=True, check=True, env={**base, **env},
    )
    return out.stdout.strip()


SHOW = "import os; print(*[os.environ.get(k, '-') for k in %r])"


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, ["2", "2", "2"]),
        ({"PIPESHUB_OMP_DEFAULT": "3"}, ["3", "3", "3"]),
        ({"PIPESHUB_OMP_DEFAULT": "junk"}, ["2", "2", "2"]),
        ({"OMP_NUM_THREADS": ""}, ["2", "2", "2"]),
        ({"OMP_NUM_THREADS": "  ", "MKL_NUM_THREADS": "", "OPENBLAS_NUM_THREADS": ""}, ["2", "2", "2"]),
        ({"OMP_NUM_THREADS": "", "PIPESHUB_OMP_DEFAULT": "4"}, ["4", "4", "4"]),
        ({"OMP_NUM_THREADS": "8"}, ["8", "8", "8"]),
        ({"OMP_NUM_THREADS": "8", "MKL_NUM_THREADS": "1"}, ["8", "8", "1"]),
        ({"OMP_NUM_THREADS": "8", "PIPESHUB_OMP_DEFAULT": "2"}, ["8", "8", "8"]),
    ],
    ids=["none", "image_default", "bad_image_default", "blank", "all_blank", "blank_with_default",
         "operator", "operator_mkl_own", "operator_beats_default"],
)
def test_thread_env(env: dict[str, str], expected: list[str]) -> None:
    got = _run(env, "import app.utils.runtime_threads\n" + SHOW % (THREADS,))
    assert got.split() == expected


def test_blank_knobs_are_removed_and_others_kept() -> None:
    env = {"MAX_CONCURRENT_PARSING": "", "GOVERNOR_INDEX_MAX": "  ", "MAX_CONCURRENT_INDEXING": "7", "UNRELATED_BLANK": ""}
    names = ("MAX_CONCURRENT_PARSING", "GOVERNOR_INDEX_MAX", "MAX_CONCURRENT_INDEXING", "UNRELATED_BLANK")
    got = _run(env, "import app.utils.runtime_threads\n" + SHOW % (names,))
    assert got.split() == ["-", "-", "7"], got  # the blank unrelated one prints as an empty word
    # an unrelated variable is left alone, even when blank
    assert _run(env, "import app.utils.runtime_threads, os; print('UNRELATED_BLANK' in os.environ)") == "True"


def test_a_blank_knob_no_longer_crashes_an_int_read() -> None:
    """The gateway read ``int(os.getenv(..., "24"))`` at import; a blank value crashed it."""
    got = _run(
        {"MAX_CONCURRENT_INDEXING_LLM_CALLS": ""},
        "import app.utils.runtime_threads\nfrom app.utils.concurrency import MAX_CONCURRENT_INDEXING_LLM_CALLS as n\nprint(n)",
    )
    assert got == "24"


def test_the_plan_is_applied_before_numpy_is_imported(tmp_path: Path) -> None:
    (tmp_path / "numpy.py").write_text("import os\nSEEN = os.environ.get('OMP_NUM_THREADS')\n")
    got = _run(
        {"OMP_NUM_THREADS": ""},
        "import app.utils.runtime_threads\nimport numpy\nprint(numpy.SEEN)",
        tmp_path,
    )
    assert got == "2"


def test_a_failing_plan_falls_back_to_the_old_thread_env(tmp_path: Path) -> None:
    got = _run(
        {"PIPESHUB_CAPACITY_FACTS": "{}", "OMP_NUM_THREADS": "5"},
        """
        import app.capacity.policy as p
        def boom(*a, **k): raise RuntimeError("no plan")
        p.plan_capacity = boom
        import app.capacity.apply as a
        a.plan_capacity = boom
        import app.utils.runtime_threads, os
        print(os.environ["OMP_NUM_THREADS"], os.environ["MKL_NUM_THREADS"], os.environ["TOKENIZERS_PARALLELISM"])
        """,
    )
    assert got == "5 5 false"


def test_a_service_announces_its_plan_once_on_stderr() -> None:
    out = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", "import app.utils.runtime_threads"],
        cwd=PYTHON_ROOT, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PYTHON_ROOT), "PIPESHUB_SERVICE": "embedding"},
    )
    lines = [ln for ln in out.stderr.splitlines() if ln.startswith("capacity:")]
    assert len(lines) == 1 and "service=embedding omp=2" in lines[0], out.stderr


def test_a_spawned_child_does_not_announce_the_plan_again(tmp_path) -> None:
    """A child spawned by the service imports its main module while ``parent_process()`` is still ``None``;
    its process name is what tells it from the service's own process."""
    code = (
        "import multiprocessing as mp\n"
        "if __name__ == '__main__':\n"
        "    mp.set_start_method('spawn')\n"
        "    import app.utils.runtime_threads\n"
        "    p = mp.Process(target=print, args=('child ran',)); p.start(); p.join()\n"
        "else:\n"
        "    import app.utils.runtime_threads\n"
    )
    script = tmp_path / "service_main.py"
    script.write_text(code)
    out = subprocess.run(
        [sys.executable, "-W", "ignore", str(script)],
        cwd=PYTHON_ROOT, capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PYTHON_ROOT), "PIPESHUB_SERVICE": "embedding"},
    )
    assert "child ran" in out.stdout, out
    assert len([ln for ln in out.stderr.splitlines() if ln.startswith("capacity:")]) == 1, out.stderr
