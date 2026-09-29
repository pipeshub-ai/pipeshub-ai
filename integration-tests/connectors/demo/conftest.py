"""Demo-data acceptance test fixtures.

The connector suite seeds a cloud LLM and embedding model before any test
runs (``connectors/conftest.py``). That is right for the fresh stack CI
brings up, but an instance that already has data indexed refuses to change
its embedding model. Set ``DEMO_ACCEPTANCE_USE_INSTANCE_MODELS=1`` to run
this test against such an instance with the models it already has.

A golden question may list plain-English facts (``answer_must_state``) that an
AI judge checks the answer against; see ``answer_judge`` in the demo harness.
The judge is built from the same ``TEST_AZURE_OPENAI_*`` settings the suite
gives the instance (``EVAL_PROVIDER`` overrides). Without them those facts are
"not judged", which fails when ``PIPESHUB_REQUIRE_JUDGE=1``, as the workflow
sets it.
"""

from __future__ import annotations

import os
import warnings
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from app.connectors.sources.demo.harness.answer_judge import (  # type: ignore[import-not-found]
        AnswerJudge,
    )


@pytest.fixture(scope="session", autouse=True)
def _connector_indexing_models_configured(request: pytest.FixtureRequest) -> None:
    if os.environ.get("DEMO_ACCEPTANCE_USE_INSTANCE_MODELS") == "1":
        return
    request.getfixturevalue("ai_models_configured")


@pytest.fixture(scope="session")
def answer_judge() -> AnswerJudge | None:
    # Imported here: every shard loads this conftest, and only the demo test needs a model.
    from app.connectors.sources.demo.harness.answer_judge import (  # type: ignore[import-not-found]
        AnswerJudge,
    )
    from tests.evals.chat_models import (  # type: ignore[import-not-found]
        MissingModelError,
        judge_client_from_env,
    )

    try:
        client, _provider, _model = judge_client_from_env()
    except MissingModelError as exc:
        warnings.warn(f"demo answers will not be judged: {exc}", stacklevel=1)
        return None
    return AnswerJudge(client)
