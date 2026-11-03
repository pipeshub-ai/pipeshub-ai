"""Line-level matches inside one record (app.utils.grep_lines)."""
import pytest
import regex

from app.utils.grep_lines import (
    MAX_SCAN_CHARS,
    ere_prefilter,
    find_matches,
    literal_prefilter,
)


def _rx(p: str) -> "regex.Pattern[str]":
    return regex.compile(p, regex.IGNORECASE)


def _code(start: int, end: int, data: str, **meta) -> dict:
    return {"data": data, "code_metadata": {"start_line": start, "end_line": end, **meta}}


def test_prose_lines_are_numbered_across_blocks_with_context() -> None:
    blocks = [{"data": "alpha\nbeta"}, {"data": "gamma\ndelta"}]
    matches, total, language, _ = find_matches(blocks, _rx("gamma"), context_lines=1, max_matches=5)
    assert total == 1 and language is None
    assert matches[0]["line"] == 3
    assert matches[0]["before"] == ["beta"] and matches[0]["after"] == ["delta"]
    assert "symbol" not in matches[0]


def test_max_matches_cuts_the_list_but_not_the_count() -> None:
    blocks = [{"data": "hit\nhit\nhit"}]
    matches, total, _, _ = find_matches(blocks, _rx("hit"), context_lines=0, max_matches=2)
    assert len(matches) == 2 and total == 3


def test_code_keeps_file_numbers_and_names_the_smallest_enclosing_symbol() -> None:
    blocks = [
        _code(1, 40, "class Cart:", kind="class", qualified_name="class:Cart", language="python"),
        _code(10, 12, "def compute_invoice_total_v2(self):\n    return 1\n", kind="method",
              qualified_name="method:Cart.compute_order_total_v2", language="python"),
    ]
    matches, total, language, _ = find_matches(blocks, _rx("compute_invoice_total_v2"), context_lines=1, max_matches=5)
    assert total == 1 and language == "python"
    assert matches[0]["line"] == 10
    assert matches[0]["symbol"]["name"] == "method:Cart.compute_order_total_v2"
    assert matches[0]["after"] == ["    return 1"]


def test_context_does_not_cross_a_gap_in_code_lines() -> None:
    blocks = [_code(1, 1, "a = 1"), _code(50, 50, "b = needle")]
    matches, _, _, _ = find_matches(blocks, _rx("needle"), context_lines=2, max_matches=5)
    assert matches[0]["line"] == 50 and matches[0]["before"] == []


def test_case_sensitive_pattern_and_unreadable_blocks() -> None:
    blocks = [{"data": None}, {"data": {"row_natural_language_text": "Name: ACME"}}, "junk"]
    assert find_matches(blocks, regex.compile("acme"), context_lines=0, max_matches=5)[1] == 0
    assert find_matches(blocks, _rx("acme"), context_lines=0, max_matches=5)[1] == 1


def test_a_catastrophic_pattern_is_given_up_on_within_the_deadline() -> None:
    import time

    line = ",".join(f"v{i}_{j}" for i, j in zip(range(60), range(60)))
    blocks = [{"data": "\n".join([line] * 400)}]
    pattern = regex.compile(r"(?:.*?,){40}x|v0_0", regex.IGNORECASE)
    start = time.monotonic()
    _, _, _, complete = find_matches(blocks, pattern, context_lines=0, max_matches=5, deadline=start + 1.0)
    assert not complete and time.monotonic() - start < 1.5


def test_a_passed_deadline_reads_nothing_and_says_so() -> None:
    import time

    matches, total, _, complete = find_matches(
        [{"data": "hit"}], _rx("hit"), context_lines=0, max_matches=5, deadline=time.monotonic() - 1,
    )
    assert (matches, total, complete) == ([], 0, False)


async def test_a_slow_pattern_does_not_stall_the_event_loop() -> None:
    import asyncio
    import time

    line = ",".join(f"v{j}" for j in range(60))
    blocks = [{"data": "\n".join([line] * 400)}]
    pattern = regex.compile(r"(?:.*?,){40}x|v0_0", regex.IGNORECASE)
    worst = 0.0

    async def ticker() -> None:
        nonlocal worst
        last = time.monotonic()
        for _ in range(40):
            await asyncio.sleep(0.01)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now

    await asyncio.gather(
        asyncio.to_thread(find_matches, blocks, pattern, context_lines=0, max_matches=5, deadline=time.monotonic() + 0.5),
        ticker(),
    )
    assert worst < 0.1


