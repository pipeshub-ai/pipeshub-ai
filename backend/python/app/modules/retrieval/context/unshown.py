"""Name the records a search matched but its result leaves out (``UNSHOWN_MATCHES_LIST``).

A search result shows the records it ranks best, as many as fit. The others
it matched are left out with only a count in the header, so the model answers
from what it saw, or concludes a record does not exist. With the switch on, the
result ends with the record id and title of up to ``MAX_UNSHOWN_MATCHES`` of
them, best first, and the model can read one with the fetch tool.

Only records the retrieval service checked the user may read are named: ids
and titles come from its ``virtual_to_record_map``, never from the hits alone.

A high-accuracy feature: ``UNSHOWN_MATCHES_LIST`` decides when set, otherwise
``HIGH_ACCURACY_MODE`` does. Off, a result is what it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.context.targeted import FETCH_TOOL
from app.utils.env_utils import accuracy_feature_enabled

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

UNSHOWN_MATCHES_LIST_ENV_VAR = "UNSHOWN_MATCHES_LIST"
MAX_UNSHOWN_MATCHES = 10
# A record name can be a whole message or subject line. Cut, ten lines stay
# near 2,000 characters, so the list cannot crowd the records out.
MAX_TITLE_CHARS = 160


@dataclass(frozen=True)
class UnshownMatch:
    virtual_record_id: str
    record_id: str
    name: str


def unshown_matches_enabled() -> bool:
    return accuracy_feature_enabled(UNSHOWN_MATCHES_LIST_ENV_VAR)


def unshown_matches(
    matched_record_ids: Iterable[str],
    exclude: Collection[Any],
    permitted_records: Mapping[str, Any],
    limit: int = MAX_UNSHOWN_MATCHES,
    *,
    known_record_ids: Collection[str] = (),
) -> list[UnshownMatch]:
    """The first ``limit`` of ``matched_record_ids`` (virtual record ids, best
    first) not in ``exclude`` and present in ``permitted_records``, the
    retrieval service's permission-checked map of graph records. A record
    whose id is in ``known_record_ids`` (the model already holds it) is skipped."""
    found: list[UnshownMatch] = []
    for vrid in dict.fromkeys(matched_record_ids):
        if len(found) >= limit:
            break
        record = permitted_records.get(vrid)
        if vrid in exclude or not isinstance(record, dict):
            continue
        record_id = record.get("_key") or record.get("id")
        if record_id and str(record_id) not in known_record_ids:
            name = " ".join(str(record.get("recordName") or record.get("record_name") or "").split())
            if len(name) > MAX_TITLE_CHARS:
                name = name[: MAX_TITLE_CHARS - 1] + "…"
            found.append(UnshownMatch(vrid, str(record_id), name))
    return found


def render_unshown_matches(matches: Iterable[UnshownMatch]) -> str:
    """The list for the end of a result, or "" when there is nothing to list.

    ``Record ID:`` lines are what the record-id shortener rewrites, so the ids
    read the same as everywhere else in the result.
    """
    rows = [f"- Record ID: {m.record_id} | {m.name or '(unnamed)'}" for m in matches]
    if not rows:
        return ""
    return (
        "\n\nAlso matched, not shown (lower-ranked; no content loaded). If one may "
        f"hold what you need, read it with `{FETCH_TOOL}`:\n" + "\n".join(rows)
    )
