"""The orjson record parse and the cheaper grep-output parse give what the
stdlib versions they replaced gave, on ordinary and adversarial input."""

import json
import os
import random
import re

import pytest

from app.agents.actions.storage_search import storage_search as mod

_RECORD_FILENAME_RE = re.compile(r"record_([0-9a-f\-]+)\.json$", re.IGNORECASE)
_GREP_COUNT_RE = re.compile(r"^(.*\.json):(\d+)$", re.IGNORECASE)


def _old_record_lines(output: str) -> list[tuple[str, str, int]]:
    line_vrids: list[tuple[str, str, int]] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or raw.startswith("[...output truncated"):
            continue
        path = line
        match_count = 1
        csm = _GREP_COUNT_RE.match(line)
        if csm:
            candidate_path = csm.group(1)
            count = int(csm.group(2))
            if count == 0:
                continue
            if _RECORD_FILENAME_RE.match(os.path.basename(candidate_path)):
                path = candidate_path
                match_count = count
        fm = _RECORD_FILENAME_RE.match(os.path.basename(path))
        if fm:
            line_vrids.append((path, fm.group(1), match_count))
    line_vrids.sort(key=lambda x: x[2], reverse=True)
    return line_vrids


def _old_record_search_text(raw: str) -> str:
    try:
        rec = json.loads(raw).get("record")
    except (ValueError, AttributeError, RecursionError):
        return "\n".join(mod._unescaped_block_data(raw))
    if not isinstance(rec, dict):
        return ""
    parts: list[str] = []
    blocks = (rec.get("block_containers") or {}).get("blocks") or []
    parts.extend(b["data"] for b in blocks if isinstance(b, dict) and isinstance(b.get("data"), str))
    summary = (rec.get("semantic_metadata") or {}).get("summary")
    if isinstance(summary, str):
        parts.append(summary)
    return "\n".join(p for p in parts if p)


def _same(fn_old, fn_new, arg) -> None:
    def run(fn) -> tuple[str, object]:
        try:
            return ("ok", fn(arg))
        except Exception as e:
            return ("err", type(e))
    assert run(fn_new) == run(fn_old), repr(arg)[:200]


_VRID = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
_GREP_LINES = [
    f"./grp/doc/sid/record_{_VRID}.json",
    f"./grp/doc/sid/record_{_VRID}.json:7",
    f"./grp/doc/sid/record_{_VRID}.json:0",
    f"./grp/doc/sid/record_{_VRID}.json:00",
    f"./grp/doc/sid/record_{_VRID}.json:012",
    f"record_{_VRID}.json:3",
    f"record_{_VRID}.json",
    f"/abs/RECORD_{_VRID.upper()}.JSON:4",
    f"  ./x/record_{_VRID}.json:2  ",
    f"\t./x/record_{_VRID}.json\t",
    f"C:\\data\\grp\\record_{_VRID}.json:5",
    f"C:\\data\\grp\\record_{_VRID}.json",
    f"./a\\b/record_{_VRID}.json",
    f"./dir/record_{_VRID}.json/record_{_VRID}.json:9",
    f"./dir:3/record_{_VRID}.json:1",
    f"./x/record_{_VRID}.json:1:2",
    f"./x/record_{_VRID}.json:",
    f"./x/record_{_VRID}.json:-1",
    f"./x/record_{_VRID}.json:٣",  # non-ASCII digit: \d and int() accept it
    f"./x/record_{_VRID}.jsonx",
    f"./x/record_{_VRID}.json.bak",
    f"./x/xrecord_{_VRID}.json",
    "./x/record_.json",
    "./x/record_zz.json:4",
    f"./x/record_{_VRID}.json ",
    "./x/metadata.json:5",
    "./x/notes.txt:5",
    "./x/notes.txt",
    "Binary file ./x/record_ab.json matches",
    f"./x/record_{_VRID}.json:4:matched text with record_ab.json",
    "[...output truncated (123456 chars total)]",
    f"  [...output truncated record_{_VRID}.json",
    f"[...output truncated ./x/record_{_VRID}.json:3",
    "",
    "   ",
    f"./x/record_{_VRID}.json\x85./y/record_ab.json:2",
    f"./x/record_{_VRID}.json\u2028./y/record_cd.json",
]


@pytest.mark.parametrize("line", _GREP_LINES)
def test_record_lines_single_line_matches_old(line) -> None:
    for text in (line, line + "\n", line + "\r\n"):
        _same(_old_record_lines, mod._record_lines, text)


