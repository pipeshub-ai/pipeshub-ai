import pytest


@pytest.fixture(autouse=True)
def _provider_queries_without_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests call provider methods directly, with mocked clients and made-up org ids, and no request or job around them, so
    no org is bound. They check query shapes and results; the org binding of every provider method is checked by
    tests/unit/core/test_org_context.py and by the integration suites, which run in enforce mode (the default)."""
    # shortcut: shadow here, not a bound org per test; bind one per test if a provider unit test ever checks org binding itself
    monkeypatch.setenv("ORG_CONTEXT_MODE", "shadow")
