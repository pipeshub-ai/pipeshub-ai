"""Exact-text matching through the lexical index (``app.utils.exact_text_index``)."""

import logging
from unittest.mock import patch

import pytest

from app.utils.exact_text_index import (
    INDEX_CANDIDATE_CHUNKS,
    MAX_RECORDS_PER_COMMAND,
    IndexMatchOutcome,
    anchor_words,
    plan_command,
    run_index_pattern_match,
)
from tests.support.lexical_index import InvertedIndexSearcher


async def _match(searcher, command: str, user_id: str = "u1") -> IndexMatchOutcome:
    plan = plan_command(command)
    assert plan is not None
    return await run_index_pattern_match(
        [plan], searcher, user_id=user_id, org_id="o1", filters=None,
        logger_instance=logging.getLogger("test"),
    )


class TestAnchorWords:
    @pytest.mark.parametrize(("pattern", "words"), [
        ("p99 latency", ["p99", "latency"]),
        ("ERR_CONN_RESET", ["err_conn_reset"]),
        (r"\bdeploy\b", ["deploy"]),
        (r"edge proxy, approved", ["edge", "proxy", "approved"]),
        (r"out of (memory|disk)", []),
        ("the of and", []),
        # A word glued to a regex construct is not a whole word of the text.
        (r"ci.cd", []),
        (r"colou?r", []),
        (r"[0-9]+ms", []),
        (r"[0-9]{3}-[0-9]{4}", []),
        (r"(?:deploy)+ment", []),
        (r"rollback [a-z]+ plan", ["rollback", "plan"]),
    ])
    def test_words_every_match_must_contain(self, pattern: str, words: list[str]) -> None:
        assert anchor_words(pattern) == words


class TestPlanCommand:
    def test_or_alternatives_share_one_lookup(self) -> None:
        plan = plan_command(r'grep -rci "p99\|latency\|tail latency" .')

        assert plan is not None and len(plan.stages) == 1
        assert plan.stages[0].query == "p99 latency tail"
        assert [rx.pattern for rx in plan.stages[0].regexes] == ["p99", "latency", "tail latency"]

    def test_each_and_stage_is_its_own_lookup(self) -> None:
        plan = plan_command(r'grep -rliZ "invoice\|billing" . | xargs -0 grep -Hci "recurring"')

        assert plan is not None
        assert [s.query for s in plan.stages] == ["invoice billing", "recurring"]

    @pytest.mark.parametrize("command", [
        'grep -rciE "[0-9]{3}-[0-9]{4}" .',  # no literal word at all
        'grep -rci "the" .',  # stop words only
        'grep -rciE "p99|[0-9]+" .',  # one alternative has no word
        'grep -rlv "draft" .',  # inverted: the index cannot list what lacks a word
        'grep -rliZ "a" . | xargs -0 grep -L "draft"',
        'grep -rcx "exact line" .',
        'grep -rciE "(a+)+b" .',  # the verifier would have to match it literally
        'grep -rciE "colou?r" .',  # "colo" is not a word of "colour"
        'grep -rciE "error.*rollback" .',  # the verifier would bound ".*"
    ])
    def test_a_command_the_index_cannot_express_is_left_to_the_scan(self, command: str) -> None:
        assert plan_command(command) is None

    def test_case_is_kept_when_grep_is_case_sensitive(self) -> None:
        sensitive = plan_command('grep -rc "Kafka" .')
        insensitive = plan_command('grep -rci "Kafka" .')

        assert not sensitive.stages[0].regexes[0].search("kafka")
        assert insensitive.stages[0].regexes[0].search("kafka")

    def test_word_regexp_matches_whole_words_only(self) -> None:
        plan = plan_command('grep -rcwi "cache" .')

        assert plan.stages[0].regexes[0].search("the cache tier")
        assert not plan.stages[0].regexes[0].search("the cached tier")


