"""Exact-text matching through the vector store's lexical index.

The grep layer reads every stored record file per search, so in a fixed time
budget it covers a shrinking share of a growing store. Here a grep command is
turned into lexical lookups instead: each stage's whole words go to the
store's keyword index (``search_lexical``), and the stage's exact patterns are
then checked on the chunks that come back. The cost follows how many chunks
hold the words, not how many the store holds.

Semantics against grep: a candidate must hold one of a pattern's whole words
(or, through the index's stemming, an inflection of one); the exact pattern
is then verified, so a hit always matches. A pattern with no whole word to
look up (a bare regex, a stem such as ``subscri``, an IP address) cannot be
looked up and is left to the bounded file scan.
"""

import asyncio
import logging
import re
import re._parser as sre_parse
from dataclasses import dataclass
from typing import Any, Protocol

import regex

from app.agents.actions.storage_search.storage_search import (
    _compile_alternatives,
    _split_pipeline_stages,
    grep_stage,
    score_text,
)
from app.modules.retrieval.lexical_search import LexicalSearchResult

# Chunks asked of the index per stage: the most one vector query leg may take
# (retrieval_service._OVERFETCH_ABSOLUTE_CAP). A stage whose words fill it is
# reported as capped, never as complete.
INDEX_CANDIDATE_CHUNKS = 300
# Records that matched some stages of a command and are checked for the rest.
_FOLLOW_UP_RECORDS = 300
_MAX_RECORDS_PER_COMMAND = 50

# Stop words the index drops on at least one backend (the Qdrant/bm25 model's
# English list, which OpenSearch's English analyser shares, and RediSearch's
# defaults). A pattern whose only words are these has nothing to look up.
STOP_WORDS = frozenset("""
a about above after again against ain all am an and any are aren as at be because been before being below
between both but by can couldn d did didn do does doesn doing don down during each few for from further had
hadn has hasn have haven having he her here hers herself him himself his how i if in into is isn it its
itself just ll m ma me mightn more most mustn my myself needn no nor not now o of off on once only or other
our ours ourselves out over own re s same shan she should shouldn so some such t than that the their theirs
them themselves then there these they this those through to too under until up ve very was wasn we were
weren what when where which while who whom why will with won wouldn y you your yours yourself yourselves
""".split())
_WORD_RE = re.compile(r"\w+")


class LexicalSearcher(Protocol):
    async def lexical_search_available(self) -> bool: ...

    async def search_lexical(
        self,
        queries: list[str],
        user_id: str,
        org_id: str,
        filter_groups: dict[str, Any] | None = None,
        limit: int = INDEX_CANDIDATE_CHUNKS,
        virtual_record_ids: list[str] | None = None,
    ) -> LexicalSearchResult: ...


@dataclass(frozen=True)
class IndexStage:
    """One grep stage: the words to look up, and the alternatives a record
    must match one of."""

    query: str
    regexes: tuple[regex.Pattern[str], ...]


@dataclass(frozen=True)
class IndexPlan:
    command: str
    stages: tuple[IndexStage, ...]

    @property
    def regexes(self) -> list[regex.Pattern[str]]:
        seen: dict[str, regex.Pattern[str]] = {}
        for stage in self.stages:
            for rx in stage.regexes:
                seen.setdefault(rx.pattern.lower(), rx)
        return list(seen.values())


def _required_literals(parsed: Any) -> list[str]:  # noqa: ANN401 - sre_parse.SubPattern
    """Literal runs every match of *parsed* must contain."""
    runs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            runs.append("".join(current))
            current.clear()

    for op, av in parsed:
        if op is sre_parse.LITERAL:
            current.append(chr(av))
            continue
        flush()
        if op is sre_parse.SUBPATTERN:
            runs.extend(_required_literals(av[-1]))
        elif op in (sre_parse.MAX_REPEAT, sre_parse.MIN_REPEAT, sre_parse.POSSESSIVE_REPEAT):
            low, _high, sub = av
            if low >= 1:
                runs.extend(_required_literals(sub))
        elif op is sre_parse.ATOMIC_GROUP:
            runs.extend(_required_literals(av))
        # Anything else (a branch, a class, ".", an anchor) breaks the run and
        # requires no particular literal.
    flush()
    return runs


def anchor_words(pattern: str) -> list[str]:
    """Whole words a match of *pattern* must contain, lower-cased, stop words
    and single characters left out. Empty when there are none to look up."""
    try:
        parsed = sre_parse.parse(pattern)
    except (re.error, OverflowError, RecursionError, ValueError):
        return []
    words: list[str] = []
    for run in _required_literals(parsed):
        for word in _WORD_RE.findall(run.lower()):
            if len(word) >= 2 and word not in STOP_WORDS and word not in words:
                words.append(word)
    return words


def plan_command(command: str) -> IndexPlan | None:
    """The lookups that answer *command*, or None when some stage cannot be
    looked up (no whole word, an inverted or whole-line match, a pattern the
    verifier cannot compile exactly) and the command must be scanned."""
    stages: list[IndexStage] = []
    for stage_text in _split_pipeline_stages(command):
        stage = grep_stage(stage_text)
        if stage is None:
            # e.g. a trailing `head`; it selects lines, it does not match.
            continue
        if stage.inverted or stage.line or not stage.patterns:
            return None
        regexes: list[regex.Pattern[str]] = []
        words: list[str] = []
        for pattern in stage.patterns:
            try:
                compiled = _compile_alternatives(
                    pattern, fixed=stage.fixed, extended=stage.extended,
                    ignore_case=stage.ignore_case, strict=True,
                )
            except ValueError:
                return None
            for alternative in compiled:
                alternative_words = anchor_words(alternative.pattern)
                if not alternative_words:
                    return None
                words.extend(w for w in alternative_words if w not in words)
                regexes.append(
                    regex.compile(rf"\b(?:{alternative.pattern})\b", alternative.flags)
                    if stage.word else alternative
                )
        if not regexes:
            return None
        stages.append(IndexStage(query=" ".join(words), regexes=tuple(regexes)))
    if not stages:
        return None
    return IndexPlan(command=command, stages=tuple(stages))


