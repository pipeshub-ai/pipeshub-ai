"""H4 PB-8: ``grant_read`` decides as ``decide_read`` and names the publication that decided it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.knowledge.publications import (
    Audience,
    Principal,
    Publication,
    ReadGrant,
    decide_read,
    grant_read,
)

if TYPE_CHECKING:
    from harness_core.knowledge.publications import PublicationMode

NOW = 1_000
READER = Principal("org-1", "reader")


def _publication(pid: str, mode: PublicationMode, *, users: frozenset[str] = frozenset({"reader"}), **kw: int) -> Publication:
    return Publication(pid, "org-1", "apps/kb", Audience(user_ids=users), mode, "owner", 1, **kw)


ROWS: list[tuple[str, bool, list[Publication], ReadGrant]] = [
    ("own wins over a publication", True, [_publication("p1", "full")], ReadGrant("own")),
    ("nothing covers it", False, [], ReadGrant("denied")),
    ("full", False, [_publication("p1", "full")], ReadGrant("published_full", "p1")),
    ("answer only", False, [_publication("p1", "answer_only")], ReadGrant("published_answer_only", "p1")),
    (
        "full is wider than an earlier answer-only",
        False,
        [_publication("p0", "answer_only"), _publication("p2", "full")],
        ReadGrant("published_full", "p2"),
    ),
    (
        "two full: the lowest id, whatever the order given",
        False,
        [_publication("p9", "full"), _publication("p3", "full")],
        ReadGrant("published_full", "p3"),
    ),
    ("revoked", False, [_publication("p1", "full", revoked_at_ms=5)], ReadGrant("denied")),
    ("expired", False, [_publication("p1", "full", expires_at_ms=NOW)], ReadGrant("denied")),
    (
        "outside the audience",
        False,
        [_publication("p1", "full", users=frozenset({"someone"}))],
        ReadGrant("denied"),
    ),
    (
        "a revoked full beside a live answer-only",
        False,
        [_publication("p1", "full", revoked_at_ms=5), _publication("p2", "answer_only")],
        ReadGrant("published_answer_only", "p2"),
    ),
]


@pytest.mark.parametrize(("case", "own", "covering", "expected"), ROWS, ids=[row[0] for row in ROWS])
def test_pb8_grant_read_decides_as_decide_read_and_names_the_publication(
    case: str, own: bool, covering: list[Publication], expected: ReadGrant
) -> None:
    granted = grant_read(own=own, covering=covering, principal=READER, now_ms=NOW)
    assert granted == expected, case
    assert granted.decision == decide_read(own=own, covering=covering, principal=READER, now_ms=NOW)
