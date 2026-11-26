"""Differential fuzz: whenever Python ``regex`` matches a line of a record, the grep
command the route builds must select the record's stored file.

Records are written by the real blob-storage writer and searched by the real ``grep``.
FUZZ_CASES (default 3000) and FUZZ_SEED set the size; CI uses the defaults.
"""
import os
import random
import shlex
import subprocess

import regex
from fastapi import HTTPException
from pydantic import ValidationError

import app.edition_config  # noqa: F401
from app.api.routes import knowledge_search as ks
from app.modules.transformers.blob_storage import (
    _encode_record_json,
    _plain_envelope_bytes,
)
from app.utils.grep_lines import _split_lines

CASES = int(os.getenv("FUZZ_CASES", "3000"))
SEED = int(os.getenv("FUZZ_SEED", "20261022"))

# (pattern piece, a text it matches)
ATOMS = [
    ("abc", "abc"), ("hello", "hello"), ("compute_v2", "compute_v2"), ("ks", "ks"), ("Straße", "STRASSE"),
    ("straße", "STRASSE"), ("ß", "ẞ"), ("kelvin", "KELVIN"), ("long", "ſong"), ("café", "café"), ("étés", "étés"),
    ("𝄞clef", "𝄞clef"), ('say "hi"', 'say "hi"'), (r"C:\\Users", r"C:\Users"), ("a/b", "a/b"), ("tab\there", "tab\there"),
    ("a\x01b", "a\x01b"), ("a\x7fb", "a\x7fb"), ("x y z", "x y z"), ("a-b_c", "a-b_c"), ("<T>", "<T>"),
    (r"\d{3}", "123"), (r"\w+", "word"), (r"\s", " "), (r"\bfoo", "foo"), (r"foo\b", "foo"), ("^start", "start"),
    ("end$", "end"), (r"\.", "."), (r"\x41", "A"), (r"\u00e9", "é"), (r"\U0001F44D", "👍"), (r"\pL", "L"),
    (r"\N{DIGIT ONE}", "1"), (r"\t", "\t"), (r"[a-c]", "b"), (r"[^x]", "q"), ("[abc]+", "abca"), ("(?:ab|cd)", "cd"),
    ("(ab)+", "abab"), ("a*", ""), ("b+", "bb"), ("c?", "c"), ("d{2}", "dd"), (".", "z"), ("a|b", "b"),
    ("(?=x)", ""), ("(?<!y)", ""), ("(?i)", ""), ("(?f)", ""), ("(?V1)", ""), ("(?i:AbC)", "abc"), ("(?x)", ""),
    ("[[a-z]--[aeiou]]", "b"), ("k{1}", "K"), ("s+", "ſ"),
]
NOISE = ["x", " ", "\n", "\x0c", "\r\n", "é", "ß", "K", "ſ", "\udcff", "\x01", "\t", '"', "\\", "𝄞", "Σ", "ǰ", "ﬃ", "\u2028"]


def _write(root, text: str) -> None:
    for old in root.glob("rec/*"):
        old.unlink()
    record = {"block_containers": {"blocks": [{"index": 0, "data": text}]}}
    (root / "rec").mkdir(exist_ok=True)
    (root / "rec" / "record_abc.json").write_bytes(_plain_envelope_bytes(record, _encode_record_json(record), "abc"))


def _selected(root, command: str) -> bool:
    out = subprocess.run(
        shlex.split(command), cwd=root, capture_output=True, text=True, check=False,
        env={**os.environ, "LC_ALL": "C.UTF-8"},
    )
    return any(not line.endswith(":0") for line in out.stdout.splitlines())


def test_the_prefilter_never_misses_a_record_python_matches(tmp_path) -> None:
    rng = random.Random(SEED)
    checked = matched = 0
    for _ in range(CASES):
        pieces = [rng.choice(ATOMS) for _ in range(rng.randint(1, 4))]
        use_regex = rng.random() < 0.7
        if use_regex:
            pattern = "".join(p for p, _ in pieces)
            if rng.random() < 0.2:
                pattern = rng.choice(["(?i)", "(?f)", "(?V1)", ""]) + pattern
        else:
            pattern = "".join(t or "" for _, t in pieces if "\n" not in (t or "")) or "abc"
        samples = [t for _, t in pieces]
        text = "".join(
            (rng.choice(samples) if rng.random() < 0.6 else rng.choice(NOISE)) for _ in range(rng.randint(1, 8))
        )
        if rng.random() < 0.5:
            text = "".join(rng.choice(samples + NOISE) + (rng.choice(NOISE) if rng.random() < 0.3 else "") for _ in range(3))
        case_sensitive = rng.random() < 0.4
        try:
            command, compiled = ks._grep_command(ks.GrepQuery(pattern=pattern, regex=use_regex, caseSensitive=case_sensitive))
        except (HTTPException, ValidationError, regex.error):
            continue
        try:
            hit = any(compiled.search(line, timeout=0.2) for line in _split_lines(text))
        except TimeoutError:
            continue
        checked += 1
        if not hit:
            continue
        matched += 1
        _write(tmp_path, text)
        assert _selected(tmp_path, command), (pattern, use_regex, case_sensitive, text, command)
    assert matched > CASES // 20, (checked, matched)  # the generator must produce matches, or the test proves nothing
