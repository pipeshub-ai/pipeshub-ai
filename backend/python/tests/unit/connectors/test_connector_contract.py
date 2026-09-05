"""Contract every registered connector has to satisfy.

Integration tests for connectors need a live account at the provider, which is
why only a minority of the shipped connectors have one. These do not: they hold
the whole registry to the shape `BaseConnector` defines, using nothing but
imports and reflection.

That covers a specific and recurring class of regression. When an abstract
method is added to `BaseConnector`, or `create_connector`'s signature changes,
or a connector module is renamed, the break is not visible at import time — it
surfaces the first time somebody syncs that one source, on the connector nobody
happened to try. Every connector is checked here on every pull request instead.

What these cannot tell you is whether a connector still talks to its provider
correctly. A rotated scope or a retired endpoint is only caught by
`integration-tests/connectors/`, which needs credentials.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.factory.connector_factory import ConnectorFactory

SOURCES_DIR = Path(__file__).resolve().parents[3] / "app" / "connectors" / "sources"


def _registry() -> dict[str, type]:
    """Every connector the product can construct, stable and beta alike."""
    combined: dict[str, type] = {}
    combined.update(getattr(ConnectorFactory, "_connector_registry", {}))
    combined.update(getattr(ConnectorFactory, "_beta_connector_definitions", {}))
    return combined


REGISTRY = _registry()
CONNECTOR_IDS = sorted(REGISTRY)

# Seven entries in `_beta_connector_definitions` are placeholders produced by the
# builder decorator in app/connectors/core/registry/connector.py. They are not
# BaseConnector subclasses and have no create_connector, run_sync or streaming
# support; their connect() prints a line and returns True.
#
# They are not inert. connectors_main.py:229 calls
# initialize_beta_connector_registry(), which registers them into the main
# registry, and router.py:2659 serves them to the UI — so they are offered to
# users as connectors that cannot sync.
#
# Recorded rather than asserted, so this file gates new breakage instead of
# failing on a known issue. Deleting a name from this set is how you prove one
# has been implemented; the test then requires it to satisfy the full contract.
KNOWN_INCOMPLETE = {
    "airtable",
    "calendar",
    "docs",
    "forms",
    "meet",
    "slides",
    "zendesk",
}

COMPLETE_IDS = [k for k in CONNECTOR_IDS if k not in KNOWN_INCOMPLETE]


def test_registry_is_not_empty() -> None:
    """A refactor that empties the registry would make every test below vacuous."""
    assert len(REGISTRY) >= 40, (
        f"only {len(REGISTRY)} connectors registered; the registry or this test is wrong"
    )


def test_known_incomplete_list_is_still_accurate() -> None:
    """Every name recorded as incomplete is still registered.

    A stale entry would silently exempt a connector that no longer exists, or
    mask one that was renamed.
    """
    stale = sorted(KNOWN_INCOMPLETE - set(CONNECTOR_IDS))
    assert not stale, (
        f"these are recorded as incomplete but are no longer registered: {stale}. "
        "Remove them from KNOWN_INCOMPLETE."
    )


def test_recorded_incomplete_connectors_have_not_been_quietly_fixed() -> None:
    """Tells you when a placeholder has become a real connector.

    Passing the full contract while still listed here means the exemption is no
    longer needed, and keeping it would stop this file guarding that connector.
    """
    now_complete = []
    for key in sorted(KNOWN_INCOMPLETE & set(CONNECTOR_IDS)):
        cls = REGISTRY[key]
        if (
            inspect.isclass(cls)
            and issubclass(cls, BaseConnector)
            and not getattr(cls, "__abstractmethods__", set())
        ):
            now_complete.append(key)
    assert not now_complete, (
        f"{now_complete} now satisfy the connector contract. Remove them from "
        "KNOWN_INCOMPLETE so they are held to it from here on."
    )


@pytest.mark.parametrize("key", COMPLETE_IDS)
def test_registered_value_is_a_connector_class(key: str) -> None:
    cls = REGISTRY[key]
    assert inspect.isclass(cls), f"{key!r} maps to {cls!r}, which is not a class"
    assert issubclass(cls, BaseConnector), (
        f"{key!r} maps to {cls.__name__}, which does not derive from BaseConnector"
    )


@pytest.mark.parametrize("key", COMPLETE_IDS)
def test_no_unimplemented_abstract_methods(key: str) -> None:
    """Every abstract method on BaseConnector is implemented.

    Python leaves unimplemented names in ``__abstractmethods__`` and raises only
    when the class is instantiated. Without this check, adding a method to
    BaseConnector breaks every connector that did not implement it, and the
    failure surfaces one connector at a time in production.
    """
    cls = REGISTRY[key]
    missing = sorted(getattr(cls, "__abstractmethods__", set()))
    assert not missing, (
        f"{cls.__name__} ({key}) does not implement: {', '.join(missing)}. "
        "It cannot be instantiated, so this connector is dead on arrival."
    )


@pytest.mark.parametrize("key", COMPLETE_IDS)
def test_create_connector_accepts_the_parameters_the_factory_passes(key: str) -> None:
    """`create_connector` names every parameter the base declares.

    The factory passes these by keyword. Accepting them into `**kwargs` instead
    of naming them is not good enough: the call still succeeds, and the body
    then raises NameError for a parameter it believes it received. Every
    connector names all five today, so this is a floor, not an aspiration.
    """
    cls = REGISTRY[key]
    impl = inspect.signature(cls.create_connector)
    base = inspect.signature(BaseConnector.create_connector)

    required = [
        name
        for name, p in base.parameters.items()
        if name not in ("cls", "self")
        and p.kind is not inspect.Parameter.VAR_KEYWORD
        and p.kind is not inspect.Parameter.VAR_POSITIONAL
    ]
    missing = [name for name in required if name not in impl.parameters]
    assert not missing, (
        f"{cls.__name__}.create_connector does not name {missing}. The factory "
        f"passes them by keyword, so they would land in **kwargs and the body "
        f"would raise NameError when it used them.\n"
        f"  base: {base}\n  impl: {impl}"
    )


@pytest.mark.parametrize("key", CONNECTOR_IDS)
def test_registry_key_is_a_safe_lookup_token(key: str) -> None:
    """Keys arrive from config and URLs, so they must be unambiguous.

    An upper-case or space-bearing key silently fails to match a lookup that
    normalises its input, which reads to a user as "this connector does not
    exist".
    """
    assert key == key.lower(), f"registry key {key!r} is not lower-case"
    assert key.isascii() and key.replace("_", "").isalnum(), (
        f"registry key {key!r} should be alphanumeric with underscores only"
    )


def test_no_two_keys_share_one_class_by_accident() -> None:
    """Several keys legitimately share a class; this records which, so a
    copy-paste registration that points a new connector at the wrong class shows
    up as a change rather than passing unnoticed."""
    by_class: dict[str, list[str]] = {}
    for key, cls in REGISTRY.items():
        by_class.setdefault(cls.__name__, []).append(key)
    shared = {c: sorted(k) for c, k in by_class.items() if len(k) > 1}
    assert shared == {}, (
        "these classes are registered under more than one key: "
        f"{shared}. If that is deliberate, update this test to record it."
    )


# ---------------------------------------------------------------------------
# Architectural rules. Connectors must reach storage through the provider
# interfaces, so the same connector works on whichever backend an instance was
# installed with.

FORBIDDEN_IMPORTS = {
    "neo4j": "use IGraphDBProvider",
    "qdrant_client": "use IVectorDBService",
    "arango": "use IGraphDBProvider",
    "pymongo": "Mongo is owned by the Node service",
    "aiokafka": "use MessagingFactory",
    "kafka": "use MessagingFactory",
}


def _connector_modules() -> list[Path]:
    if not SOURCES_DIR.is_dir():  # pragma: no cover - layout guard
        pytest.skip(f"connector sources not found at {SOURCES_DIR}")
    return sorted(SOURCES_DIR.rglob("*.py"))


def test_connector_sources_directory_is_found() -> None:
    """Guards the rule below: a wrong path would scan nothing and always pass."""
    assert len(_connector_modules()) > 50, (
        f"expected many connector modules under {SOURCES_DIR}, found "
        f"{len(_connector_modules())}"
    )


def test_no_connector_imports_a_storage_client_directly() -> None:
    """Connectors go through the provider interfaces, never a concrete client.

    A connector that imports qdrant_client or neo4j works on the backend the
    author happened to run and fails on every other one, which is only
    discovered by whoever installed the product differently.
    """
    violations: list[str] = []
    for path in _connector_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - a syntax error is its own failure
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    rel = path.relative_to(SOURCES_DIR.parents[2])
                    violations.append(
                        f"{rel}:{node.lineno} imports {name} — {FORBIDDEN_IMPORTS[root]}"
                    )
    assert not violations, "connectors must not import storage clients directly:\n" + "\n".join(
        f"  {v}" for v in violations
    )
