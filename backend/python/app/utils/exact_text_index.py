"""Exact-text matching through the vector store's lexical index.

The grep layer reads every stored record file per search, so in a fixed time
budget it covers a shrinking share of a growing store. Here a grep command is
turned into lexical lookups instead: each stage's whole words go to the
store's keyword index (``search_lexical``), and the stage's exact patterns are
then checked on the chunks that come back. The cost follows how many chunks
hold the words, not how many the store holds.

Semantics against grep: a candidate must hold one of a pattern's words as a
whole word (or, through the index's stemming, an inflection of one); the
exact pattern is then verified, so a hit always matches. Unlike grep's
substring match, a word inside a longer token (a cut-off stem such as
``subscri``) is not found; the tool result says so when a lookup finds
nothing. A pattern with no whole word to look up (a bare regex, ``colou?r``,
an IP address) or one the verifier would only approximate (``.*``) is left
to the bounded file scan.
"""

import asyncio
import logging
import re
import re._parser as sre_parse
from dataclasses import dataclass, field
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
MAX_RECORDS_PER_COMMAND = 50
_VERIFY_TIMEOUT_SECS = 0.25

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

    async def lexical_store_chunks(self, org_id: str, user_id: str) -> int | None: ...

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


@dataclass(frozen=True)
class _Run:
    """A literal run every match contains. An open edge touches a regex
    construct (a quantifier, a class, ``.``, a group), so the word at that edge
    may be part of a longer token in the text."""

    text: str
    left_open: bool
    right_open: bool


def _required_literals(parsed: Any, *, open_edges: bool = False) -> list[_Run]:  # noqa: ANN401 - sre_parse.SubPattern
    """Literal runs every match of *parsed* must contain. The pattern's own
    start and end are closed edges; a group's are open (*open_edges*)."""
    runs: list[_Run] = []
    current: list[str] = []
    left_open = open_edges

    def flush(*, right_open: bool) -> None:
        if current:
            runs.append(_Run("".join(current), left_open, right_open))
            current.clear()

    for op, av in parsed:
        if op is sre_parse.LITERAL:
            current.append(chr(av))
            continue
        # A word boundary keeps the neighbouring word whole.
        boundary = op is sre_parse.AT and av is sre_parse.AT_BOUNDARY
        flush(right_open=not boundary)
        left_open = not boundary
        if op is sre_parse.SUBPATTERN:
            runs.extend(_required_literals(av[-1], open_edges=True))
        elif op in (sre_parse.MAX_REPEAT, sre_parse.MIN_REPEAT, sre_parse.POSSESSIVE_REPEAT):
            low, _high, sub = av
            if low >= 1:
                runs.extend(_required_literals(sub, open_edges=True))
        elif op is sre_parse.ATOMIC_GROUP:
            runs.extend(_required_literals(av, open_edges=True))
        # Anything else (a branch, a class, ".", an anchor) breaks the run and
        # requires no particular literal.
    flush(right_open=open_edges)
    return runs


def anchor_words(pattern: str) -> list[str]:
    """Whole words a match of *pattern* must contain, lower-cased, stop words
    and single characters left out. Empty when there are none to look up.

    A word glued to a regex construct (``colo`` in ``colou?r``, ``ms`` in
    ``[0-9]+ms``) is not a whole word of the text, so it is not one to look up.
    """
    try:
        parsed = sre_parse.parse(pattern)
    except (re.error, OverflowError, RecursionError, ValueError):
        return []
    words: list[str] = []
    for run in _required_literals(parsed):
        text = run.text.lower()
        for m in _WORD_RE.finditer(text):
            if (m.start() == 0 and run.left_open) or (m.end() == len(text) and run.right_open):
                continue
            word = m.group()
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
    """Per plan, its ranked hits; whether a lookup was cut at its limit
    (*capped*); and whether a plan matched more records than it lists
    (*truncated*)."""

    hits: list[list[dict[str, str]]]
    capped: bool
    truncated: bool = False
    # The most collections any lookup could not search, of how many it asked.
    failed_collections: int = 0
    searched_collections: int = 0
    # Per plan: some candidate took too long to check against its pattern.
    unverified: list[bool] = field(default_factory=list)
    # A lookup after the first failed; what the others found is kept.
    lookup_failed: bool = False


