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
from helper.second_user import second_user  # noqa: F401 - fixture


@pytest.fixture(scope="session", autouse=True)
def _connector_indexing_models_configured(
    ai_models_configured: SeededAIModel,
) -> None:
    """Ensure org LLM + embedding are seeded before connector ITs run."""
    del ai_models_configured  # fixture ordering only — side effect is the seed


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip or xfail scenario-matrix tests from their connector's declarations, before anything runs."""
    del config
    from connectors.scenario_matrix import apply_static_marks

    apply_static_marks(items)


@pytest.fixture(scope="class")
def scenario_run(
    request: pytest.FixtureRequest, scenario_adapter, vector_store, blob_store, mongo_store,
    test_org_id: str,
):
    """One connector's walk through ``scenario_matrix``; ``scenario_adapter`` comes from its module."""
    from connectors.scenario_matrix import MatrixRun

    return MatrixRun(
        scenario_adapter,
        unsupported=dict(getattr(request.cls, "UNSUPPORTED", {})),
        known_bugs=dict(getattr(request.cls, "KNOWN_BUGS", {})),
        vector=vector_store,
        blob=blob_store,
        mongo=mongo_store,
        org_id=test_org_id,
    )