@dataclass
class IndexMatchOutcome:
    """Per plan, its ranked hits, plus whether any lookup was cut at a limit."""

    hits: list[list[dict[str, str]]]
    capped: bool


def _stage_matches(stage: IndexStage, text: str) -> bool:
    try:
        return any(rx.search(text, timeout=0.25) for rx in stage.regexes)
    except TimeoutError:
        return False


def _collect(texts: dict[str, list[str]], result: LexicalSearchResult) -> None:
    for batch in result.hits:
        for hit in batch:
            chunks = texts.setdefault(hit.virtual_record_id, [])
            if hit.text and hit.text not in chunks:
                chunks.append(hit.text)


def _stages_met(plan: IndexPlan, chunks: list[str]) -> list[bool]:
    return [any(_stage_matches(stage, chunk) for chunk in chunks) for stage in plan.stages]


def _rank(plan: IndexPlan, texts: dict[str, list[str]]) -> list[dict[str, str]]:
    """Records meeting every stage, as find_records shapes a hit, best first."""
    ranked: list[tuple[int, int, str, str]] = []
    for vrid, chunks in texts.items():
        if not all(_stages_met(plan, chunks)):
            continue
        distinct, total, snippet = score_text("\n".join(chunks), plan.regexes)
        if distinct:
            ranked.append((distinct, total, vrid, snippet))
    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
    hits: list[dict[str, str]] = []
    for distinct, total, vrid, snippet in ranked[:_MAX_RECORDS_PER_COMMAND]:
        hit = {"virtual_record_id": vrid, "matched_terms": str(distinct), "match_count": str(total)}
        if snippet:
            hit["match_preview"] = snippet
        hits.append(hit)
    return hits


def _partial_records(
    plans: list[IndexPlan], texts: dict[str, list[str]], capped_queries: set[str],
) -> list[tuple[str, set[str]]]:
    """Records that met some stages of a plan and may still meet the rest,
    with the queries of the stages they lack; those meeting most stages first.

    A record lacking a stage the index returned in full cannot meet it, so it
    is not looked up again.
    """
    missing: dict[str, set[str]] = {}
    met_count: dict[str, int] = {}
    for plan in plans:
        if len(plan.stages) < 2:
            continue
        for vrid, chunks in texts.items():
            met = _stages_met(plan, chunks)
            if not any(met) or all(met):
                continue
            lacking = {stage.query for stage, ok in zip(plan.stages, met) if not ok}
            if not lacking <= capped_queries:
                continue
            missing.setdefault(vrid, set()).update(lacking)
            met_count[vrid] = max(met_count.get(vrid, 0), sum(met))
    return sorted(missing.items(), key=lambda item: (-met_count[item[0]], item[0]))


async def run_index_pattern_match(
    plans: list[IndexPlan],
    searcher: LexicalSearcher,
    *,
    user_id: str,
    org_id: str,
    filters: dict[str, Any] | None,
    logger_instance: logging.Logger,
) -> IndexMatchOutcome:
    """Look every plan's stages up in the lexical index and verify them.

    One batched lookup for every stage of every plan. A record whose words
    sit in different chunks, one of them past a stage's limit, is then looked
    up once more within that record, so an AND across stages is decided on
    the record, not on one chunk.
    """
    queries = list(dict.fromkeys(stage.query for plan in plans for stage in plan.stages))
    result = await searcher.search_lexical(
        queries, user_id, org_id, filter_groups=filters, limit=INDEX_CANDIDATE_CHUNKS,
    )
    capped_queries = {q for q, cut in zip(queries, result.capped) if cut}
    # A plan's matches all meet each of its stages, so one stage the index
    # returned in full already names every candidate; only when every stage
    # was cut can a match lie outside all of them.
    capped = any(all(stage.query in capped_queries for stage in plan.stages) for plan in plans)
    texts: dict[str, list[str]] = {}
    _collect(texts, result)

    missing = await asyncio.to_thread(_partial_records, plans, texts, capped_queries)
    if missing:
        follow_up = missing[:_FOLLOW_UP_RECORDS]
        follow_queries = sorted({q for _vrid, lacking in follow_up for q in lacking})
        follow = await searcher.search_lexical(
            follow_queries, user_id, org_id, filter_groups=filters,
            limit=INDEX_CANDIDATE_CHUNKS, virtual_record_ids=[vrid for vrid, _lacking in follow_up],
        )
        capped = capped or len(missing) > len(follow_up) or any(follow.capped)
        _collect(texts, follow)

    hits = await asyncio.to_thread(lambda: [_rank(plan, texts) for plan in plans])
    logger_instance.info(
        "pattern_match index: %d command(s), %d lookup(s), %d candidate record(s), hits=%s capped=%s",
        len(plans), len(queries), len(texts), [len(h) for h in hits], capped,
    )
    return IndexMatchOutcome(hits=hits, capped=capped)
