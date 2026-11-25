"""Autouse fixture for the suites that run the services' own entry points: enforce mode, and no org-context violation at all."""

from collections.abc import Iterator

import pytest

from app.core import org_context


class _Recording:
    """Records each violation the code counts, whatever metrics backend the telemetry setting chose (a no-op one keeps no value)."""

    def __init__(self, inner: object) -> None:
        self.inner, self.seen = inner, []

    def inc(self, *labels: str, value: float = 1.0) -> None:
        self.seen.append(labels)
        self.inner.inc(*labels, value=value)


@pytest.fixture(autouse=True)
def enforced_org_context(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Enforce (the default) refuses an access with no org or another org's; the count also catches one whose refusal the code
    swallowed, which a passing test would otherwise hide."""
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    counted = _Recording(org_context.VIOLATIONS)
    monkeypatch.setattr(org_context, "VIOLATIONS", counted)
    yield
    assert counted.seen == [], f"an org-scoped access ran with no org or another org's: {counted.seen}"