def _stage_matches(stage: IndexStage, text: str) -> bool | None:
    """Whether *text* matches the stage; None when the check ran out of time,
    which is neither a hit nor a miss."""
    try:
        return any(rx.search(text, timeout=_VERIFY_TIMEOUT_SECS) for rx in stage.regexes)
    except TimeoutError:
        return None


def _collect(texts: dict[str, list[str]], result: LexicalSearchResult) -> None:
    for batch in result.hits:
        for hit in batch:
            chunks = texts.setdefault(hit.virtual_record_id, [])
            if hit.text and hit.text not in chunks:
                chunks.append(hit.text)


def _stages_met(plan: IndexPlan, chunks: list[str]) -> list[bool] | None:
    """Per stage, whether some chunk matches it; None when a check that could
    decide the record ran out of time."""
    met: list[bool] = []
    for stage in plan.stages:
        outcomes = [_stage_matches(stage, chunk) for chunk in chunks]
        if any(outcomes):
            met.append(True)
        elif None in outcomes:
            return None
        else:
            met.append(False)
    return met


def _rank(plan: IndexPlan, texts: dict[str, list[str]]) -> tuple[list[dict[str, str]], bool, bool]:
    """Records meeting every stage, as find_records shapes a hit, best first;
    whether more met them than are listed; and whether some record could not
    be checked in time (it is left out, the others are kept)."""
    ranked: list[tuple[int, int, str, str]] = []
    unverified = False
    for vrid, chunks in texts.items():
        met = _stages_met(plan, chunks)
        if met is None:
            unverified = True
            continue
        if not all(met):
            continue
        distinct, total, snippet = score_text("\n".join(chunks), plan.regexes)
        # Scoring gives up on a slow regex with 0; the stages were verified,
        # so the record still matches, it is only ranked last.
        ranked.append((distinct, total, vrid, snippet))
    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
    hits: list[dict[str, str]] = []
    for distinct, total, vrid, snippet in ranked[:MAX_RECORDS_PER_COMMAND]:
        hit = {"virtual_record_id": vrid, "matched_terms": str(max(distinct, 1)), "match_count": str(max(total, 1))}
        if snippet:
            hit["match_preview"] = snippet
        hits.append(hit)
    return hits, len(ranked) > MAX_RECORDS_PER_COMMAND, unverified


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
            # An unchecked record is reported by _rank, not looked up again.
            if met is None or not any(met) or all(met):
                continue
            lacking = {stage.query for stage, ok in zip(plan.stages, met) if not ok}
            if not lacking <= capped_queries:
                continue
            missing.setdefault(vrid, set()).update(lacking)
            met_count[vrid] = max(met_count.get(vrid, 0), sum(met))
    return sorted(missing.items(), key=lambda item: (-met_count[item[0]], item[0]))


def _word_specificity(word: str) -> int:
    """A guess at how rare *word* is: longer words, and identifiers with
    digits or underscores, tend to sit in fewer chunks."""
    return len(word) + (8 if any(c.isdigit() or c == "_" for c in word) else 0)


