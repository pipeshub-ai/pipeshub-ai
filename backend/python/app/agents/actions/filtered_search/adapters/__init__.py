"""Concrete `NativeFilterAdapter` implementations. Importing this package
registers every built-in adapter with `FilterAdapterRegistry` — the only
place connector-specific filter logic lives.

Adding connector N+1: write `adapters/<connector>.py` with one
`NativeFilterAdapter` subclass, import it here, done. No other file in
`filtered_search/`, no hook, no prompt-builder code needs to change.
"""

from app.agents.actions.filtered_search.adapters.confluence import (
    ConfluenceFilterAdapter,
)
from app.agents.actions.filtered_search.adapters.jira import JiraFilterAdapter
from app.agents.actions.filtered_search.adapters.slack import SlackFilterAdapter
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry

# Registered against every `Connectors` enum value (`config/constants/
# arangodb.py`) this adapter actually serves, including "PERSONAL" (OAuth
# individual-scope) variants — the personal/team distinction is an auth
# detail the toolset layer already resolves to the same client class, but
# `FilterAdapterRegistry.get()` matches by the graph's connector `type`
# string exactly (see `registry.py::_normalize`), not fuzzily, so each
# distinct type string a connector can appear as must be listed here.
FilterAdapterRegistry.register("JIRA", JiraFilterAdapter)
FilterAdapterRegistry.register("JIRA_PERSONAL", JiraFilterAdapter)
FilterAdapterRegistry.register("JIRA_DATA_CENTER", JiraFilterAdapter)
FilterAdapterRegistry.register("JIRA_DATA_CENTER_PERSONAL", JiraFilterAdapter)
FilterAdapterRegistry.register("CONFLUENCE", ConfluenceFilterAdapter)
FilterAdapterRegistry.register("CONFLUENCE_DATA_CENTER", ConfluenceFilterAdapter)
FilterAdapterRegistry.register("CONFLUENCE_DATA_CENTER_PERSONAL", ConfluenceFilterAdapter)
FilterAdapterRegistry.register("SLACK", SlackFilterAdapter)
FilterAdapterRegistry.register("SLACK_WORKSPACE", SlackFilterAdapter)

__all__ = ["JiraFilterAdapter", "ConfluenceFilterAdapter", "SlackFilterAdapter"]
