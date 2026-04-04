"""Shared test setup for agents/deep tests."""

import sys
from types import ModuleType

# Ensure langchain.agents is resolvable for patch() on Python 3.12+.
# Python 3.12 changed mock.patch to use pkgutil.resolve_name, which
# requires submodules to be present in sys.modules (unlike the old
# _dot_lookup that auto-imported them). We register a stub module so
# patch("langchain.agents.create_agent") can resolve without triggering
# the full (and potentially broken) langchain.agents import chain.
if "langchain.agents" not in sys.modules:
    import langchain

    _agents_stub = ModuleType("langchain.agents")
    _agents_stub.create_agent = None  # type: ignore[attr-defined]
    sys.modules["langchain.agents"] = _agents_stub
    langchain.agents = _agents_stub  # type: ignore[attr-defined]