def test_record_lines_mixed_outputs_match_old() -> None:
    rng = random.Random(428)
    for _ in range(300):
        lines = [rng.choice(_GREP_LINES) for _ in range(rng.randint(0, 60))]
        lines += [f"./c{rng.randint(0, 9)}/record_{rng.getrandbits(64):x}.json:{rng.randint(0, 5)}" for _ in range(20)]
        rng.shuffle(lines)
        _same(_old_record_lines, mod._record_lines, rng.choice(["\n", "\r\n"]).join(lines))


def test_record_lines_drops_a_line_of_nul_joined_paths() -> None:
    # `find -print0 -o -print` can print two paths on one line: the vrid would be
    # taken from the second while ranking reads the first.
    text = f"./x/record_ab.json\x00./y/record_{_VRID}.json\n./z/record_cd.json\n"
    assert [vrid for _path, vrid, _n in mod._record_lines(text)] == ["cd"]


def _record(blocks, summary="the summary", **extra) -> dict[str, dict]:
    return {"record": {"block_containers": {"blocks": blocks}, "semantic_metadata": {"summary": summary}, **extra}}


_RECORD_TEXTS = [
    json.dumps(_record([{"data": "alpha"}, {"data": "βeta ✓ 😀"}, {"data": ""}, {"data": 5}, "x", {"no": 1}])),
    json.dumps(_record([{"data": "a"}], summary=None)),
    json.dumps(_record([{"data": "a"}]), ensure_ascii=False),
    json.dumps(_record([{"data": "😀 \u2028 \x00 \\ \""}]), ensure_ascii=True),
    json.dumps({"record": None}),
    json.dumps({"record": [1]}),
    json.dumps({"record": {}}),
    json.dumps({"record": {"block_containers": None, "semantic_metadata": None}}),
    json.dumps({"record": {"block_containers": {"blocks": None}}}),
    json.dumps({"record": {"block_containers": [1]}}),
    json.dumps({"record": {"block_containers": {"blocks": 7}}}),
    json.dumps({"record": {"block_containers": {"blocks": {"data": "k"}}}}),
    json.dumps([{"record": {}}]),
    '"string"', "1", "null", "true", "[]", "{}", "",
    # NaN, Infinity and big ints: json accepts, orjson refuses or turns into floats.
    '{"record": {"block_containers": {"blocks": [{"data": "n", "x": NaN}]}, "semantic_metadata": {"summary": "s", "v": -Infinity}}}',
    '{"record": {"block_containers": {"blocks": [{"data": "b", "n": %d}]}, "x": 1e400}}' % (2**70),
    '{"record": {"block_containers": %d}}' % (2**70),
    '{"record": {"block_containers": {"blocks": %d}}}' % (2**70),
    # Escaped surrogates: a lone one, a reversed pair, a valid pair.
    '{"record": {"block_containers": {"blocks": [{"data": "lone \\ud800 x"}]}}}',
    '{"record": {"block_containers": {"blocks": [{"data": "rev \\ude00\\ud83d"}]}}}',
    '{"record": {"block_containers": {"blocks": [{"data": "pair \\ud83d\\ude00"}]}}}',
    '{"record": {"semantic_metadata": {"summary": "raw lone \ud800"}}}',
    # Duplicate keys: last one wins in both.
    '{"record": {"semantic_metadata": {"summary": "first", "summary": "second"}}, "record": {"semantic_metadata": {"summary": "third"}}}',
    # Not JSON: cut at the read cap, BOM, trailing junk, control characters.
    json.dumps(_record([{"data": "one"}, {"data": "two \\\"q\\\""}]))[:-7],
    "\ufeff" + json.dumps(_record([{"data": "bom"}])),
    json.dumps(_record([{"data": "junk"}])) + " x",
    '{"record": {"block_containers": {"blocks": [{"data": "ctl \x01 c"}]}}}',
    '{"record": {"block_containers": {"blocks": [{"data": "bad \\x escape"}, {"data": "ok"}]}}}',
    " \n\t" + json.dumps(_record([{"data": "ws"}])) + "\n ",
    "\x00",
]


@pytest.mark.parametrize("raw", _RECORD_TEXTS)
def test_record_search_text_matches_old(raw) -> None:
    _same(_old_record_search_text, mod._record_search_text, raw)


@pytest.mark.parametrize("depth", [10, 1000, 1024, 1025, 1100, 5000, 200_000])
def test_record_search_text_deep_nesting_matches_old(depth) -> None:
    raw = '{"record": {"semantic_metadata": {"summary": "deep"}, "x": ' + "[" * depth + "]" * depth + "}}"
    _same(_old_record_search_text, mod._record_search_text, raw)
    _same(_old_record_search_text, mod._record_search_text, "[" * depth + "]" * depth)
