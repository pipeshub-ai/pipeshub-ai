"""Prompt for the pairwise merge decision (Tier 2 of the resolver)."""

from __future__ import annotations

import hashlib
import json
import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.entity_resolution.models import ExtractedName, WinnerCandidate

_SUMMARY_MAX_CHARS = 1200
# Spellings shown per offered match; a node keeps up to MAX_TAXONOMY_ALIASES,
# and the prompt pays for every alias of every candidate of every item.
PROMPT_ALIASES_PER_MATCH = 10
_HANDLE_HEX = 8

_PROMPT = """# Task
You maintain a knowledge-graph taxonomy for one organisation. A document was just classified and produced the names below. For each name decide whether it denotes the SAME concept as one of the existing entities offered next to it (its nearest matches, listed in no particular order), or the same concept as another name in this list, or a genuinely new concept.

# Rules
- Merge only when the two names denote the same concept. Casing, spacing, punctuation, plural versus singular, word order, an acronym and its expansion, a typo, or a trailing qualifier that adds nothing ("session", "process", "overview") are the same concept.
- Related is not the same. "Unit testing" and "integration testing" are different concepts. A narrower topic is not the same as its broader topic.
- Never merge across kinds: a topic never matches a category, and a subcategory only matches a subcategory of the same level. Every offered match already has the right kind, so decide on meaning only.
- At most one offered match can be the same concept; if none is, do not pick the closest one.
- If a name has no offered match and no sibling in this list it is new.
- When a name is new you may return a cleaned display form in canonical_name: fix casing and punctuation, expand nothing, add nothing. Leave it empty to keep the name as written.
- Use the document summary only as context for what the names mean.
- Return exactly one decision per item, using the item's index. target must be the id of one of that item's offered matches, copied exactly. same_as_item must be the index of another item of the same kind.

# Document summary
{summary}

# Items
{items}

# Output
Return a JSON object with a "decisions" array. Each decision has: i (int), same (bool), target (string, one offered id or ""), same_as_item (int, another item index or -1), canonical_name (string or "").
"""


def build_prompt(
    summary: str | None,
    items: list[ExtractedName],
    candidates: dict[int, tuple[WinnerCandidate, ...]],
) -> str:
    """Render the merge prompt for ``items`` and their offered candidates."""
    handles = candidate_handles(items, candidates)
    rendered: list[dict[str, Any]] = []
    for item in items:
        entry: dict[str, Any] = {
            "i": item.index,
            "name": item.display,
            "kind": _kind_label(item),
        }
        entry["matches"] = [
            {"id": handles[c.entity_id], "name": c.name, "aliases": list(c.aliases[:PROMPT_ALIASES_PER_MATCH])}
            for c in _shuffled(item, candidates.get(item.index) or ())
        ]
        rendered.append(entry)

    text = (summary or "").strip()
    if len(text) > _SUMMARY_MAX_CHARS:
        text = text[:_SUMMARY_MAX_CHARS].rstrip() + " ..."
    if not text:
        text = "(no summary available)"

    # One item per line: the indented form spent a third of the items' tokens on whitespace.
    items_json = "[\n" + ",\n".join(
        json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in rendered
    ) + "\n]"
    # str.replace, not str.format: names and summaries can contain braces.
    return _PROMPT.replace("{summary}", text).replace("{items}", items_json)


def candidate_handles(
    items: list[ExtractedName],
    candidates: dict[int, tuple[WinnerCandidate, ...]],
) -> dict[str, str]:
    """The short id the prompt shows for each offered node, by node id.

    A node id is a UUID, about 35 tokens the model reads for every match and
    copies into every merge. The prompt shows its first 8 hex digits, longer
    only where two offered ids share them. Opaque ids, not "m1", "m2": with
    numbered handles the model merged a fifth less often than with UUIDs.
    """
    ids = sorted({c.entity_id for item in items for c in candidates.get(item.index) or ()})
    width = _HANDLE_HEX
    while True:
        handles = {node_id: _hex(node_id)[:width] for node_id in ids}
        if len(set(handles.values())) == len(handles) or width >= 64:
            return handles
        width *= 2


def _hex(node_id: str) -> str:
    """The id's hex digits, or a digest for an id that is not a UUID."""
    digits = node_id.replace("-", "").lower()
    if digits and all(ch in "0123456789abcdef" for ch in digits):
        return digits
    return hashlib.sha256(node_id.encode()).hexdigest()


def _shuffled(item: ExtractedName, offered: tuple[WinnerCandidate, ...]) -> list[WinnerCandidate]:
    """``offered`` in an order that does not follow the search rank, since a
    model favours the first option it sees (arXiv 2405.16884), and that is
    the same for the same name and candidates so a run can be reproduced."""
    seed = "|".join([item.normalized, *(c.entity_id for c in offered)])
    order = list(offered)
    random.Random(hashlib.sha256(seed.encode()).hexdigest()).shuffle(order)
    return order


def _kind_label(item: ExtractedName) -> str:
    if item.kind.level:
        return f"subcategory level {item.kind.level}"
    return item.kind.entity_type.value


__all__ = ["build_prompt", "candidate_handles"]
