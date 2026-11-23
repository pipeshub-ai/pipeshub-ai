"""grep stages run through ripgrep, and the ripgrep thread count (GREP_THREADS)."""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.agents.actions.storage_search import storage_search as ss
from app.agents.actions.storage_search.storage_search import (
    _grep_stage_as_rg,
    _posix_to_rust_regex,
    _run_subprocess,
    _with_ripgrep,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

RG = "/usr/bin/rg"
HEAD = [RG, "--no-config", "--no-ignore", "--hidden", "-j2"]


@pytest.fixture
def rg_installed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(ss, "_rg_binary", lambda: RG)
    monkeypatch.setattr(ss, "_grep_threads", lambda: 2)
    yield


@pytest.fixture
def cpus(monkeypatch: pytest.MonkeyPatch) -> Iterator[callable]:
    monkeypatch.delenv("GREP_THREADS", raising=False)

    def set_cpus(n: int) -> None:
        monkeypatch.setattr(ss, "_available_cpus", lambda: n)
    yield set_cpus


class TestGrepThreads:
    def test_unset_is_half_the_cpus(self, cpus) -> None:
        cpus(8)
        assert ss._grep_threads() == 4

    @pytest.mark.parametrize(("value", "expected"), [("1", 1), ("3", 3), ("4", 4), ("16", 4)])
    def test_set_is_capped_at_half_the_cpus(self, cpus, monkeypatch, value, expected) -> None:
        cpus(8)
        monkeypatch.setenv("GREP_THREADS", value)
        assert ss._grep_threads() == expected

    @pytest.mark.parametrize("value", ["", "0", "-2", "many"])
    def test_a_blank_or_bad_value_uses_the_default(self, cpus, monkeypatch, value) -> None:
        cpus(6)
        monkeypatch.setenv("GREP_THREADS", value)
        assert ss._grep_threads() == 3

    @pytest.mark.parametrize("n", [1, 0])
    def test_never_below_one(self, cpus, monkeypatch, n) -> None:
        cpus(n)
        assert ss._grep_threads() == 1
        monkeypatch.setenv("GREP_THREADS", "4")
        assert ss._grep_threads() == 1


class TestAvailableCpus:
    @pytest.fixture(autouse=True)
    def _fresh(self) -> Iterator[None]:
        ss._available_cpus.cache_clear()
        yield
        ss._available_cpus.cache_clear()

    @pytest.mark.parametrize(("cpu_max", "expected"), [
        ("200000 100000\n", 2),
        ("150000 100000\n", 2),  # 1.5 CPUs rounds up
        ("50000 100000\n", 1),
        ("400000 100000\n", 4),
    ])
    def test_the_cgroup_quota_wins(self, tmp_path: Path, monkeypatch, cpu_max, expected) -> None:
        f = tmp_path / "cpu.max"
        f.write_text(cpu_max)
        monkeypatch.setattr(ss, "_CGROUP_CPU_MAX", str(f))
        assert ss._available_cpus() == expected

    @pytest.mark.parametrize("cpu_max", ["max 100000\n", None, "garbage"])
    def test_no_quota_uses_the_cpus_the_process_may_run_on(self, tmp_path: Path, monkeypatch, cpu_max) -> None:
        f = tmp_path / "cpu.max"
        if cpu_max is not None:
            f.write_text(cpu_max)
        monkeypatch.setattr(ss, "_CGROUP_CPU_MAX", str(f))
        expected = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
        assert ss._available_cpus() == expected


class TestRgBinary:
    @pytest.fixture(autouse=True)
    def _fresh(self) -> Iterator[None]:
        ss._rg_binary.cache_clear()
        yield
        ss._rg_binary.cache_clear()

    @pytest.mark.parametrize(("found", "expected"), [
        ("/usr/bin/rg", "/usr/bin/rg"),
        ("/home/app/.cargo/bin/rg", None),  # Landlock would refuse to execute it
        (None, None),
    ])
    def test_only_a_system_ripgrep_is_used(self, found, expected) -> None:
        with patch.object(ss.shutil, "which", return_value=found):
            assert ss._rg_binary() == expected


class TestGrepStageAsRg:
    @pytest.mark.usefixtures("rg_installed")
    @pytest.mark.parametrize(("tokens", "expected"), [
        (["grep", "-ril", "foo", "."], [*HEAD, "-i", "-l", "-e", "foo", "--", "."]),
        (["grep", "-rc", "foo", "."], [*HEAD, "-c", "--include-zero", "-e", "foo", "--", "."]),
        (["grep", "-rlc", "foo", "."], [*HEAD, "-l", "-e", "foo", "--", "."]),
        (["grep", "-rlZ", "-e", "a", "-e", "b", "."], [*HEAD, "-l", "-0", "-e", "a", "-e", "b", "--", "."]),
        (["grep", "-rxw", "foo", "."], [*HEAD, "-x", "-e", "foo", "--", "."]),
        (["grep", "-rE", "ab{,3}c", "."], [*HEAD, "-e", "ab{0,3}c", "--", "."]),
        (["grep", "-rE", r"\(draft)", "."], [*HEAD, "-e", r"\(draft\)", "--", "."]),
        (["fgrep", "-r", "a.b(", "."], [*HEAD, "-F", "-e", "a.b(", "--", "."]),
        (["grep", "-rF", "a.b(", "."], [*HEAD, "-F", "-e", "a.b(", "--", "."]),
        (["grep", "-rE", "x{2}|y+", "."], [*HEAD, "-e", "x{2}|y+", "--", "."]),
        (["grep", "-r", r"\(a\|b\)c", "."], [*HEAD, "-e", "(a|b)c", "--", "."]),
        (["grep", "-rw", "word", "--", "d"], [*HEAD, "-w", "-e", "word", "--", "d"]),
    ])
    def test_translates_what_ripgrep_reproduces(self, tokens, expected) -> None:
        assert _grep_stage_as_rg(tokens, via_xargs=False) == expected

    @pytest.mark.usefixtures("rg_installed")
    @pytest.mark.parametrize("tokens", [
        ["grep", "-rn", "foo", "."],                 # -n: no exact equivalent in the table
        ["grep", "-r", "--include=*.json", "foo", "."],
        ["grep", "-r", "-A", "2", "foo", "."],
        ["grep", "-r", r"\(a\)\1", "."],             # backreference
        ["grep", "-r", r"[\w]", "."],                # backslash inside brackets
        ["grep", "foo", "file.json"],                # not recursive: grep would not search a dir
        ["grep", "-ril", "foo"],                     # no path: grep searches ".", ripgrep would read stdin
        ["grep", "-rl", r"\<word\>", "."],           # word start/end anchors
        ["grep", "-rE", "caf[[:alpha:]]", "."],      # POSIX classes are ASCII-only in ripgrep
        ["cat", "x"],
    ])
    def test_keeps_gnu_grep_for_the_rest(self, tokens) -> None:
        assert _grep_stage_as_rg(tokens, via_xargs=False) is None

    @pytest.mark.usefixtures("rg_installed")
    def test_after_xargs_only_the_non_recursive_form(self) -> None:
        # grep skips a directory xargs hands it; --max-depth 0 keeps ripgrep from walking it.
        assert _grep_stage_as_rg(["grep", "-l", "x"], via_xargs=True) == [*HEAD, "--max-depth", "0", "-l", "-e", "x", "--"]
        assert _grep_stage_as_rg(["grep", "-rl", "x"], via_xargs=True) is None
        assert _grep_stage_as_rg(["grep", "-lI", "x"], via_xargs=True) is None  # ripgrep reads named binaries

    def test_without_ripgrep_nothing_changes(self, monkeypatch) -> None:
        monkeypatch.setattr(ss, "_rg_binary", lambda: None)
        assert _grep_stage_as_rg(["grep", "-rl", "x", "."], via_xargs=False) is None
        pipeline = [["grep", "-rl", "x", "."], ["xargs", "grep", "-l", "y"]]
        assert _with_ripgrep(pipeline) == pipeline


class TestPosixToRust:
    @pytest.mark.parametrize(("pattern", "extended", "expected"), [
        ("a+b", False, r"a\+b"),
        (r"a\+b", False, "a+b"),
        ("a{2}", False, r"a\{2\}"),
        (r"a\{2,3\}", False, "a{2,3}"),
        ("a{,3}", True, "a{0,3}"),
        (r"\(a\)", False, "(a)"),
        ("(a))", True, r"(a)\)"),
        ("^a|b$", True, "^a|b$"),
        ("a^b", False, r"a\^b"),
        ("#&~", False, r"\#\&\~"),
        ("*a", False, r"\*a"),
        (r"\.", False, r"\."),
    ])
    def test_same_regex_in_rust_syntax(self, pattern, extended, expected) -> None:
        assert _posix_to_rust_regex(pattern, extended=extended) == expected

    @pytest.mark.parametrize("pattern", [r"\1", r"\`a", "[a", r"[[.a.]]", "a\\", "[[:alpha:]]", r"\<a", r"a\)"])
    def test_unsure_forms_are_refused(self, pattern) -> None:
        assert _posix_to_rust_regex(pattern, extended=False) is None

    @pytest.mark.parametrize(("pattern", "extended"), [
        ("foo.+?bar", True), ("ab+?", True), (r"a\{2,3\}\?", False), ("a**", False), ("a{2}?", True),  # quantified quantifier
        ("*foo", True), ("(+a)", True), ("{,2}", True),                                              # leading ERE quantifier
        ("a{,}", True), ("^^a", False),
    ])
    def test_forms_the_two_read_differently_are_refused(self, pattern, extended) -> None:
        assert _posix_to_rust_regex(pattern, extended=extended) is None


@pytest.mark.usefixtures("rg_installed")
class TestWithRipgrep:
    def test_file_searches_move_to_ripgrep_and_text_filters_stay_grep(self) -> None:
        out = _with_ripgrep([
            ["grep", "-rlZ", "a", "."],
            ["xargs", "-0", "grep", "-l", "b"],
            ["grep", "c"],
        ])
        assert out[0][0] == RG
        assert out[1][:3] == ["xargs", "-r", "-0"] and out[1][3] == RG
        assert out[2] == ["grep", "c"]

    def test_a_model_written_ripgrep_gets_the_thread_cap(self) -> None:
        assert _with_ripgrep([["rg", "-l", "x", "."]]) == [["rg", "-j2", "-l", "x", "."]]
        assert _with_ripgrep([["grep", "-rl", "x", "."], ["xargs", "rg", "-l", "y"]])[1] == [
            "xargs", "-r", "rg", "-j2", "-l", "y",
        ]

    @pytest.mark.parametrize("own", [["-j", "8"], ["-j8"], ["--threads=8"], ["--threads", "8"]])
    def test_its_own_thread_setting_is_kept(self, own) -> None:
        tokens = ["rg", *own, "x", "."]
        assert _with_ripgrep([tokens]) == [tokens]


_SYSTEM_RG = shutil.which("rg")


@pytest.mark.skipif(
    not (_SYSTEM_RG and _SYSTEM_RG.startswith(("/usr/", "/bin/"))), reason="ripgrep not installed in a system dir",
)
class TestSameOutputAsGrep:
    """Real ripgrep against real GNU grep on the same files: same lines, any order."""

    @pytest.fixture
    def scope(self, tmp_path: Path) -> Path:
        (tmp_path / "sub").mkdir()
        (tmp_path / "record_a.json").write_text('{"t": "Revenue grew; asana renewal Q3"}\n{"t": "word-word"}\n')
        (tmp_path / "record_b.json").write_text('{"t": "nothing here"}\n')
        (tmp_path / "sub" / "record_c.json").write_text('{"t": "ASANA revenue (draft) 2024 café"}\n', encoding="utf-8")
        return tmp_path

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", [
        "grep -ril asana .",
        "grep -rc revenue .",
        "grep -rli -e asana -e draft .",
        "grep -rE 'reven(ue)+' .",
        r"grep -r '\(asana\|draft\)' .",
        "grep -rw word .",
        "grep -rlZ asana . | xargs -0 grep -il revenue",
        "grep -rl zzz-not-there .",
        "grep -ril asana . | grep sub",
        "grep -rilZ zzz-not-there . | xargs -0 grep -il revenue",  # empty batch
        "grep -ril asana",                                          # no path
        "grep -rE 'caf[[:alpha:]]' .",                              # non-ASCII letter
        "grep -rlxw 'word-word' .",
        "grep -rlE 'ab{,3}c|ren{,2}ewal' .",
        r"grep -rlE '\(draft)' .",
        "ls | xargs grep -il revenue",           # xargs hands grep a directory ("sub") too
        "grep -rlE 'reven.+?ue' .",             # "+?" is "optional" in grep, "lazy" in ripgrep
    ])
    async def test_same_lines(self, scope: Path, cmd: str) -> None:
        ss._rg_binary.cache_clear()
        try:
            with patch.object(ss, "_rg_binary", return_value=None):
                grep_ok, grep_out = await _run_subprocess(cmd, cwd=str(scope))
            assert ss._rg_binary() is not None  # ripgrep is in play
            rg_ok, rg_out = await _run_subprocess(cmd, cwd=str(scope))
        finally:
            ss._rg_binary.cache_clear()
        assert rg_ok == grep_ok, (rg_out, grep_out)
        assert sorted(rg_out.replace("\0", "\n").splitlines()) == sorted(grep_out.replace("\0", "\n").splitlines())


@pytest.mark.asyncio
async def test_xargs_output_from_one_run_is_kept_when_another_run_matched_nothing(tmp_path: Path) -> None:
    """xargs exits 123 when any of its runs exits non-zero; the runs that matched still printed."""
    (tmp_path / "record_a.json").write_text('{"t": "asana Revenue"}')
    (tmp_path / "record_b.json").write_text('{"t": "asana revenue"}')
    with patch.object(ss, "_rg_binary", return_value=None):
        ok, out = await _run_subprocess("grep -rlZ asana . | xargs -0 -n 1 grep -l revenue", cwd=str(tmp_path))
    assert ok is True
    assert out.strip() == "./record_b.json"
