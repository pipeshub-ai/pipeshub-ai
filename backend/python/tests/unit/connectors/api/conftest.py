"""Break the router <-> edition_config import cycle before collection.

`app/connectors/api/router.py:63` imports `app.edition_config`, which imports
the router back, and `router = APIRouter()` is defined 85 lines *after* that
import — so a re-entry finds the module half-built and `ImportError`s.

A full `pytest tests/unit` run survives only by accident of alphabetical
collection: `tests/unit/api/routes/test_mcp_servers.py` pulls `edition_config`
to completion first. Scope the run to this directory and nothing has, so twelve
router modules fail at collection.

Importing `edition_config` here drives it to completion before any test module
is imported, which makes the outcome the same either way.

It also pins the base connector resolvers. `app.edition_config` hands the router
its resolvers and an edition may swap them. These tests mock a plain container
and config service to exercise the router itself, so they need the base
resolvers on every edition; an edition's own resolvers are covered by its own
tests.
"""

import importlib
import inspect

import pytest

# Imported for its side effect (see above), so through importlib rather than
# as an unused name.
importlib.import_module("app.edition_config")

import app.connectors.api.connector_resolvers as base_resolvers  # noqa: E402
import app.connectors.api.router as router_module  # noqa: E402


@pytest.fixture(autouse=True)
def _base_connector_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, fn in inspect.getmembers(base_resolvers, inspect.isfunction):
        if fn.__module__ == base_resolvers.__name__ and hasattr(router_module, name):
            monkeypatch.setattr(router_module, name, fn)
