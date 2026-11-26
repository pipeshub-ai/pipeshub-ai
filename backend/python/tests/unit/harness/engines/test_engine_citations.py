"""CI-1..CI-8 (S1b): the engine's answer, with its citations mapped to the refs of records it read."""

from __future__ import annotations

import pytest

from app.harness.engines.engine_citations import (
    CitationRewrite,
    ReadRecords,
    records_read,
    rewrite_engine_citations,
)

URLS = {
    "ref1": "https://kb.example/record/rec-exp/preview#blockIndex=0",
    "ref4": "https://kb.example/record/rec-tr23/preview#blockIndex=0",
    "ref5": "https://kb.example/record/rec-tr25/preview#blockIndex=0",
    "ref6": "https://kb.example/record/rec-tr25/preview#blockIndex=1",
    "ref9": "https://kb.example/record/rec-other/preview#blockIndex=0",
}
# What the eval's knowledge fixture renders (``Record:``) and what production renders (``Record ID``, ``Name``, ``Web URL``).
SEARCH = (
    "Top 3 blocks from 3 records (ranked sample — other records may match).\n\n"
    "<record>\n\nRecord: Expense policy\nRecord blocks (sorted):\n[0|ref1] Attach a receipt.\n\n</record>\n"
    "<record>\nRecord ID: rec-tr23\nName: Travel policy\nConnector: CONFLUENCE\n"
    "Web URL: https://wiki.example/pages/travel-2023\n\nRecord blocks (sorted):\n\n[0|ref4] Superseded.\n</record>\n"
    "<record>\nRecord ID: rec-tr25\nName: Travel policy (2025)\nWeb URL: https://wiki.example/pages/travel-2025\n\n"
    "Record blocks (sorted):\n\n[0|ref5] Economy under 6 hours.\n[1|ref6] Hotel cap $220.\n</record>\n"
)


@pytest.fixture
def read() -> ReadRecords:
    return records_read([SEARCH], URLS)


def rewrite(text: str, read: ReadRecords) -> CitationRewrite:
    return rewrite_engine_citations(text, read)


# CI-1


def test_ci_1_records_are_grouped_with_what_names_them(read: ReadRecords) -> None:
    expense, old, new = read.records
    assert expense.refs == ("ref1",) and expense.titles == frozenset({"expense policy"})
    assert "rec-exp" in expense.ids
    assert old.refs == ("ref4",) and old.titles == frozenset({"travel policy"})
    assert old.ids >= {"rec-tr23"} and old.urls == frozenset({"https://wiki.example/pages/travel-2023"})
    assert new.refs == ("ref5", "ref6")
    assert read.refs == {"ref1", "ref4", "ref5", "ref6"}


def test_ci_1_a_ref_outside_a_record_is_its_own_record_and_text_without_refs_gives_nothing() -> None:
    read = records_read(['{"answer": "see ref9"}', "no refs here"], URLS)
    (only,) = read.records
    assert only.refs == ("ref9",) and only.ids == frozenset({"rec-other"})
    assert records_read([], URLS).records == ()


def test_ci_1_json_escaped_results_are_read_by_their_lines() -> None:
    escaped = SEARCH.replace("\n", "\\n")
    assert [r.refs for r in records_read([escaped], URLS).records] == [("ref1",), ("ref4",), ("ref5", "ref6")]


# CI-2


def test_ci_2_a_block_label_copied_verbatim_becomes_a_link(read: ReadRecords) -> None:
    out = rewrite("Economy under 6 hours [0|ref5]. Hotels capped [1|ref6].", read)
    assert out.text == "Economy under 6 hours [source](ref5). Hotels capped [source](ref6)."
    assert (out.kept, out.mapped, out.dropped) == (2, 2, 0)


# CI-3


