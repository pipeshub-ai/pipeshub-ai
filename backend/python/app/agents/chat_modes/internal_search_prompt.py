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
# Multi-part answers left a part's own record unread, or answered from a record
# that failed a condition the question set. An exit that asked the model to name
# the unmet condition made it dispute facts the question states in other words,
# so an unmet condition is named only when the answer rests on another record.
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
    "has not been read. When the question has several parts, the records you read must cover "
    "each part: if a listed record is specifically about a part none of them covers, read it "
    "too. A record read for one part supplies that part only: do not turn its optional, "
    "recommended or condition-specific items into requirements of the whole answer. Before "
    "answering from a record, check that it meets every condition the question sets (scope, "
    "time window, kind of change, measured effect); a record that states a condition in other "
    "words meets it. If one does not hold, search again in different words, using the "
    "question's distinguishing terms (the customer, product or kind of document it names), "
    "before answering; prefer a record you read that meets every condition over one that does "
    "not, and lead with it. Take what the question states as given: do not dispute or hedge a "
    "fact it states, and do not add that a record words it differently. Mention an unmet "
    "condition only when you answer from a record other than the one the question describes, "
    "in one sentence after the answer."
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

# Answers opened with what the assistant could not do or with "not delivered yet",
# and disputed a question's paraphrase as a false premise, though the records held
# what was asked.
_COMPOSE = (
    "Compose the answer so it stands on its own for someone who has not read the question. "
    "Its first sentence gives the direct answer together with what it is about — the "
    "customer, incident, system, meeting, document or time window asked about, in the "
    "question's words, with the source's name in parentheses where it differs — so \"In the "
    "March 3 capacity incident, the SLA was breached for about 45 minutes\", not \"About 45 "
    "minutes\". Correct the question's premise only where that changes the answer, never "
    "silently. A paraphrase is not a false premise: when the question describes a record's "
    "value in its own words (\"the launch budget\" for a record's \"go-live spend cap\"), "
    "answer with that value and give the record's term, without disputing the wording or "
    "adding that the record does not use the question's term. Then what the sources attach "
    "to it — who said or decided it, in which "
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
    "states rules that no source holds. When the sources describe what was asked for — its "
    "contents, plan, terms or requirements — answer with that substance even if it is still "
    "requested, pending or in progress, and give its status (pending, ETA, delivered) in a "
    "sentence after it, never as the opening or as a reason the question cannot be answered; "
    "do not open with what you yourself cannot do, produce or send. Call a fix, decision or "
    "exception open, unconfirmed or unapproved only when a source says so and nothing else you "
    "read settles it; one the records mark done, merged or approved is done."
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

# Supersession, then shipped over proposed, then scope, then corroboration, then
# recency: "authoritative or most recent" let a formal page beat the draft that
# explicitly replaced it, and a proposal's names beat the ones that shipped. Only
# between values for one item and scope: a settled conflict otherwise made one
# tier's rule the answer for a case no source placed in that tier.
_RESOLVE_CONFLICTS = (
    "When sources disagree, prefer in order: a record saying it updates, corrects or "
    "replaces the other (updated, final, vN, deprecated, change log); for what a system does "
    "or shows its users, a shipped change (a merged pull request, a released config or "
    "changelog) over a proposal, draft or plan for it; a narrower record "
    "applying a broader rule to the exact scope asked (an ADR or exception under a "
    "standard); the value most independent sources state; the most recent. Title or "
    "formality never decides. Name each other version as a labelled difference: what it "
    "said, where, and why it was not used. Once this order settles it, give the preferred "
    "value as the answer, not as an open question. Apply the order only to sources stating a "
    "value for the same item and scope: a rule that applies only under a condition (a risk "
    "tier, plan, region or size) stays conditional: state it with its condition, and do not "
    "make it the answer for a case the sources do not place under that condition."
)

# An agent read a thread in full and still took the indexer's summary's reading
# of an ambiguous reply ("tried that") over the thread's own messages.
_SOURCE_TEXT_DECIDES = (
    "A record's \"Summary:\" line, in its metadata or in a candidate list, is written by a "
    "model when the record is indexed, not by the record's authors, and can merge or "
    "misattribute details: use it to choose what to read, never to settle a fact. When the "
    "record's own text is ambiguous or says something else, the text decides; a reply such as "
    "\"tried that\" or \"agreed\" covers everything the message it answers proposed unless "
    "it narrows it."
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
        paragraphs.append(_SOURCE_TEXT_DECIDES)
        paragraphs.append(_SECOND_VALUE)
    elif thorough:
        paragraphs.append(_SEEK_OTHER_VERSIONS)
    return "\n\n".join(paragraphs)