def _lead_stage(plan: IndexPlan) -> IndexStage:
    """The stage to look up first: the one whose least specific alternative
    is most specific, since any alternative may bring in many chunks."""
    def score(stage: IndexStage) -> int:
        words = stage.query.split()
        return min((_word_specificity(w) for w in words), default=0)

    return max(plan.stages, key=score)


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

    Each plan's most specific stage is looked up first, in one batch. When
    the index returns it in full, its records are every candidate the plan
    can have, so the plan's other stages are looked up only within them: a
    stage of common words then costs its matches in those records, not in
    the store. Otherwise the other stages are looked up in full. A record
    that met some stages, the rest cut at a limit, is looked up once more
    within itself, so an AND across stages is decided on the record, not on
    one chunk.
    """
    lead = {id(plan): _lead_stage(plan) for plan in plans}
    lead_queries = list(dict.fromkeys(stage.query for stage in lead.values()))
    first = await searcher.search_lexical(
        lead_queries, user_id, org_id, filter_groups=filters, limit=INDEX_CANDIDATE_CHUNKS,
    )
    capped_queries = {q for q, cut in zip(lead_queries, first.capped) if cut}
    texts: dict[str, list[str]] = {}
    lookups = [first]
    _collect(texts, first)

    lead_records = {
        query: {hit.virtual_record_id for hit in batch} for query, batch in zip(lead_queries, first.hits)
    }
    within: set[str] = set()
    within_records: set[str] = set()
    everywhere: set[str] = set()
    for plan in plans:
        rest = [stage.query for stage in plan.stages if stage.query != lead[id(plan)].query]
        records = lead_records[lead[id(plan)].query]
        if lead[id(plan)].query in capped_queries or len(records) > _FOLLOW_UP_RECORDS:
            everywhere.update(rest)
        elif records:
            within.update(rest)
            within_records |= records
    within -= everywhere | set(lead_queries)
    everywhere -= set(lead_queries)
    rounds = []
    if everywhere:
        rounds.append((sorted(everywhere), None))
    if within:
        rounds.append((sorted(within), sorted(within_records)))
    results = await asyncio.gather(*(
        searcher.search_lexical(
            queries, user_id, org_id, filter_groups=filters,
            limit=INDEX_CANDIDATE_CHUNKS, virtual_record_ids=scope,
        )
        for queries, scope in rounds
    ), return_exceptions=True)
    lookup_failed = False
    for (queries, _scope), result in zip(rounds, results):
        if isinstance(result, BaseException):
            # What the first lookup found stays; these stages count as cut.
            logger_instance.warning("pattern_match index: a lookup failed: %s", result)
            lookup_failed = True
            capped_queries.update(queries)
            continue
        capped_queries.update(q for q, cut in zip(queries, result.capped) if cut)
        _collect(texts, result)
        lookups.append(result)

    # A plan's matches all meet each of its stages, so one stage the index
    # returned in full already names every candidate; only when every stage
    # was cut can a match lie outside all of them.
    capped = any(all(stage.query in capped_queries for stage in plan.stages) for plan in plans)

    missing = await asyncio.to_thread(_partial_records, plans, texts, capped_queries)
    if missing:
        follow_up = missing[:_FOLLOW_UP_RECORDS]
        follow_queries = sorted({q for _vrid, lacking in follow_up for q in lacking})
        try:
            follow = await searcher.search_lexical(
                follow_queries, user_id, org_id, filter_groups=filters,
                limit=INDEX_CANDIDATE_CHUNKS, virtual_record_ids=[vrid for vrid, _lacking in follow_up],
            )
        except Exception as exc:
            logger_instance.warning("pattern_match index: the follow-up lookup failed: %s", exc)
            lookup_failed = True
        else:
            capped = capped or len(missing) > len(follow_up) or any(follow.capped)
            _collect(texts, follow)
            lookups.append(follow)

    ranked = await asyncio.to_thread(lambda: [_rank(plan, texts) for plan in plans])
    hits = [plan_hits for plan_hits, _more, _unverified in ranked]
    truncated = any(more for _hits, more, _unverified in ranked)
    logger_instance.info(
        "pattern_match index: %d command(s), %d lookup round(s), %d candidate record(s), "
        "hits=%s capped=%s truncated=%s",
        len(plans), 1 + len(rounds) + bool(missing), len(texts), [len(h) for h in hits], capped, truncated,
    )
    worst = max(lookups, key=lambda r: r.failed_collections)
    return IndexMatchOutcome(
        hits=hits, capped=capped, truncated=truncated,
        failed_collections=worst.failed_collections, searched_collections=worst.searched_collections,
        unverified=[unchecked for _hits, _more, unchecked in ranked], lookup_failed=lookup_failed,
    )
