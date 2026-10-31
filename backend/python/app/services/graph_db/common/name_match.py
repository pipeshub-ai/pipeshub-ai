"""How the knowledge-hub listings match a name query.

File names join their words with "-", "_" or ".", and a name query was matched as one literal
substring, so "printer jam" found nothing while "printer-jam" found printer-jam.pdf. Both sides
are now compared with each of those characters read as a space, one for one, so a query that
matched before still matches.

Lowercasing is not context-free for one letter: a capital sigma lowercases to "ς" at the end of
a word and to "σ" elsewhere, so the same name lowercases differently with a space or a "." after
it, and a query typed alone ends in "ς" where the name continues. Both sides read "ς" as "σ".

A query with no space, separator or sigma matches the same names either way, so the listing
compares the plain lowercased name for it (the ``kh_norm`` bind value is false) and only a query
that needs the replacements pays for them.

Both backends build the expressions here so the rule has one definition.
"""

from typing import Any

_SEPARATORS = ("-", "_", ".")
_REPLACEMENTS = (("ς", "σ"), *((separator, " ") for separator in _SEPARATORS))


def name_query_key(query: str) -> str:
    """``query`` as the listing compares it: lowercased, "ς" read as "σ" and each "-", "_" or "." as a space."""
    key = query.lower()
    for old, new in _REPLACEMENTS:
        key = key.replace(old, new)
    return key


def name_query_params(query: str) -> dict[str, Any]:
    """The ``kh_search`` and ``kh_norm`` bind values for a name query."""
    key = name_query_key(query)
    return {"kh_search": key, "kh_norm": " " in key or "σ" in key}


def cypher_name_key(expr: str) -> str:
    """Cypher for the name ``expr`` as ``name_query_key`` reads it when ``$kh_norm``, else lowercased."""
    lowered = f"toLower({expr})"
    normalized = lowered
    for old, new in _REPLACEMENTS:
        normalized = f"replace({normalized}, '{old}', '{new}')"
    return f"(CASE WHEN $kh_norm THEN {normalized} ELSE {lowered} END)"


def aql_name_key(expr: str) -> str:
    """AQL for the name ``expr`` as ``name_query_key`` reads it when ``@kh_norm``, else lowercased."""
    lowered = f"LOWER({expr})"
    olds = ", ".join(f"'{old}'" for old, _ in _REPLACEMENTS)
    news = ", ".join(f"'{new}'" for _, new in _REPLACEMENTS)
    return f"(@kh_norm ? SUBSTITUTE({lowered}, [{olds}], [{news}]) : {lowered})"
