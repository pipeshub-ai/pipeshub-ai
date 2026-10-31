"""The knowledge-hub listings read "-", "_" and "." in names and name queries as spaces, and "ς" as "σ".

That the stores apply the same rule is proven on real stores in
tests/integration/graph_permissions/test_kh_name_search_first.py.
"""

from __future__ import annotations

from app.services.graph_db.common.name_match import (
    aql_name_key,
    cypher_name_key,
    name_query_key,
    name_query_params,
)


def test_separators_read_as_spaces_one_for_one() -> None:
    assert name_query_key("Printer-Jam_tray.2") == "printer jam tray 2"
    # One for one, so a query that matched a name literally still matches it.
    assert name_query_key("a - b") == "a   b"


def test_only_a_spaced_query_compares_spaced_names() -> None:
    assert name_query_params("Printer") == {"kh_search": "printer", "kh_norm": False}
    assert name_query_params("printer jam") == {"kh_search": "printer jam", "kh_norm": True}
    assert name_query_params("printer-jam") == {"kh_search": "printer jam", "kh_norm": True}


def test_a_final_sigma_reads_as_a_sigma() -> None:
    """A capital sigma lowercases by what follows it: "ΟΣ.Α" to "οσ.α" but "ΟΣ Α" to "ος α"."""
    assert name_query_key("ΟΣ.Α") == name_query_key("ΟΣ Α") == "οσ α"
    # A query typed alone ends in "ς" where a longer name has "σ", so it takes the compared path.
    assert name_query_params("νομος") == {"kh_search": "νομοσ", "kh_norm": True}


def test_a_term_with_no_wildcards() -> None:
    """``%`` stays literal: the stores compare with CONTAINS, never LIKE."""
    assert name_query_params("50% off")["kh_search"] == "50% off"


def test_both_stores_read_the_switch_from_the_bind_value() -> None:
    cypher = cypher_name_key("node.name")
    aql = aql_name_key("node.name")
    assert "$kh_norm" in cypher and "toLower(node.name)" in cypher
    assert "@kh_norm" in aql and "LOWER(node.name)" in aql
    assert "LIKE" not in cypher.upper() and "LIKE" not in aql.upper()
