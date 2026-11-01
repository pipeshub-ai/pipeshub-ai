"""PATTERN_MATCH_ENGINE=ripgrep: grep stages that read files run on rg and print what GNU grep prints."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.storage_search import landlock_exec, ripgrep
from app.agents.actions.storage_search.ripgrep import (
    _translate,
    grep_to_ripgrep,
    lists_files_only,
    ripgrep_binary,
    ripgrep_threads,
)
from app.agents.actions.storage_search.storage_search import (
    _confined,
    _ripgrep_stage,
    _run_subprocess,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

RG = "/usr/bin/rg"
REAL_RG = shutil.which("rg")
GNU_GREP = shutil.which("grep")
_GNU = GNU_GREP is not None and b"GNU" in subprocess.run([GNU_GREP, "--version"], capture_output=True).stdout
needs_both = pytest.mark.skipif(REAL_RG is None or not _GNU, reason="needs rg and GNU grep")


@pytest.fixture(autouse=True)
def _fresh_lookups(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for cached in (ripgrep._rg_on_path, ripgrep._rg_confinable, ripgrep._utf8_locale):
        cached.cache_clear()
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_CTYPE", raising=False)
    monkeypatch.delenv("PATTERN_MATCH_RG_THREADS", raising=False)
    yield
    for cached in (ripgrep._rg_on_path, ripgrep._rg_confinable, ripgrep._utf8_locale):
        cached.cache_clear()


def walk(tokens: list[str]) -> list[str] | None:
    return grep_to_ripgrep(tokens, RG, walk=True, threads=4)


@pytest.mark.parametrize(("pattern", "expected"), [
    (r"TK-5530\|5530", "TK-5530|5530"),
    ("p|q (r) {s} p+q?", r"p\|q \(r\) \{s\} p\+q\?"),
    (r"\(north\|south\)ward", "(north|south)ward"),
    (r"o\{2,3\}", "o{2,3}"),
    (r"o\{,3\}", "o{0,3}"),
    (r"o\{,\}", "o{0,}"),
    (r"x\.y", r"x\.y"),
    ("^begin", "^begin"),
    ("p^q", r"p\^q"),
    ("tail$", "tail$"),
    ("p$q", r"p\$q"),
    (r"\(p$\)", "(p$)"),
    ("*lead", r"\*lead"),
    ("^*lead", r"^\*lead"),
    (r"\(*lead\)", r"(\*lead)"),
    ("[[:digit:]]x", "[[:digit:]]x"),
    (r"[p\q]", r"[p\\q]"),
    ("[]p]", r"[\]p]"),
    ("[^]p]", r"[^\]p]"),
    ("[p-]", r"[p\-]"),
    ("[a-f0-9]", "[a-f0-9]"),
    ("[&~]", r"[\&\~]"),
    (r"\/", "/"),
])
def test_basic_regex_translation(pattern: str, expected: str) -> None:
    assert _translate(pattern, extended=False) == expected


@pytest.mark.parametrize(("pattern", "expected"), [
    ("p|q", "p|q"),
    (r"x\|y", r"x\|y"),
    ("o{", r"o\{"),
    ("o{k}", r"o\{k\}"),
    ("o{2,3}", "o{2,3}"),
    ("o{,3}", "o{0,3}"),
    ("(re)?load", "(re)?load"),
    (r"o\{1\}", r"o\{1\}"),
])
def test_extended_regex_translation(pattern: str, expected: str) -> None:
    assert _translate(pattern, extended=True) == expected


@pytest.mark.parametrize(("pattern", "extended"), [
    (r"\(p\)\1", False),        # backreference
    (r"\wrd", False),           # word class: glibc and Unicode hold different characters
    (r"p\sq", False),
    (r"\<lead", False),
    (r"\d", False),             # a letter escape means something else in each GNU version
    ("[[:alpha:]]", False),     # ASCII-only in Rust
    ("[[=p=]]", False),
    ("[[.p.]]", False),
    ("[é-ü]", False),           # a non-ASCII range needs the locale's collation
    ("[z-a]", False),
    ("[p", False),
    ("trailing\\", False),
    (r"\{1\}p", False),         # a leading BRE interval
    (r"o\{3,2\}", False),
    ("o{1,2,3}", True),         # GNU refuses some malformed intervals and reads others as text
    ("o{}", True),
    ("+lead", True),            # GNU drops a leading ERE quantifier; Rust refuses it
    ("(*lead)", True),
    ("o**", True),              # Rust versions differ on a repeated repetition
    ("p)", True),
    ("(p", True),
    ("o{300}", True),
])
def test_patterns_without_an_exact_rust_form_stay_on_grep(pattern: str, extended: bool) -> None:
    assert _translate(pattern, extended=extended) is None


def test_first_stage_argv() -> None:
    assert walk(["grep", "-rliZ", r"TK-5530\|5530", "."]) == [
        RG, "--no-config", "--no-ignore", "--hidden", "--threads", "4",
        "--ignore-case", "--files-with-matches", "--null", "--regexp", "TK-5530|5530", "--", ".",
    ]
    assert walk(["egrep", "-rc", "-e", "p|q", "-e", "r", "./A", "./B"])[6:] == [
        "--count", "--include-zero", "--regexp", "p|q", "--regexp", "r", "--", "./A", "./B",
    ]
    assert walk(["fgrep", "-rl", "p.q", "."])[-5:] == ["--fixed-strings", "--regexp", "p.q", "--", "."]
    assert walk(["grep", "-rl", "x", ".", "-i"])[-4:] == ["--regexp", "x", "--", "."]
    assert walk(["grep", "-rl", "-m", "2", "x", "."])[-6:] == ["--max-count", "2", "--regexp", "x", "--", "."]


@pytest.mark.parametrize("tokens", [
    ["grep", "-li", "x", "."],              # a first stage that does not recurse
    ["grep", "-rl", "x"],                   # no path: grep walks ".", rg would read the pipe
    ["grep", "-rP", "x", "."],              # a flag with no exact rg form
    ["grep", "-rlw", "x", "."],
    ["grep", "-rL", "x", "."],              # exit status differs between versions
    ["grep", "-rlE", "-F", "x", "."],       # GNU grep refuses both matchers
    ["egrep", "-rlF", "x", "."],
    ["grep", "-rlc", "x", "."],
    ["grep", "-rl", "-m5", "x", "."],
    ["grep", "-rl", r"\(p\)\1", "."],
    ["rg", "-l", "x", "."],
    ["xargs", "-0", "grep", "-l", "x"],
])
def test_stages_ripgrep_cannot_run_exactly_stay_on_grep(tokens: list[str]) -> None:
    assert walk(tokens) is None


def test_xargs_stage_runs_on_rg_only_when_handed_files() -> None:
    argv = ["xargs", "-0", "grep", "-Hci", r"north\|south"]
    translated, threads = _ripgrep_stage(argv, RG, first=False, previous=["grep", "-rliZ", "east", "."])
    assert threads == min(4, ripgrep.effective_cpu_count())
    assert translated[:3] == ["xargs", "-0", RG]
    assert translated[-4:] == ["--include-zero", "--regexp", "north|south", "--"]
    # "find ." lists directories too, which grep refuses and rg would search.
    assert _ripgrep_stage(argv, RG, first=False, previous=["find", "."]) == (argv, 1)
    assert _ripgrep_stage(argv, RG, first=False, previous=["grep", "-rcZ", "east", "."]) == (argv, 1)
    line_filter = ["grep", "-v", ":0$"]
    assert _ripgrep_stage(line_filter, RG, first=False, previous=argv) == (line_filter, 1)


@pytest.mark.parametrize(("tokens", "expected"), [
    (["grep", "-rliZ", "x", "."], True),
    (["grep", "--files-with-matches", "x", "."], True),
    (["grep", "-rci", "x", "."], False),
    (["find", ".", "-type", "f", "-name", "*.json", "-print0"], True),
    (["find", ".", "-type", "f", "-o", "-type", "d"], False),
    (["find", ".", "-not", "-type", "f"], False),
    (["find", ".", "-name", "*.json"], False),
])
def test_stages_that_list_only_files(tokens: list[str], expected: bool) -> None:
    assert lists_files_only(tokens) is expected


@pytest.mark.parametrize(("setting", "cpus", "threads"), [(None, 8, 4), ("32", 48, 8), ("6", 4, 4), ("0", 8, 1)])
def test_threads_stay_within_eight_and_the_cpus(
    monkeypatch: pytest.MonkeyPatch, setting: str | None, cpus: int, threads: int,
) -> None:
    monkeypatch.setattr(ripgrep, "effective_cpu_count", lambda: cpus)
    if setting is not None:
        monkeypatch.setenv("PATTERN_MATCH_RG_THREADS", setting)
    assert ripgrep_threads() == threads


class TestEngineSwitch:
    def test_off_unless_asked_for(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ripgrep.shutil, "which", lambda name: RG)
        monkeypatch.delenv("PATTERN_MATCH_ENGINE", raising=False)
        assert ripgrep_binary(confined=False) is None
        monkeypatch.setenv("PATTERN_MATCH_ENGINE", "grep")
        assert ripgrep_binary(confined=False) is None
        monkeypatch.setenv("PATTERN_MATCH_ENGINE", "ripgrep")
        assert ripgrep_binary(confined=False) == RG

    def test_without_rg_it_stays_on_grep_and_says_so_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(ripgrep.shutil, "which", lambda name: None)
        monkeypatch.setenv("PATTERN_MATCH_ENGINE", "ripgrep")
        with caplog.at_level(logging.WARNING, logger=ripgrep.__name__):
            assert ripgrep_binary(confined=False) is None
            assert ripgrep_binary(confined=False) is None
        assert sum("not on PATH" in r.getMessage() for r in caplog.records) == 1

    def test_needs_a_utf8_locale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ripgrep.shutil, "which", lambda name: RG)
        monkeypatch.setenv("PATTERN_MATCH_ENGINE", "ripgrep")
        monkeypatch.setenv("LC_ALL", "C")
        assert ripgrep_binary(confined=False) is None
        monkeypatch.setenv("LC_ALL", "en_US.utf8")
        assert ripgrep_binary(confined=False) == RG

    def test_a_confined_stage_runs_rg_only_from_the_system_directories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATTERN_MATCH_ENGINE", "ripgrep")
        monkeypatch.setattr(ripgrep.shutil, "which", lambda name: "/opt/tools/rg")
        assert ripgrep_binary(confined=True) is None
        assert ripgrep_binary(confined=False) == "/opt/tools/rg"
        ripgrep._rg_on_path.cache_clear()
        monkeypatch.setattr(ripgrep.shutil, "which", lambda name: RG)
        assert ripgrep_binary(confined=True) == RG


class TestConfinedCpuTime:
    def test_each_thread_gets_the_stage_cpu_time(self) -> None:
        one = _confined(["grep", "-rl", "x", "."], "/data", 1)
        assert one == [sys.executable, "-I", "-S", one[3], "/data", "--", "grep", "-rl", "x", "."]
        four = _confined([RG, "x"], "/data", 4)
        assert four[5:7] == [f"--cpu-seconds={landlock_exec.CPU_SECONDS * 4}", "--"]

    def test_the_wrapper_applies_the_cpu_time_it_is_given(self) -> None:
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import landlock_exec, resource;"
            "landlock_exec.limit_resources(97);"
            "print(resource.getrlimit(resource.RLIMIT_CPU)[1])"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code, os.path.dirname(landlock_exec.__file__)],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
        assert int(out) <= 97

    @pytest.mark.parametrize("value", ["x", "0", "-5"])
    def test_the_wrapper_refuses_a_bad_cpu_time(self, value: str, tmp_path: Path) -> None:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", landlock_exec.__file__, str(tmp_path), f"--cpu-seconds={value}", "--", "true"],
            capture_output=True, timeout=30,
        )
        assert proc.returncode == 2


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "alpha" / "deep").mkdir(parents=True)
    (tmp_path / "beta").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "alpha" / "deep" / "record_1.json").write_text("TK-5530 latency spike\nTK 5530\nLatency regression\n")
    (tmp_path / "beta" / "record_2.json").write_text("unrelated words\n5530\n")
    (tmp_path / ".cache" / "record_3.json").write_text("TK-5530 kept out of sight\n")
    (tmp_path / "beta" / "record_4.json").write_text("p|q (paren) {k} p+q? p^q p$q tail 2024\n")
    (tmp_path / "beta" / "record_5.json").write_text("Grüße aus Köln, latency\n")
    return tmp_path


def _records(out: bytes) -> list[bytes]:
    # rg's threads print files in any order; -c -Z prints "path\0count" lines.
    return sorted(out.split(b"\n") if b"\n" in out else out.split(b"\0"))


@needs_both
@pytest.mark.parametrize("tokens", [
    ["grep", "-rliZ", r"TK-5530\|5530", "."],
    ["grep", "-rHci", r"latency\|spike", "."],
    ["grep", "-rciZ", "latency", "."],
    ["grep", "-rl", "p|q (paren) {k} p+q?", "."],
    ["grep", "-rl", "p^q", "."],
    ["grep", "-rl", "p$q", "."],
    ["grep", "-rlx", "TK 5530", "."],
    ["grep", "-rl", r"\(latency\|regression\) spike", "."],
    ["egrep", "-rli", "(latency|regression)$", "."],
    ["fgrep", "-rl", "p+q?", "."],
    ["grep", "-rlE", "[[:digit:]]{4}", "."],
    ["grep", "-rc", r"5\{0,2\}3", "./alpha", "./beta"],
    ["grep", "-rli", "-m", "1", "latency", "."],
    ["grep", "-rlZ", "-e", "spike", "-e", "regression", "."],
    ["grep", "-rli", "grüße\\|KÖLN", "."],
    ["grep", "-ri", "latency", "."],
    ["grep", "-rl", "nothing matches this", "."],
])
def test_ripgrep_output_matches_gnu_grep(tree: Path, tokens: list[str]) -> None:
    rg_argv = grep_to_ripgrep(tokens, REAL_RG, walk=True, threads=4)
    assert rg_argv is not None
    dialect = {"grep": [], "egrep": ["-E"], "fgrep": ["-F"]}[tokens[0]]
    env = {"LANG": "C.UTF-8", "PATH": os.environ.get("PATH", "")}
    gnu = subprocess.run([GNU_GREP, *dialect, *tokens[1:]], cwd=tree, capture_output=True, env=env)
    rg = subprocess.run(rg_argv, cwd=tree, capture_output=True, env=env)
    assert gnu.returncode == rg.returncode
    assert _records(gnu.stdout) == _records(rg.stdout)


@needs_both
def test_an_xargs_stage_matches_gnu_grep_on_the_files_it_is_handed(tree: Path) -> None:
    files = b"./alpha/deep/record_1.json\0./beta/record_2.json\0./beta/record_5.json\0"
    tokens = ["grep", "-Hci", r"latency\|spike"]
    rg_argv = grep_to_ripgrep(tokens, REAL_RG, walk=False, threads=4)
    gnu = subprocess.run(["xargs", "-0", GNU_GREP, *tokens[1:]], cwd=tree, input=files, capture_output=True)
    rg = subprocess.run(["xargs", "-0", *rg_argv], cwd=tree, input=files, capture_output=True)
    assert gnu.returncode == rg.returncode
    assert _records(gnu.stdout) == _records(rg.stdout)


@needs_both
def test_run_subprocess_runs_the_file_stages_on_rg(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        seen.append(list(argv))
        return await real_exec(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    command = r'grep -rliZ "TK-5530\|5530" . | xargs -0 grep -Hci "latency\|spike" | grep -v ":0$"'
    monkeypatch.setenv("PATTERN_MATCH_ENGINE", "grep")
    off_ok, off_out = asyncio.run(_run_subprocess(command, cwd=str(tree)))
    monkeypatch.setenv("PATTERN_MATCH_ENGINE", "ripgrep")
    on_ok, on_out = asyncio.run(_run_subprocess(command, cwd=str(tree)))
    assert [argv[0] for argv in seen] == ["grep", "xargs", "grep", REAL_RG, "xargs", "grep"]
    assert seen[4][2] == REAL_RG
    assert off_ok and on_ok
    assert sorted(off_out.split("\n")) == sorted(on_out.split("\n"))
