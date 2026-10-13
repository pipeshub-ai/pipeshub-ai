"""Opt-in wording for knowledge-search query text.

Shared by the two prompts whose model writes that query: the main agent's
when it holds a flat search tool, and the exploration sub-agent's when the
main agent delegates retrieval to it.
"""

from __future__ import annotations

from app.utils.env_utils import accuracy_feature_enabled

# Follows HIGH_ACCURACY_MODE when unset: hybrid search embeds the query and
# matches it against document text, where meta-phrasing is noise. Unmeasured,
# so not yet shown to every model.
KEYWORD_SEARCH_QUERIES_ENV_VAR = "KEYWORD_SEARCH_QUERIES"


def keyword_search_query_rule(search_tool: str) -> str | None:
    """The rule for `search_tool`'s query text, or None while the feature is off."""
    if not accuracy_feature_enabled(KEYWORD_SEARCH_QUERIES_ENV_VAR):
        return None
    return (
        f"Query text for `{search_tool}`: keyword-dense, the entities, terms "
        "and dates as they would appear in a document (e.g. 'parental leave "
        "policy Germany 2024'), with no instructions or meta-phrases such as "
        "'find', 'search for' or 'use the tool'."
    )
