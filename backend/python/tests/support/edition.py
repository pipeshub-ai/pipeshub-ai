"""Fixtures that pin an edition seam to the value a test's expectations assume."""

import pytest


@pytest.fixture
def org_wide_service_account_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service-account agents are always shared with the whole org."""
    monkeypatch.setattr("app.edition_services.sa_forces_org_sharing", lambda: True)


@pytest.fixture
def default_chat_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default `ChatExtension`, whatever the edition installs."""
    from app.modules.agents.chat_extension import default_chat_extension

    monkeypatch.setattr("app.edition_services.chat_extension", default_chat_extension)
