"""Reading a child's CPU time from /proc: the parse, and the guard against another process."""

from __future__ import annotations

import os

import pytest

from app.connectors.sources.localKB.push import cpu


def stat_line(
    comm: str, utime: int = 120, stime: int = 30, starttime: int = 9_000
) -> str:
    # pid (comm) state ppid ... : utime and stime are fields 14-15, starttime field 22 (1-based, counting pid and comm)
    after_comm = [
        "S",
        "1",
        "1",
        "1",
        "0",
        "-1",
        "4194304",
        "100",
        "0",
        "0",
        "0",
        str(utime),
        str(stime),
        "0",
        "0",
        "20",
        "0",
        "1",
        "0",
        str(starttime),
        "1000",
        "1",
    ]
    return f"4242 ({comm}) " + " ".join(after_comm)


@pytest.mark.parametrize("comm", ["kb-push-worker", "a b) c 1", "x)(y", "((( )", ""])
def test_the_parse_counts_fields_from_the_last_paren(comm) -> None:
    assert cpu.parse_stat(stat_line(comm)) == (150, 9_000)


def test_a_naive_split_would_read_the_wrong_fields() -> None:
    """The control for the parse: this is what splitting the whole line gives for a name with a space."""
    line = stat_line("a b) c 1")
    naive = line.split()
    assert (int(naive[13]) + int(naive[14])) != 150


@pytest.mark.parametrize("text", ["", "no parens", "1 (x) S", "1 (x) S " + "a " * 30])
def test_a_malformed_stat_is_no_reading(text) -> None:
    assert cpu.parse_stat(text) is None


def test_a_missing_process_is_no_reading(tmp_path) -> None:
    assert cpu.read(999_999_999, proc_root=str(tmp_path)) is None
    assert cpu.read(None) is None


def test_a_real_process_reads_and_a_busy_loop_costs_cpu() -> None:
    if not os.path.exists("/proc/self/stat"):
        pytest.skip("not applicable on this platform: no /proc")
    a = cpu.read(os.getpid())
    assert a is not None
    t = 0
    for i in range(3_000_000):
        t += i
    b = cpu.read(os.getpid())
    assert cpu.seconds_between(a, b) is not None and cpu.seconds_between(a, b) >= 0


def test_a_different_start_time_is_not_the_same_process() -> None:
    """A recycled pid (or a child replaced mid-task) must give no measurement, never a wrong delta."""
    assert cpu.seconds_between((100, 1), (900, 2)) is None
    assert cpu.seconds_between((100, 7), (900, 7)) is not None
    assert cpu.seconds_between(None, (900, 7)) is None


def test_the_strike_ratio_is_half_the_wall_time() -> None:
    assert cpu.cpu_bound(25.0, 50.0) is True
    assert cpu.cpu_bound(24.9, 50.0) is False
    assert cpu.cpu_bound(None, 50.0) is False
    assert cpu.cpu_bound(10.0, 0.0) is False
