"""The public limits shown in the documentation are rendered from `scenarios/deltas.yaml`, and only from entries marked public."""

from __future__ import annotations

import re

from .deltas import Delta, load_deltas
from .gen_docs import HEADER, render

GOLDEN = """| Connector | Source permission | What PipesHub does differently |
| --- | --- | --- |
| Confluence | A page nested very deep in a space | A page more than 50 levels below its space is hidden from everyone. Move it closer to the top of the space. |
| Gmail | A delegate added to a mailbox | Delegates cannot search the mailbox in PipesHub. Only the mailbox owner can. |
| Google Drive | A file shared with your whole domain, or with anyone who has the link | It does not give everyone in the organisation access in PipesHub. People it is shared with directly or through a group still see it. |
"""
INTERNAL = re.compile(r"DELTA|\.py|\bR\d+\b|\bEE\b|enterprise|tenant|billing|customer|ticket|S\d+[a-z]?\b|PM-\d+", re.IGNORECASE)


def test_the_published_table_is_the_golden_one_and_reproducible() -> None:
    assert render(load_deltas()) == GOLDEN
    assert render(load_deltas()) == render(load_deltas())


def test_an_entry_that_is_not_marked_public_stays_internal() -> None:
    internal = Delta("DELTA-99", "under-share", ("X",), ("X/a/b",), "an internal reason", "a fix")
    public = Delta("DELTA-98", "under-share", ("X",), ("X/a/b",), "r", "f", {"connector": "Jira", "source": "s", "differs": "d"})
    assert render([internal]) == HEADER + "\n"
    assert render([internal, public]).splitlines()[2:] == ["| Jira | s | d |"]


def test_a_cell_cannot_break_the_table() -> None:
    d = Delta("D", "under-share", (), (), "r", "f", {"connector": "A|B", "source": "line\none", "differs": "x"})
    assert render([d]).splitlines()[2] == "| A\\|B | line one | x |"


def test_what_is_published_is_written_for_the_reader() -> None:
    for d in load_deltas():
        if d.public:
            assert set(d.public) == {"connector", "source", "differs"}, d.id
            for text in d.public.values():
                assert text.strip() and not INTERNAL.search(text), (d.id, text)
