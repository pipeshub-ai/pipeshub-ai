"""The parameters of a hop page: where the cursor is decides which branches of the statement read, and from where."""
import pytest

from app.services.graph_db.postgres.codegraph import _hop_args

SEP = "\x01"


def args(anchors: list[str], after: str, *, out: bool = True, inn: bool = True, both: bool = False) -> list:
    return _hop_args("o", "blocks", anchors, ["CALLS"], 200, out=out, inn=inn, both=both, after=after)


def test_the_first_page_reads_every_anchor_from_its_start() -> None:
    a = args(["b", "a"], "")
    assert a[4:7] == [True, True, False] and a[7] is False and a[15] is False and a[14] == ["a", "b"] and a[22] == ["a", "b"]


def test_a_cursor_in_an_anchors_inbound_edges_resumes_there_and_reads_the_later_anchors() -> None:
    a = args(["a", "b", "c"], SEP.join(["b", "inbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"]))
    assert a[7] is True and a[8] == "b" and a[9:14] == ["", "", "", "", ""]                                   # outbound edges of b all follow a cursor in its inbound edges
    assert a[15] is True and a[17:22] == ["blocks", "k", "CALLS", "nodeRelations", "CALLS"]                    # the inbound ones resume after it
    assert a[14] == ["c"] and a[22] == ["c"]


def test_a_cursor_in_an_anchors_outbound_edges_leaves_its_inbound_edges_behind() -> None:
    a = args(["a", "b", "c"], SEP.join(["b", "outbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"]))
    assert a[7] is True and a[9:14] == ["blocks", "k", "CALLS", "nodeRelations", "CALLS"] and a[15] is False


def test_a_cursor_of_an_anchor_not_asked_for_resumes_with_the_anchors_after_it() -> None:
    a = args(["a", "z"], SEP.join(["m", "inbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"]))
    assert a[7] is False and a[15] is False and a[14] == ["z"]


@pytest.mark.parametrize("bad", ["x", SEP.join(["a", "b"]), SEP.join(["a"] * 8)])
def test_a_malformed_cursor_is_an_error(bad) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        args(["a"], bad)


def test_the_directions_asked_for_switch_the_branches() -> None:
    assert args(["a"], "", out=False)[4:6] == [False, True] and args(["a"], "", inn=False)[4:6] == [True, False]


async def test_a_page_sets_the_planner_profile_and_is_planned_for_its_arguments() -> None:
    from app.services.graph_db.postgres import codegraph as cg

    seen: list[str] = []

    class Tx:
        async def fetchval(self, sql: str, *a: object) -> None:
            seen.append(sql)

        async def fetch(self, sql: str, *a: object) -> list:
            seen.append("generic:" + sql)
            return []

        async def fetch_custom_plan(self, sql: str, *a: object) -> list:
            seen.append(sql)
            return []
    await cg._hop_page(Tx(), "o", "blocks", ["a"], ["CALLS"], 5, out=True, inn=True, both=False, after="")
    assert seen == [cg._HOP_PROFILE, cg._HOP_PAGE_SQL]      # a custom plan: the generic plan of a prepared statement cannot use the cursor bound
