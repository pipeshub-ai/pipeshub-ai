"""The enterprise-search (`internal_search`) mode text.

With ANSWER_COMPOSITION_RULES and THOROUGH_RETRIEVAL both off it is the
grounding and fetch paragraphs alone, byte for byte what this mode has always
sent. Each switch adds its own paragraphs; the result depends only on the
switches and on whether the entity tools are granted, never on the question.
"""

from __future__ import annotations

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.modules.agents.record_escalation.policy import FULL_READ_SKIP

_GROUNDING = (
    "Answer ONLY from the organization's internal knowledge base / provided context and "
    "attachments. Do not use your own training knowledge to answer factual questions. If the "
    "answer is not present in the retrieved context, say so explicitly rather than guessing."
)

_FETCH_WHEN_NEEDED = (
    "The context you are given contains the most relevant BLOCKS from each document — not "
    "the full documents. For questions that can be answered by locating the right passage "
    "(a date, a name, a number, a status), the blocks are sufficient. For questions that "
    "need whole-document content — a summary, what the risks or key points are, a review, "
    "a comparison, anything covering all of something — a handful of blocks cannot support "
    "the answer. When a candidate list appears below the context blocks showing how much of "
    "each record you hold (\"you have N of M blocks\"), call "
    f"`{FETCH_RECORD_TOOL_NAME}` "
    "with those record IDs before answering."
)

# A block can hold an outdated value, half a list, or a step without its
# conditions; "blocks are sufficient for a single fact" let answers rest on one.
_READ_IN_FULL = (
    "The context you are given contains the most relevant BLOCKS from each document — not "
    "the full documents. Before answering, read in full the records your answer will rest "
    f"on: in ONE `{FETCH_RECORD_TOOL_NAME}` call, pass the IDs of the records that are "
    "about the specific thing the question asks (usually one to three; skip records "
    "that only share words with it). Do this for a single fact too: a block can hold an "
    "outdated value, part of a list, or a step without its conditions, and the rest of the "
    f"record settles which. Skip it only when {FULL_READ_SKIP} "
    "(the candidate list below the context shows \"you have N of M blocks\"). If a fetched "
    "record comes back with blocks not shown and the part you need could be among them, read "
    "on with `start_block` before answering. A record listed in results with no blocks shown "
    "has not been read."
)

_SEARCH_AGAIN = (
    "That first context comes from one search worded like the question, and documents "
    "rarely use the question's wording. Before answering, check that it states each thing "
    "the question asks for; for any part it does not, search again in the terms a document "
    "would use — the system, team, customer, ticket or incident id, product name, or a "
    "synonym for the question's phrasing — rather than answering around the gap."
)

# Only when the entity tools are granted: the mode text is not filtered by the
# grant the way Finding Information is, so it must not name a withheld tool.
# `find_records_by_entity` is left to `search_entities`' own schema: it is
# granted only once that tool has returned an entity id.
_SEARCH_ENTITIES = (
    " When the question names a specific team, customer, project, product, system or "
    "document, also look it up with `knowledgegraph__search_entities` (in a document's terms "
    "once you have seen them) and reach the records tagged with the matching entity through "
    "`knowledgegraph__search` with `entity_ids`."
)

_LIST_THE_SET = (
    " For a question about all, each, every or \"across\" something, one search returns a "
    "sample, not the set: list the records of the folder (`knowledgegraph__list_files`, "
    "`knowledgegraph__navigate`){or_entity} the question covers instead of sampling with more "
    "searches, and fetch the listed records the answer needs. Only where there is no such "
    "folder{or_entity}, keep searching with different terms until new searches stop turning "
    "up new records."
)

_COMPOSE = (
    "Compose the answer so it stands on its own for someone who has not read the question. "
    "Its first sentence gives the direct answer together with what it is about — the "
    "customer, incident, system, meeting, document or time window asked about, in the "
    "question's words, with the source's name in parentheses where it differs — so \"In the "
    "March 3 capacity incident, the SLA was breached for about 45 minutes\", not \"About 45 "
    "minutes\". Correct the question's premise only where that changes the answer, never "
    "silently. Then what the sources attach to it — who said or decided it, in which "
    "thread, ticket, meeting or document, and when, named in the prose "
    "(a citation marker alone does not say who; when a fact comes from a chat message, "
    "meeting turn or email, name the person or bot who said it); numbers, names, versions "
    "and thresholds exactly as the source states them, with their qualifiers (never round, "
    "re-bound or paraphrase a value: \"150 ms\" is not \"below 150 ms\", \"about\", \"at "
    "most\" and AND/OR conditions stay as written); and the conditions, exceptions, owners "
    "and follow-ups the source ties to them. Cover every part the question names, at the "
    "depth it needs, and stop there: no sections it did not ask for (background, related "
    "checks, next steps). For a procedure, checklist or policy, give every step or "
    "requirement of the record that defines it for the exact scope asked about (product, "
    "deployment mode, region, customer) — not a blend of several records' versions, which "
    "states rules that no source holds."
)

_SEEK_OTHER_VERSIONS = (
    "When the question asks about a discrepancy, a change over time, or what different "
    "people, teams or sources said — or once you have found one source's value for "
    "something several threads, emails or meetings discuss — search for other sources on "
    "the same subject before answering, and report each version with its source."
)

_REPORT_VERSIONS = (
    "When the question asks about a discrepancy, a change over time, or what different "
    "people, teams or sources said, report each version with its source."
)

# Supersession, then scope, then corroboration, then recency: "authoritative or
# most recent" let a formal page beat the draft that explicitly replaced it.
_RESOLVE_CONFLICTS = (
    "When sources disagree, prefer in order: a record saying it updates, corrects or "
    "replaces the other (updated, final, vN, deprecated, change log); a narrower record "
    "applying a broader rule to the exact scope asked (an ADR or exception under a "
    "standard); the value most independent sources state; the most recent. Title or "
    "formality never decides. Name each other version as a labelled difference: what it "
    "said, where, and why it was not used."
)

# A looser mention in the same record (a weekday beside a dated deadline) was
# being reported as a rival value, which reads as an unresolved contradiction;
# a different person's value in the same thread or meeting is still a version.
_SECOND_VALUE = (
    "Present a second value for what was asked only when another record, a different person "
    "in the same thread, meeting or email, or an explicit revision within one record "
    "(updated, corrected, replaced) states a different value for the same item and scope. "
    "The same source's looser or partial mention elsewhere in the record — a day or time "
    "without the date, another label in a table, a later aside — is not a rival version: "
    "answer with the value the record states precisely and leave the other mention out."
)


def internal_search_instructions(*, composition: bool, thorough: bool, entity_tools: bool) -> str:
    """The `internal_search` addendum for these switches.

    `entity_tools` is whether `knowledgegraph__search_entities` is granted to
    this chat; it only matters under `thorough`.
    """
    paragraphs = [_GROUNDING, _READ_IN_FULL if thorough else _FETCH_WHEN_NEEDED]
    if thorough:
        or_entity = " or entity" if entity_tools else ""
        paragraphs.append(
            _SEARCH_AGAIN
            + (_SEARCH_ENTITIES if entity_tools else "")
            + _LIST_THE_SET.format(or_entity=or_entity)
        )
    if composition:
        versions = _SEEK_OTHER_VERSIONS if thorough else _REPORT_VERSIONS
        paragraphs.append(f"{_COMPOSE} {versions} {_RESOLVE_CONFLICTS}")
        paragraphs.append(_SECOND_VALUE)
    elif thorough:
        paragraphs.append(_SEEK_OTHER_VERSIONS)
    return "\n\n".join(paragraphs)