@pytest.mark.parametrize("python_pattern,literal", [
    (r"\d+", ""), (r"foo\d+bar", "foo"), (r"(?:ab)+\.com", "com"), (r"ab+c", ""), (r"ab*cde", "cde"),
    (r"a|b", ""), (r"^def ", "def "), (r"\bfoo\b", "foo"), ('say "hi"', "say "), (r"C:\\Users", "Users"),
    (r"a\tb", ""), (r"[abc]xyz(?=q)", ""), (r"a{2}bcd", "bcd"), ("(?x) a b", ""), (r"\x41BCD", ""),
    (r"\U0001F44Dokay", ""), (r"(?V1)[[a-z]--[aeiou]]xyz", ""), (r"ab(?i)CDEF", ""), (r"(?f)strasse", ""),
    (r"abc[\w.]+@acme\.xyz", "@acme"), ("étés.abc", "abc"),
])
def test_prefilter_keeps_only_plain_text_every_match_contains(python_pattern, literal) -> None:
    assert ere_prefilter(python_pattern)[0] == literal


def test_prefilter_leaves_out_letters_with_non_ascii_case_variants_when_case_is_ignored() -> None:
    assert ere_prefilter("ksks_abc", ignore_case=True)[0] == "_abc"
    assert ere_prefilter("ksks_abc", ignore_case=False)[0] == "ksks_abc"
    assert literal_prefilter("task id: 42", ignore_case=True) == "d: 42"


def test_prefilter_reads_the_inline_case_flag() -> None:
    assert ere_prefilter("(?i)foo") == ("foo", True)
    assert ere_prefilter("foo") == ("foo", False)


def test_the_letters_with_a_non_ascii_case_variant_are_exactly_those_the_prefilter_leaves_out() -> None:
    from app.utils.grep_lines import _NON_ASCII_FOLD

    # one search per letter over every non-ASCII character at once (a match per character took minutes)
    blob = "".join(chr(cp) for cp in range(0x80, 0x30000))
    found = {
        letter for letter in "abcdefghijklmnopqrstuvwxyz"
        if regex.search(letter, blob, regex.IGNORECASE)
    }
    assert found == {c.lower() for c in _NON_ASCII_FOLD} == {"i", "k", "s"}


def test_lines_are_counted_on_newlines_only() -> None:
    blocks = [{"data": "a = 1\n\x0c\nb = 2\r\n", "code_metadata": {"start_line": 1, "end_line": 3}}]
    matches, *_ = find_matches(blocks, regex.compile("b = 2$"), context_lines=0, max_matches=5)
    assert matches[0]["line"] == 3


async def test_a_costly_request_does_not_starve_a_cheap_one() -> None:
    import asyncio
    import time

    from app.utils.grep_lines import find_matches_bounded

    text = "\n".join(",".join(f"v{i}_{j}" for j in range(60)) for i in range(2000))
    blocks = [{"index": 0, "data": text}]

    async def request(compiled, budget: float, n: int) -> int:
        start = time.monotonic()
        results = await asyncio.gather(*(
            find_matches_bounded(blocks, compiled, context_lines=0, max_matches=5, deadline=start + budget)
            for _ in range(n)
        ))
        return sum(1 for r in results if r[3])

    slow = regex.compile(r"(?:.*?,){40}x|v0_0")
    done = await asyncio.gather(request(slow, 1.0, 30), request(slow, 1.0, 30), request(regex.compile("v1999_59"), 1.0, 20))
    assert done[2] == 20


def test_code_blocks_keep_their_source_in_data_text_and_the_file_summary_is_not_searched() -> None:
    blocks = [
        {"index": 0, "type": "code", "data": {"text": "import os\n\n\n", "kind": "statements", "start_line": 1, "end_line": 3}, "code_metadata": {"start_line": 1}},
        {"index": 1, "type": "code", "data": {"text": "def compute_total(x):\n    return x\n", "kind": "function"},
         "code_metadata": {"start_line": 4, "end_line": 5, "kind": "function", "qualified_name": "function:compute_total"}},
        {"index": 2, "type": "record_summary", "data": {"text": "invoice (python) - function:compute_total", "kind": "file_summary"}},
    ]
    from app.utils.grep_lines import record_lines
    lines = record_lines(blocks)
    assert lines.numbers[lines.texts.index("def compute_total(x):")] == 4
    assert not any("invoice (python)" in t for t in lines.texts)


def test_a_very_long_line_is_scanned_up_to_a_cap_and_the_answer_says_it_is_incomplete() -> None:
    long_line = "x" * (MAX_SCAN_CHARS + 10) + "needle"
    matches, total, _, complete = find_matches([{"data": long_line}], _rx("needle"), context_lines=0, max_matches=5)
    assert (matches, total, complete) == ([], 0, False)
    matches, total, _, complete = find_matches([{"data": "needle " + long_line}], _rx("needle"), context_lines=0, max_matches=5)
    assert total == 1 and complete is False  # a match inside the cap is still found
