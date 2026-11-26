"""C-1 (PA-16): the durable run's intent call is traced to Opik as legacy traces it.

Legacy's factory traces every transport of a request (`traced_transport_factory`
behind `resolve_opik_gate`, the Opik settings the deployment already has) and
hands the intent call `opik_active`; a durable run does the same with the same
gate, so no new setting exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop.usage_transport import UsageReportingTransport
from harness_core.models.transports.opik_tracing import OpikTracingTransport
from tests.support.opik_client import opik_spans_offline

from . import world as w
from .test_parity import _durable_turn_one, _full_grant

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    # Opik is turned on by patching its gate, with no server configured: a real span would build the SDK's
    # client and send to Opik Cloud.
    with w.deployment(), opik_spans_offline():
        yield


@pytest.mark.parametrize("configured", [True, False])
async def test_c1_the_intent_call_is_traced_when_the_deployment_traces(
    configured: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents.agent_loop import router

    monkeypatch.setattr(
        "harness_core.models.transports.opik_tracing.is_opik_configured", lambda: configured
    )
    monkeypatch.setenv("OPIK_PROJECT_NAME", "pipeshub-traces")
    seen: dict[str, Any] = {}
    real = router.select_loop_and_goal

    async def spy(**kwargs: Any) -> Any:  # noqa: ANN401
        seen.update(kwargs)
        return await real(**kwargs)

    monkeypatch.setattr(router, "select_loop_and_goal", spy)
    agent = w.agent_doc()
    _ = await _durable_turn_one(w.Services(agent=agent), w.pinned_spec(**_full_grant(agent)), "react", "q")
    assert (seen["opik_active"], seen["opik_project_name"]) == (configured, "pipeshub-traces")
    intent = seen["transport_registry"].resolve("langchain")
    # Under the usage report every request transport carries (main #5548), as legacy's factory builds it.
    assert isinstance(intent, UsageReportingTransport)
    assert isinstance(intent._inner, OpikTracingTransport) is configured  # pyright: ignore[reportPrivateUsage]
