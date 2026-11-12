"""The parameters of a hop page: where the cursor is decides which branches of the statement read, and from where."""
import pytest

from app.services.graph_db.postgres.codegraph import _cursor, _decode_cursor, _hop_args

SEP = "\x01"


def cur(anchor: str, d: str, coll: str, key: str, rel: str, edge_coll: str, ekey: str) -> str:
    return _cursor({"anchor": anchor, "dir": d, "coll": coll, "key": key, "rel": rel, "edge_coll": edge_coll, "ekey": ekey})


def args(anchors: list[str], after: str, *, out: bool = True, inn: bool = True, both: bool = False) -> list:
    return _hop_args("o", "blocks", anchors, ["CALLS"], 200, out=out, inn=inn, both=both, after=after)


def test_the_first_page_reads_every_anchor_from_its_start() -> None:
    a = args(["b", "a"], "")
    assert a[4:7] == [True, True, False] and a[7] is False and a[15] is False and a[14] == ["a", "b"] and a[22] == ["a", "b"]


def test_a_cursor_in_an_anchors_inbound_edges_resumes_there_and_reads_the_later_anchors() -> None:
    a = args(["a", "b", "c"], cur("b", "inbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"))
    assert a[7] is True and a[8] == "b" and a[9:14] == ["", "", "", "", ""]                                   # outbound edges of b all follow a cursor in its inbound edges
    assert a[15] is True and a[17:22] == ["blocks", "k", "CALLS", "nodeRelations", "CALLS"]                    # the inbound ones resume after it
    assert a[14] == ["c"] and a[22] == ["c"]


def test_a_cursor_in_an_anchors_outbound_edges_leaves_its_inbound_edges_behind() -> None:
    a = args(["a", "b", "c"], cur("b", "outbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"))
    assert a[7] is True and a[9:14] == ["blocks", "k", "CALLS", "nodeRelations", "CALLS"] and a[15] is False


def test_a_cursor_of_an_anchor_not_asked_for_resumes_with_the_anchors_after_it() -> None:
    a = args(["a", "z"], cur("m", "inbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"))
    assert a[7] is False and a[15] is False and a[14] == ["z"]


@pytest.mark.parametrize("bad", ["x", SEP.join(["a", "b"]), SEP.join(["a", "inbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"]), "v2:" + SEP.join(["a"] * 7), "v2:a"])
def test_a_cursor_that_is_not_of_the_ordered_page_is_an_error_here(bad: str) -> None:
    with pytest.raises(ValueError, match="not a cursor"):
        args(["a"], bad)


@pytest.mark.parametrize("field", ["plain", "p\x01A", "\x01", "a\\", "\\1", "\\\x01\\", "v2:", "é\x01é", ""])
def test_a_field_of_any_text_round_trips_through_the_cursor(field: str) -> None:
    fields = [field, "inbound", "blocks", field, field, "nodeRelations", field]
    assert _decode_cursor(cur(*fields)) == fields
    assert SEP not in cur(*fields)[3:].replace(SEP, "", 6)               # the separator of the fields is the only one in it


def test_the_directions_asked_for_switch_the_branches() -> None:
    assert args(["a"], "", out=False)[4:6] == [False, True] and args(["a"], "", inn=False)[4:6] == [True, False]


@pytest.mark.parametrize("restore", [False, True])
async def test_a_page_sets_the_planner_profile_before_it_reads_and_a_callers_transaction_gets_its_settings_back(restore: bool) -> None:
    from app.services.graph_db.postgres import codegraph as cg

    seen: list[tuple] = []

    class Tx:
        async def fetchrow(self, sql: str, *a: object) -> dict:
            seen.append((sql,))
            return {"b": "on", "s": "on", "p": "2", "m": "auto"}

        async def fetchval(self, sql: str, *a: object) -> None:
            seen.append((sql, *a))

        async def fetch(self, sql: str, *a: object) -> list:
            seen.append((sql,))
            return []
    await cg._hop_page(Tx(), "o", "blocks", ["a"], ["CALLS"], 5, out=True, inn=True, both=False, after="", restore=restore)
    assert seen == [(cg._HOP_PROFILE,), (cg._HOP_FIRST_ANY_SQL,), *([(cg._HOP_RESTORE, "on", "on", "2", "auto")] if restore else [])]


@pytest.mark.parametrize(("out", "inn", "sql", "n"), [(True, False, "_HOP_FIRST_OUT_SQL", 5), (False, True, "_HOP_FIRST_IN_SQL", 6), (True, True, "_HOP_FIRST_ANY_SQL", 6)])
def test_a_first_page_has_the_statement_of_its_directions_only(out: bool, inn: bool, sql: str, n: int) -> None:
    from app.services.graph_db.postgres import codegraph as cg

    text, args = cg._hop_statement("o", "blocks", ["b", "a", "a"], ["CALLS"], 7, out=out, inn=inn, both=out and inn, after="")
    assert text is getattr(cg, sql) and len(args) == n and args[4] == ["a", "b"]
    assert max(int(x[1:]) for x in __import__("re").findall(r"\$\d+", text)) == n                  # every parameter is used and none is missing: a gap is an error of the server


def test_a_cursor_takes_the_statement_of_all_branches() -> None:
    from app.services.graph_db.postgres import codegraph as cg

    text, args = cg._hop_statement("o", "blocks", ["a"], ["CALLS"], 7, out=True, inn=True, both=False, after=cur("a", "outbound", "blocks", "k", "CALLS", "nodeRelations", "CALLS"))
    assert text is cg._HOP_PAGE_SQL and len(args) == 23
