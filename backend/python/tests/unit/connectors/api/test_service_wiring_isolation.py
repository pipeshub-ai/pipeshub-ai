"""Importing the connector service must not leave the router wired for the tests after it.

``app.connectors_main`` builds a container at import, and building it wires the router.
``test_router_deep1.py`` calls the route functions directly, which fails once they are wired.
The two tests below run in file order in one process: the first does what the polluting files
do, the second is the next test (the autouse fixture in ``tests/unit/conftest.py`` has run
between them).
"""

import importlib

from dependency_injector import wiring

from app.connectors.api import router


def _router_is_wired() -> bool:
    # No public query for this: ``wire`` binds providers into the registry's entries and
    # ``unwire`` empties them (the entries stay).
    patched = wiring._patched_registry.get_callables_from_module(router)
    return any(wiring._patched_registry.get_callable(fn).injections for fn in patched)


def test_importing_the_service_wires_the_router() -> None:
    connectors_main = importlib.import_module("app.connectors_main")
    connectors_main.container.wire(modules=["app.connectors.api.router"])
    assert _router_is_wired()


def test_the_next_test_finds_the_router_unwired() -> None:
    assert not _router_is_wired()
