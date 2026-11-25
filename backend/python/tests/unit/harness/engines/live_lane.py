"""What the live engine lanes share: the org's Azure model entry, the fixture that exports its variables, and Node's
model-grant and budget routes for the attempts the rig minted tokens for.

Runs only when the Azure test variables are set: in the environment, or in the dotenv file ``HARNESS_LIVE_ENV_FILE``
names, exported for the test only. Values are never printed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import pytest

from harness_core.access.types import route_digest
from harness_core.budget.tree import usd_micros
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from tests.unit.harness.access.support import FakeNode
from tests.unit.harness.access.test_live_azure_gateway import (
    REQUIRED,
    _dotenv,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from aiohttp import web

    from harness_core.access.types import PinnedRoute

ORG = "org-ev1-live"
MODEL_KEY = "key-azure-live"
RUN_BUDGET_USD = 0.5
RUN_BUDGET_TOKENS = 2_000_000


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    source = os.environ.get("HARNESS_LIVE_ENV_FILE")
    if source and Path(source).is_file():
        for name, value in _dotenv(source).items():
            if name.startswith("TEST_AZURE_OPENAI_") and value:
                monkeypatch.setenv(name, value)
    if not all(os.environ.get(v) for v in REQUIRED):
        pytest.skip("needs the Azure OpenAI test variables")


def _model() -> str:
    return os.environ.get("TEST_AZURE_OPENAI_MODEL") or os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"]


def _ai_models() -> dict[str, Any]:
    return {
        "llm": [
            {
                "provider": "azureOpenAI",
                "modelKey": MODEL_KEY,
                "isReasoning": True,
                "isDefault": True,
                "configuration": {
                    "model": _model(),
                    "apiKey": os.environ["TEST_AZURE_OPENAI_API_KEY"],
                    "endpoint": os.environ["TEST_AZURE_OPENAI_ENDPOINT"],
                    "deploymentName": os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"],
                },
            }
        ]
    }


@dataclass
class LiveNode(FakeNode):
    """Node's model-grant and budget routes for every attempt the rig's token route minted a token for."""

    issued: list[str] = field(default_factory=list[str])
    route: PinnedRoute | None = None

    @override
    def authorized(self, request: web.Request) -> bool:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return request.match_info["runId"] in self.grants and token in self.issued

    @override
    async def model_grant(self, request: web.Request) -> web.Response:
        self.asked += 1
        if not self.authorized(request):
            return web.json_response({"error": {"code": "AGENT_HARNESS_ATTEMPT_CLAIMS"}}, status=401)
        return web.json_response(self.grants[request.match_info["runId"]][1], headers={"Cache-Control": "no-store"})

    def grant_run(self, run_id: str, attempt_id: str) -> None:
        assert self.route is not None
        route = self.route
        self.grants[run_id] = (
            "",
            {
                "runId": run_id,
                "attemptId": attempt_id,
                "routesDigest": route_digest((route,)),
                "routes": [
                    {"provider": route.provider, "model": route.model, "modelKey": route.config_key, "modelName": route.config_model}
                ],
                "budgets": {"maxCostUsd": RUN_BUDGET_USD, "maxTokens": RUN_BUDGET_TOKENS},
                "actorId": "user-1",
                "engine": "opencode",
            },
        )
        self.budget.ledger.pin(
            RunAccountRef(ORG, run_id), PinnedBudget(Cap(usd_micros(RUN_BUDGET_USD), RUN_BUDGET_TOKENS))
        )


__all__ = ["MODEL_KEY", "ORG", "RUN_BUDGET_TOKENS", "RUN_BUDGET_USD", "LiveNode", "live_env"]
