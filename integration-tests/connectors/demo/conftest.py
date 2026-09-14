"""Demo-data acceptance test fixtures.

The connector suite seeds a cloud LLM and embedding model before any test
runs (``connectors/conftest.py``). That is right for the fresh stack CI
brings up, but an instance that already has data indexed refuses to change
its embedding model. Set ``DEMO_ACCEPTANCE_USE_INSTANCE_MODELS=1`` to run
this test against such an instance with the models it already has.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _connector_indexing_models_configured(request: pytest.FixtureRequest) -> None:
    if os.environ.get("DEMO_ACCEPTANCE_USE_INSTANCE_MODELS") == "1":
        return
    request.getfixturevalue("ai_models_configured")
