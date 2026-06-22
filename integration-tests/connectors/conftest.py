# pyright: ignore-file

"""Shared fixtures for connector integration tests.

Connector fixtures (``s3_connector``, ``jira_connector``, etc.) never request
``ai_models_configured`` directly, but the indexing pipeline still needs org LLM
+ cloud embedding config when sync runs. This autouse fixture is only a pytest
dependency hook: it forces ``ai_models_configured`` to run for every test under
``connectors/`` without editing six per-provider conftest files.
"""

from __future__ import annotations

import pytest

from ai_models_setup import SeededAIModel


@pytest.fixture(scope="session", autouse=True)
def _connector_indexing_models_configured(
    ai_models_configured: SeededAIModel,
) -> None:
    """Ensure org LLM + embedding are seeded before connector ITs run."""
    del ai_models_configured  # fixture ordering only — side effect is the seed
