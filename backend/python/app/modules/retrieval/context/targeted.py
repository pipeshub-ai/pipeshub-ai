"""Find with less, then read the right part (``TARGETED_RETRIEVAL``).

Every knowledge search the assistant runs fills a 64,000-character result,
and a question usually takes about three of them, so most of what a question
costs is search passages written into the prompt. The answers still miss
details inside records the searches found: a few passages of the right record
are shown, and the detail sits in a part that was not.

With the switch on, the two halves trade places:

* **Find with less.** A search result is held to ``TARGETED_SEARCH_MAX_CHARS``
  (32,000 by default), each record shows at most
  ``TARGETED_SEARCH_PASSAGES_PER_RECORD`` passages (unset: the count the
  search API keeps per record, 8 at the assistant's limit of 50), and passages the
  model already received from an earlier search in the same request give
  way to new ones. A match inside a long section (a Slack thread, a pull
  request, a list or a table) no longer brings the whole section: the
  passages that matched come first, then those nearest them.
* **Read the right part.** ``knowledgegraph__fetch_record`` takes a
  ``query``. With one, each record is read as its blocks most relevant to
  that query, within ``FOCUSED_READ_MAX_CHARS`` per record (16,000), instead
  of the whole record. The search result names the records that had more
  matching passages than it showed, and the prompt tells the model to read
  the part it needs before answering a specific detail.

Opt-in only, and not part of ``HIGH_ACCURACY_MODE``: it changes what the
model is shown, and its effect on answers is not measured yet. Off, nothing
here runs and every result is what it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
)
from app.modules.retrieval.context.units import (
    neighbour_anchors,
    without_orphan_neighbours,
)
from app.modules.retrieval.record_cut import (
    passages_per_record as record_cut_passages_per_record,
)
from app.utils.chat_helpers import group_child_results
from app.utils.env_utils import env_bool, env_int
from app.utils.render_budget import FETCH_MAX_CHARS

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.modules.retrieval.context.units import Unit

TARGETED_RETRIEVAL_ENV_VAR = "TARGETED_RETRIEVAL"
SEARCH_MAX_CHARS_ENV_VAR = "TARGETED_SEARCH_MAX_CHARS"
PASSAGES_PER_RECORD_ENV_VAR = "TARGETED_SEARCH_PASSAGES_PER_RECORD"
FOCUSED_READ_CHARS_ENV_VAR = "FOCUSED_READ_MAX_CHARS"

DEFAULT_SEARCH_MAX_CHARS = 32_000
DEFAULT_FOCUSED_READ_CHARS = 16_000

# Below this a search result holds a few records' headers and little else.
MIN_SEARCH_MAX_CHARS = 8_000
# Smaller than this, a focused read holds a record's header and a passage.
MIN_FOCUSED_READ_CHARS = 4_000
MAX_PASSAGES_PER_RECORD = 50

FETCH_TOOL = "knowledgegraph__fetch_record"
# The assistant's search asks for 50 hits from one source (`execute_search`).
DEFAULT_HIT_LIMIT = 50


def targeted_retrieval_enabled() -> bool:
    """``TARGETED_RETRIEVAL=true`` turns the feature on; anything else leaves it off.

    Read per call but set per deployment, so the tool schema and prompt text it
    changes are the same for every request and stay in the cached prefix.
    """
    return env_bool(TARGETED_RETRIEVAL_ENV_VAR, False)


def search_max_chars() -> int:
    """Characters one search result may hold, never above the shared tool-result cap."""
    value = env_int(
        SEARCH_MAX_CHARS_ENV_VAR, DEFAULT_SEARCH_MAX_CHARS,
        lo=MIN_SEARCH_MAX_CHARS, hi=DEFAULT_MAX_RESULT_CHARS,
    )
    return DEFAULT_SEARCH_MAX_CHARS if value is None else value


def passages_per_record(limit: int | None) -> int:
    """Passages one record may show in a result of ``limit`` hits.

    Unset, it is the count the search API keeps per record
    (``record_cut.passages_per_record``: 8 at a limit of 50), so one pair of
    knobs bounds a record everywhere it is searched.
    """
    value = env_int(PASSAGES_PER_RECORD_ENV_VAR, None, lo=1, hi=MAX_PASSAGES_PER_RECORD)
    if value is not None:
        return value
    return record_cut_passages_per_record(limit if limit and limit > 0 else DEFAULT_HIT_LIMIT)


def focused_read_chars() -> int:
    """Characters a focused read gives each record."""
    value = env_int(
        FOCUSED_READ_CHARS_ENV_VAR, DEFAULT_FOCUSED_READ_CHARS,
        lo=MIN_FOCUSED_READ_CHARS, hi=FETCH_MAX_CHARS,
    )
    return DEFAULT_FOCUSED_READ_CHARS if value is None else value


# Request state: every block a search of this request has shown. Kept apart
# from `final_results`, which holds one entry per section (keyed by its first
# block) and so never learns of a later search's other slice of it.
SHOWN_BLOCKS_STATE_KEY = "targeted_shown_blocks"


def rendered_block_indices(unit: Mapping[str, Any]) -> list[int]:
    """The block indices a unit puts in front of the model.

    A section, list or table renders its children only; its own
    ``block_index`` names its first child, which a trimmed section may no
    longer hold, so it does not count. A block renders itself.
    """
    children = group_child_results(dict(unit))
    if children is None:
        index = _int(unit.get("block_index"))
        return [] if index is None else [index]
    return [index for child in children if (index := _int(child.get("block_index"))) is not None]


def shown_block_keys(units: Iterable[Mapping[str, Any]] | None) -> set[tuple[str, int]]:
    """``(virtual record id, block index)`` of every block ``units`` render."""
    keys: set[tuple[str, int]] = set()
    for unit in units or ():
        if not isinstance(unit, dict):
            continue
        vrid = unit.get("virtual_record_id")
        if not vrid:
            continue
        keys.update((vrid, index) for index in rendered_block_indices(unit))
    return keys


def already_shown(state: Mapping[str, Any]) -> set[tuple[str, int]]:
    """Blocks this request has already shown: the first-turn context and
    every earlier search."""
    shown = shown_block_keys(state.get("final_results"))
    recorded = state.get(SHOWN_BLOCKS_STATE_KEY)
    if isinstance(recorded, set):
        shown |= recorded
    return shown


def remember_shown(state: dict[str, Any], units: Iterable[Mapping[str, Any]]) -> None:
    """Add the blocks of ``units``, as rendered, to what this request has shown."""
    recorded = state.get(SHOWN_BLOCKS_STATE_KEY)
    if not isinstance(recorded, set):
        recorded = state[SHOWN_BLOCKS_STATE_KEY] = set()
    recorded |= shown_block_keys(units)


@dataclass
class PassageCap:
    """What ``cap_passages_per_record`` kept, and what it left out."""

    units: list[Unit]
    omitted: dict[str, int] = field(default_factory=dict)
    """Per virtual record id: passages the search returned that are not shown."""
    repeats_dropped: int = 0
    """Passages left out because an earlier search in the request showed them."""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _score(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _child_order(unit: Mapping[str, Any], children: list[dict[str, Any]], repeat: Any) -> list[int]:  # noqa: ANN401
    """Positions of ``children`` best first: new before already shown, a
    passage the search matched before its neighbours (best hit first), then
    the nearest to a match."""
    matched = {
        index: _score(score)
        for index, score in (unit.get("hit_block_scores") or {}).items()
        if _int(index) is not None
    }

    def key(position: int) -> tuple[bool, bool, float, int, int]:
        child = children[position]
        index = _int(child.get("block_index"))
        is_match = index is not None and index in matched
        distance = (
            min(abs(index - hit) for hit in matched)
            if matched and index is not None else 0
        )
        score = matched[index] if is_match else _score(child.get("score"))
        return (repeat(index), not is_match if matched else False, -score, distance, position)

    return sorted(range(len(children)), key=key)


def cap_passages_per_record(
    ranked: list[Unit],
    per_record: int,
    *,
    already_shown: set[tuple[str, int]] | frozenset[tuple[str, int]] = frozenset(),
) -> PassageCap:
    """At most ``per_record`` passages of each record, the best ones.

    ``ranked`` is most relevant first, neighbours after the hits (as
    ``KnowledgeContextBuilder`` returns it). A passage is a block unit, or one
    child of a section, list or table unit. A record keeps its passages in rank
    order; inside a section, the children the search matched come first, best
    hit first, then those nearest a match. Passages in ``already_shown`` (an
    earlier result of this request holds them) go last, so new text fills the
    record's share first. Every record keeps at least one passage. Neighbours
    are context, not passages: they stay only beside a unit that stayed.

    A record whose kept passages were all shown before moves behind the
    others, so a result short of room drops it first.
    """
    per_record = max(per_record, 1)
    hits = [unit for unit in ranked if neighbour_anchors(unit) is None]
    neighbours = [unit for unit in ranked if neighbour_anchors(unit) is not None]

    by_record: dict[Any, list[int]] = {}
    for position, unit in enumerate(hits):
        by_record.setdefault(unit.get("virtual_record_id"), []).append(position)

    kept: dict[int, Unit] = {}
    omitted: dict[str, int] = {}
    repeats_dropped = 0
    stale_records: set[Any] = set()

    for vrid, positions in by_record.items():
        def repeat(index: int | None, _vrid: Any = vrid) -> bool:  # noqa: ANN401
            return index is not None and (_vrid, index) in already_shown

        def unit_is_repeat(unit: Unit, _vrid: Any = vrid) -> bool:  # noqa: ANN401
            indices = rendered_block_indices(unit)
            return bool(indices) and all((_vrid, index) in already_shown for index in indices)

        room = per_record
        fresh_kept = False
        any_kept = False
        lost = 0
        for position in sorted(positions, key=lambda p: (unit_is_repeat(hits[p]), p)):
            unit = hits[position]
            children = group_child_results(unit)
            if children is None or not children:
                # A block, a record summary, or a group with nothing to show.
                cost = 0 if children is not None else 1
                if room >= cost or not any_kept:
                    kept[position] = unit
                    room -= cost
                    any_kept = True
                    fresh_kept = fresh_kept or not unit_is_repeat(unit)
                elif unit_is_repeat(unit):
                    repeats_dropped += cost
                else:
                    lost += cost
                continue
            order = _child_order(unit, children, repeat)
            take = room if room > 0 else (0 if any_kept else 1)
            chosen = sorted(order[:take])
            for dropped in order[take:]:
                if repeat(_int(children[dropped].get("block_index"))):
                    repeats_dropped += 1
                else:
                    lost += 1
            if not chosen:
                continue
            content = unit.get("content")
            summary = content[0] if isinstance(content, tuple) else ""
            trimmed = unit if len(chosen) == len(children) else {
                **unit, "content": (summary, [children[i] for i in chosen]),
            }
            kept[position] = trimmed
            room -= len(chosen)
            any_kept = True
            fresh_kept = fresh_kept or any(
                not repeat(_int(children[i].get("block_index"))) for i in chosen
            )
        if lost and vrid:
            omitted[vrid] = lost
        if not fresh_kept:
            stale_records.add(vrid)

    ordered = [kept[p] for p in sorted(kept) if hits[p].get("virtual_record_id") not in stale_records]
    ordered += [kept[p] for p in sorted(kept) if hits[p].get("virtual_record_id") in stale_records]
    shown_neighbours = [
        unit for unit in neighbours
        if (unit.get("virtual_record_id"), _int(unit.get("block_index"))) not in already_shown
    ]
    units = without_orphan_neighbours([*ordered, *shown_neighbours])
    return PassageCap(units=units, omitted=omitted, repeats_dropped=repeats_dropped)


def more_passages_note(
    per_record: int, omitted_records: int, repeats_dropped: int, *, focused_reads: bool = True,
) -> str:
    """The line at the top of a result whose records were trimmed.

    Names no record, so it is the same size whatever ids it would print; the
    records themselves are listed at the end (``more_passages_list``).

    ``focused_reads`` is whether the fetch tool takes a ``query`` in this
    request; under THOROUGH_RETRIEVAL's full-read policy it does not, and the
    note asks for the record read whole instead.
    """
    how = (
        "with that record's ID and a `query` naming the detail"
        if focused_reads else "with that record's ID to read it"
    )
    lines: list[str] = []
    if omitted_records:
        lines.append(
            f"Note: each record shows at most {per_record} matching passages. "
            f"{omitted_records} record{'s' if omitted_records != 1 else ''} had more "
            "(listed at the end). When the question asks for a specific detail (a "
            "value, version, tag, name, field, setting, rule, status or date) that "
            f"the passages below do not state word for word, call `{FETCH_TOOL}` "
            f"{how} before answering."
        )
    if repeats_dropped:
        lines.append(
            f"Note: {repeats_dropped} passage{'s' if repeats_dropped != 1 else ''} you "
            "already received from an earlier search in this request "
            f"{'were' if repeats_dropped != 1 else 'was'} not repeated."
        )
    return "".join(f"{line}\n\n" for line in lines)


def more_passages_list(
    omitted: Mapping[str, int], records: Mapping[str, Any], *, focused_reads: bool = True,
) -> str:
    """The records of a result that had more matching passages than it shows.

    ``omitted`` is keyed by virtual record id; only records in ``records`` (the
    ones shown) are listed. ``Record ID:`` lines are what the record-id
    shortener rewrites, so the ids read the same as everywhere else.
    """
    rows: list[str] = []
    for vrid, count in omitted.items():
        record = records.get(vrid)
        if not isinstance(record, dict) or count <= 0:
            continue
        record_id = record.get("id") or record.get("_key") or vrid
        name = record.get("record_name") or record.get("recordName") or "(unnamed)"
        rows.append(
            f"- Record ID: {record_id} | {name} | {count} more matching "
            f"passage{'s' if count != 1 else ''} not shown"
        )
    if not rows:
        return ""
    how = (
        f"read the part you need with `{FETCH_TOOL}` and a `query`"
        if focused_reads else f"read them with `{FETCH_TOOL}`"
    )
    return (
        f"\n\nRecords with more matching passages than shown above — {how}:\n"
        + "\n".join(rows)
    )


__all__ = [
    "DEFAULT_FOCUSED_READ_CHARS",
    "DEFAULT_HIT_LIMIT",
    "DEFAULT_SEARCH_MAX_CHARS",
    "FOCUSED_READ_CHARS_ENV_VAR",
    "PASSAGES_PER_RECORD_ENV_VAR",
    "SEARCH_MAX_CHARS_ENV_VAR",
    "SHOWN_BLOCKS_STATE_KEY",
    "TARGETED_RETRIEVAL_ENV_VAR",
    "PassageCap",
    "already_shown",
    "cap_passages_per_record",
    "focused_read_chars",
    "more_passages_list",
    "more_passages_note",
    "passages_per_record",
    "remember_shown",
    "rendered_block_indices",
    "search_max_chars",
    "shown_block_keys",
    "targeted_retrieval_enabled",
]
