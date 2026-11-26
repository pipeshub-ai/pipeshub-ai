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


def _ilike_ascii(pattern: str, text: str) -> bool:
    """PostgreSQL's ILIKE in the narrowest locale (C: only ASCII letters fold), for a pattern of "%" and letters and digits."""
    import re
    return re.fullmatch(".*".join(re.escape(p) for p in pattern.split("%")), text.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")), re.S) is not None


def _kept(query: str, name: str) -> bool:
    """Whether the listing's search keeps a name (kg/listing.py filters_sql on kg.records.name_key, which is ``name.lower()``)."""
    key, name_key = name_query_key(query), name.lower()
    return key in (name_query_key(name_key) if (" " in key or "σ" in key) else name_key)


def test_the_name_pattern_finds_every_name_the_listing_search_keeps() -> None:
    """graph.records_by_name finds a name search's candidates by ``name_pattern`` (ILIKE on the node's names); the listing then keeps those whose key holds the
    query's. The pattern must match each kept name in any PostgreSQL locale, or a search would lose rows: names and queries of capitals of other scripts that
    lowercase to ASCII letters, sigmas, separators, LIKE's own wildcards and accents, and random ones drawn from them."""
    import random

    from app.services.graph_db.postgres.kg.listing import name_pattern
    names = ["Printer-Jam.pdf", "printer jam", "PRINTER_JAM_tray.2", "ΟΣ.Α report", "νομος.txt", "ΝΟΜΟΣ x", "100%_done\\x", "a\\%b_c%d", "Café Crème",
             "item 1234 handle", "ITEM 12345 handle", "item 1234-handle", "xx--yy", "Report", "re port", "\u0130tem 77", "\u212aelvin 9", "Kelvin 9", "ſtate"]
    queries = ["printer-jam", "Printer Jam", "jam.pdf", "ΟΣ Α", "νομος", "100%_done", "\\%b", "b_c%d", "café", "item 1234 handle", "item-1234.handle", "1234 h",
               "-yy", "x-", "rep", "re po", "%", "ab", "ç c", "kelvin", "elvin 9", "item", "tem 77", "state", "\u0130tem"]
    rng = random.Random(7)
    alphabet = "aAbBcdeEiIkKsSzZ01289 -_.%\\\u0130\u0131\u212a\u017f\u03a3\u03c3\u03c2\u00e9"
    names += ["".join(rng.choice(alphabet) for _ in range(rng.randint(3, 16))) for _ in range(600)]
    queries += [n[i:i + rng.randint(1, 8)] for n in rng.sample(names, 500) for i in [rng.randrange(len(n))]]
    used = kept = 0
    for q in queries:
        pattern = name_pattern(q)
        if pattern is None:
            continue
        used += 1
        assert all(c in "%0123456789abcdefghjlmnopqrstuvwxyz" for c in pattern) and len(pattern) >= 5, pattern      # the function's own argument check
        for name in names:
            if _kept(q, name):
                kept += 1
                assert _ilike_ascii(pattern, name), (q, name, pattern)
    assert used >= 50 and kept >= 80, (used, kept)
    assert name_pattern("rep") == "%rep%" and name_pattern("item-1234.handle") == "%tem%1234%handle%" and name_pattern("50%_off") == "%50%off%"
    assert name_pattern("re po") is None and name_pattern("νομος") is None and name_pattern("kik") is None and name_pattern("%") is None
    assert name_pattern("") is None and name_pattern(None) is None and name_pattern("x" * 1999) is None      # longer than the function takes