@pytest.mark.parametrize(
    "definitions",
    [
        "[1]: ref5\n[2]: ref1",
        "[1] Travel policy (2025)\n[2] Expense policy",
        "[^1]: rec-tr25\n[^2]: Expense policy (Confluence)",
        "[1]: https://wiki.example/pages/travel-2025\n[2]: Expense policy",
        "Sources:\n1. Travel policy (2025), effective March 2025\n2. Expense policy",
        "## References\n\n1) **Travel policy (2025)**\n2) Expense policy",
        "Sources: [1] Travel policy (2025); [2] Expense policy",
    ],
)
def test_ci_3_footnotes_map_to_the_records_ref_and_the_list_goes(read: ReadRecords, definitions: str) -> None:
    text = f"Book economy under 6 hours [1]. Receipts above $25 [2].\n\n{definitions}\n"
    if definitions.startswith("[^"):
        text = text.replace("[1]", "[^1]").replace("[2]", "[^2]")
    out = rewrite(text, read)
    assert out.text.strip() == "Book economy under 6 hours [source](ref5). Receipts above $25 [source](ref1)."
    assert (out.kept, out.mapped, out.dropped) == (2, 2, 0)


def test_ci_3_a_marker_with_several_numbers_and_an_unpointed_source(read: ReadRecords) -> None:
    out = rewrite("Policy changed [1, 2].\n\nSources:\n[1] Travel policy (2025)\n[2] Travel policy\n- Expense policy\n", read)
    assert out.text == "Policy changed [source](ref5) [source](ref4).\n\n[source](ref1)\n"
    assert (out.mapped, out.dropped) == (3, 0)


# CI-4


def test_ci_4_links_to_a_record_and_mentions_name_its_ref(read: ReadRecords) -> None:
    text = (
        "Economy [policy](https://wiki.example/pages/travel-2025). Superseded [old](https://kb.example/record/rec-tr23/x). "
        "Receipts (source: Expense policy). Hotels (Record ID: rec-tr25). Both (sources: Expense policy; Travel policy (2025))."
    )
    out = rewrite(text, read)
    assert out.text == (
        "Economy [policy](ref5). Superseded [old](ref4). Receipts [source](ref1). Hotels [source](ref5). "
        "Both [source](ref1) [source](ref5)."
    )
    assert (out.kept, out.mapped, out.dropped) == (6, 6, 0)


# CI-5


def test_ci_5_bare_and_bracketed_refs_use_todays_normalizer(read: ReadRecords) -> None:
    out = rewrite("A ref5. B [ref1]. C [source](ref4, ref6).", read)
    assert out.text == "A [source](ref5). B [source](ref1). C [source](ref4) [source](ref6)."
    assert (out.kept, out.dropped) == (4, 0)


# CI-6


def test_ci_6_citations_of_records_not_read_are_removed_and_counted(read: ReadRecords) -> None:
    text = (
        "Margins rose [x](ref9). Economy [source](ref5). Unknown [source](ref77) too.\n"
        "Leave is 25 days [1]. Remote (source: Remote work policy). Elsewhere [w](https://kb.example/record/rec-none/p).\n\n"
        "[1]: Leave policy\n"
    )
    out = rewrite(text, read)
    assert out.text.rstrip() == (
        "Margins rose. Economy [source](ref5). Unknown too.\n"
        "Leave is 25 days. Remote. Elsewhere [w](https://kb.example/record/rec-none/p)."
    )
    assert (out.kept, out.dropped) == (1, 4)


# CI-7


@pytest.mark.parametrize(
    "text",
    [
        "Revenue was $42 in Q3.",
        "Use items[1] and the list [1] has no definition.",
        "See [the vendor page](https://vendor.example/pricing) for list prices.",
        "Steps:\n1. Open the file\n2. Sum the column",
    ],
)
def test_ci_7_text_without_engine_citations_is_unchanged(read: ReadRecords, text: str) -> None:
    out = rewrite(text, read)
    assert (out.text, out.kept, out.mapped, out.dropped) == (text, 0, 0, 0)


# CI-8


def test_ci_8_the_longest_matching_title_wins(read: ReadRecords) -> None:
    assert read.ref_for("Travel policy (2025) section 2") == "ref5"
    assert read.ref_for("the Travel policy") == "ref4"
    assert read.ref_for("ref6 and ref5") == "ref6"
    assert read.ref_for("nothing at all") is None
