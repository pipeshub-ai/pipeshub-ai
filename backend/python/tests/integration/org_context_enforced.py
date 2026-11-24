"""Autouse fixture for the suites that run the services' own entry points: enforce mode, and no org-context violation at all."""

from collections.abc import Iterator

import pytest

from app.core import org_context


def _violations() -> float:
    return sum(s.value for m in org_context.VIOLATIONS._metric.collect() for s in m.samples if s.name.endswith("_total"))


@pytest.fixture(autouse=True)
def enforced_org_context(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Enforce (the default) refuses an access with no org or another org's; the count also catches one whose refusal the code
    swallowed, which a passing test would otherwise hide."""
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    before = _violations()
    yield
    assert _violations() == before, "an org-scoped access ran with no org or another org's (pipeshub_org_context_violations_total grew)"