class TestRunIndexPatternMatch:
    @pytest.mark.asyncio
    async def test_a_hit_is_verified_against_the_exact_pattern(self) -> None:
        searcher = InvertedIndexSearcher([
            ("vr-exact", "the p99 latency regression after the deploy"),
            ("vr-scattered", "latency was fine; p99 regression elsewhere"),
        ])

        outcome = await _match(searcher, 'grep -rci "p99 latency regression" .')

        assert [h["virtual_record_id"] for h in outcome.hits[0]] == ["vr-exact"]
        assert "p99 latency regression" in outcome.hits[0][0]["match_preview"]
        assert outcome.capped is False

    @pytest.mark.asyncio
    async def test_and_stages_are_decided_on_the_record_not_the_chunk(self) -> None:
        # The rare word's chunk and the common word's chunk of vr-both are
        # different chunks, and the common word fills the stage's limit.
        chunks = [(f"vr-{i}", "cache cache warm") for i in range(INDEX_CANDIDATE_CHUNKS + 50)]
        chunks += [("vr-both", "zebrafinch p99 report"), ("vr-both", "the cache tier")]
        searcher = InvertedIndexSearcher(chunks)

        outcome = await _match(searcher, 'grep -rliZ "zebrafinch" . | xargs -0 grep -Hci "cache"')

        assert [h["virtual_record_id"] for h in outcome.hits[0]] == ["vr-both"]
        # The rare stage was returned in full, so the result is complete.
        assert outcome.capped is False
        # The common word is looked up only within the rare word's records.
        assert [c["virtual_record_ids"] for c in searcher.calls if "cache" in c["queries"]] == [["vr-both"]]

    @pytest.mark.asyncio
    async def test_when_no_stage_is_returned_in_full_each_is_looked_up_everywhere(self) -> None:
        chunks = [(f"vr-a{i}", "cache warm") for i in range(INDEX_CANDIDATE_CHUNKS + 5)]
        chunks += [(f"vr-b{i}", "proxy cold") for i in range(INDEX_CANDIDATE_CHUNKS + 5)]
        searcher = InvertedIndexSearcher(chunks)

        outcome = await _match(searcher, 'grep -rliZ "cache" . | xargs -0 grep -Hci "proxy"')

        assert outcome.capped is True
        assert {q for c in searcher.calls if c["virtual_record_ids"] is None for q in c["queries"]} == {"cache", "proxy"}

    @pytest.mark.asyncio
    async def test_every_stage_cut_at_its_limit_is_reported(self) -> None:
        searcher = InvertedIndexSearcher(
            [(f"vr-{i}", "cache tier") for i in range(INDEX_CANDIDATE_CHUNKS + 1)]
        )

        outcome = await _match(searcher, 'grep -rci "cache" .')

        assert outcome.capped is True

    @pytest.mark.asyncio
    async def test_a_large_store_is_searched_by_lookup_not_by_size(self) -> None:
        chunks = [(f"vr-{i}", f"routine status update number {i}") for i in range(20_000)]
        chunks.append(("vr-needle", "rollback of ERR_CONN_RESET in the gateway"))
        searcher = InvertedIndexSearcher(chunks)

        outcome = await _match(searcher, 'grep -rci "ERR_CONN_RESET" .')

        assert [h["virtual_record_id"] for h in outcome.hits[0]] == ["vr-needle"]
        assert searcher.calls == [{"queries": ["err_conn_reset"], "limit": INDEX_CANDIDATE_CHUNKS, "virtual_record_ids": None}]

    @pytest.mark.asyncio
    async def test_more_matching_records_than_are_listed_is_reported(self) -> None:
        searcher = InvertedIndexSearcher(
            [(f"vr-{i}", f"zebrafinch sighting {i}") for i in range(MAX_RECORDS_PER_COMMAND + 1)]
        )

        outcome = await _match(searcher, 'grep -rci "zebrafinch" .')

        assert len(outcome.hits[0]) == MAX_RECORDS_PER_COMMAND
        assert outcome.truncated is True
        assert outcome.capped is False

    @pytest.mark.asyncio
    async def test_a_slow_check_leaves_out_one_record_and_keeps_the_rest(self) -> None:
        from app.utils import exact_text_index

        searcher = InvertedIndexSearcher([("vr-fast", "p99 latency here"), ("vr-slow", "p99 latency slow")])
        real = exact_text_index._stage_matches

        def _check(stage, text: str) -> bool | None:
            return None if "slow" in text else real(stage, text)

        with patch.object(exact_text_index, "_stage_matches", _check):
            outcome = await _match(searcher, 'grep -rci "p99 latency" .')

        assert [h["virtual_record_id"] for h in outcome.hits[0]] == ["vr-fast"]
        assert outcome.unverified == [True]

    @pytest.mark.asyncio
    async def test_a_later_lookup_that_fails_keeps_what_the_first_found(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "zebrafinch report"), ("vr-1", "cache tier")])
        lookup = searcher.search_lexical
        calls = 0

        async def _second_fails(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("store hiccup")
            return await lookup(*args, **kwargs)

        searcher.search_lexical = _second_fails
        single = await _match(searcher, 'grep -rci "zebrafinch" .')
        calls = 0
        anded = await _match(searcher, 'grep -rliZ "zebrafinch" . | xargs -0 grep -Hci "cache"')

        assert [h["virtual_record_id"] for h in single.hits[0]] == ["vr-1"]
        assert anded.lookup_failed is True

    @pytest.mark.asyncio
    async def test_a_match_the_scorer_gives_up_on_is_still_listed(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "p99 latency")])

        with patch("app.utils.exact_text_index.score_text", return_value=(0, 0, "")):
            outcome = await _match(searcher, 'grep -rci "p99 latency" .')

        assert [h["virtual_record_id"] for h in outcome.hits[0]] == ["vr-1"]

    @pytest.mark.asyncio
    async def test_collections_the_index_could_not_search_are_reported(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "p99 latency")])
        lookup = searcher.search_lexical

        async def _partly(*args: object, **kwargs: object) -> object:
            result = await lookup(*args, **kwargs)
            result.failed_collections, result.searched_collections = 1, 3
            return result

        searcher.search_lexical = _partly
        outcome = await _match(searcher, 'grep -rci "p99" .')

        assert (outcome.failed_collections, outcome.searched_collections) == (1, 3)

    @pytest.mark.asyncio
    async def test_only_records_the_searcher_admitted_come_back(self) -> None:
        searcher = InvertedIndexSearcher(
            [("vr-mine", "zebrafinch in my space"), ("vr-other-org", "zebrafinch in another org")],
            readable={"u1": {"vr-mine"}},
        )

        outcome = await _match(searcher, 'grep -rci "zebrafinch" .')

        assert [h["virtual_record_id"] for h in outcome.hits[0]] == ["vr-mine"]
