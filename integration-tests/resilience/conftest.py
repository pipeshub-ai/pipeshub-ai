"""Fixtures for breaking a dependency of the running stack on purpose.

These tests restart the broker, the vector database, the graph database or
MongoDB, kill the indexing process, cut the app off from its AI provider, or
make blob storage unwritable, while the product is busy. That disturbs anything else using the
stack, so they carry their own marker and run on their own, after the rest of
the suite (see the integration workflow).
"""

from __future__ import annotations

import pytest

from helper.compose_control import ComposeStack, ComposeUnavailable, graph_service

STACK_SERVICES = ("pipeshub-ai", "redis", "qdrant", "mongodb", graph_service())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run only when asked for by marker, never as part of a plain ``pytest``."""
    if "resilience" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="restarts shared services; select it with -m resilience")
    for item in items:
        if item.get_closest_marker("resilience"):
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _resilience_indexing_models_configured(ai_models_configured) -> None:
    """Indexing cannot reach COMPLETED without an org LLM and embedding model."""
    del ai_models_configured  # fixture ordering only — the seed is the effect


@pytest.fixture(scope="session")
def compose() -> ComposeStack:
    """The compose stack under test, or a skip that says why it cannot be reached."""
    try:
        stack = ComposeStack.from_env()
        stack.require(*STACK_SERVICES)
    except ComposeUnavailable as exc:
        pytest.skip(f"cannot control the integration stack from here: {exc}")
    return stack
