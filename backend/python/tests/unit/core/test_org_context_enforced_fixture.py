"""The integration suites' violation check works with telemetry off, where the metrics backend keeps no counts."""

import pytest

from app.core import org_context
from app.plugins.telemetry.backend import NoopBackend
from tests.integration import (
    org_context_enforced,  # the module, not the name: an imported autouse fixture would run here
)


def _fixture_body(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    return org_context_enforced.enforced_org_context.__wrapped__(monkeypatch)


@pytest.mark.parametrize("telemetry", ["on", "off"])
def test_a_counted_violation_fails_the_test_whatever_the_telemetry_setting(monkeypatch: pytest.MonkeyPatch, telemetry: str) -> None:
    if telemetry == "off":   # TELEMETRY_COLLECTION_FLAG=false: the counter is the no-op backend's
        monkeypatch.setattr(org_context, "VIOLATIONS", NoopBackend().counter("v", "v", ["kind", "site"]))
    body = _fixture_body(monkeypatch)
    next(body)
    monkeypatch.setenv("ORG_CONTEXT_MODE", "shadow")     # a refusal the code swallowed: counted, let through
    org_context.ambient_org("org_x", site="fixture-check")
    with pytest.raises(AssertionError, match="no org or another org"):
        next(body)


def test_no_violation_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _fixture_body(monkeypatch)
    next(body)
    with pytest.raises(StopIteration):
        next(body)
