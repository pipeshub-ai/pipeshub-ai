"""How the knowledge-hub listings match a name query.

File names join their words with "-", "_" or ".", and a name query was matched as one literal
substring, so "printer jam" found nothing while "printer-jam" found printer-jam.pdf. Both sides
are now compared with each of those characters read as a space, one for one, so a query that
matched before still matches.

A query with no space or separator matches the same names either way, so the listing compares
the plain lowercased name for it (the ``kh_norm`` bind value is false) and only a spaced query
pays for the replacements.

Both backends build the expressions here so the rule has one definition.
"""

from typing import Any

_SEPARATORS = ("-", "_", ".")


def name_query_key(query: str) -> str:
    """``query`` as the listing compares it: lowercased, each "-", "_" or "." read as a space."""
    key = query.lower()
    for separator in _SEPARATORS:
        key = key.replace(separator, " ")
    return key


def name_query_params(query: str) -> dict[str, Any]:
    """The ``kh_search`` and ``kh_norm`` bind values for a name query."""
    key = name_query_key(query)
    return {"kh_search": key, "kh_norm": " " in key}


def cypher_name_key(expr: str) -> str:
    """Cypher for the name ``expr`` as ``name_query_key`` reads it when ``$kh_norm``, else lowercased."""
    lowered = f"toLower({expr})"
    spaced = lowered
    for separator in _SEPARATORS:
        spaced = f"replace({spaced}, '{separator}', ' ')"
    return f"(CASE WHEN $kh_norm THEN {spaced} ELSE {lowered} END)"


def aql_name_key(expr: str) -> str:
    """AQL for the name ``expr`` as ``name_query_key`` reads it when ``@kh_norm``, else lowercased."""
    lowered = f"LOWER({expr})"
    replaced = ", ".join(f"'{separator}'" for separator in _SEPARATORS)
    spaces = ", ".join("' '" for _ in _SEPARATORS)
    return f"(@kh_norm ? SUBSTITUTE({lowered}, [{replaced}], [{spaces}]) : {lowered})"
