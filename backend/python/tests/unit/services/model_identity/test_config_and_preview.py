"""Registry fields, flat settings, and the admin preview."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.config.ai_models.providers  # noqa: F401
from app.api.routes.ai_models_registry import router
from app.config.ai_models.registry import ai_model_registry
from app.services.model_identity.groups import GroupMembership
from app.services.model_identity.preview import REDACTED, build_preview, redact_headers
from app.services.model_identity.principal import service_principal
from app.services.model_identity.settings import (
    DEFAULT_GOVERNANCE,
    ConnectionForwardingSettings,
)
from app.services.model_identity.transport.instrument import settings_from_config
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from starlette.responses import Response

GATEWAYS = ("litellmProxy", "openAICompatible", "openRouter")
FLAT = (
    "identityForwardingEnabled",
    "identityForwardingMode",
    "identityForwardingPreset",
    "identityForwardingServiceApiKey",
)


@pytest.mark.parametrize("provider_id", GATEWAYS)
def test_gateway_providers_offer_the_forwarding_fields_for_every_capability(provider_id) -> None:
    by_capability = ai_model_registry.get_provider_schema(provider_id)["fields"]
    assert len(by_capability) == 5
    for capability, fields in by_capability.items():
        assert set(FLAT) <= {f["name"] for f in fields}, capability


def test_a_provider_that_is_not_a_gateway_does_not_offer_them() -> None:
    assert "identityForwardingEnabled" not in str(ai_model_registry.get_provider_schema("anthropic"))


def test_the_service_key_is_a_secret_field() -> None:
    fields = ai_model_registry.get_provider_schema("litellmProxy", "text_generation")["fields"]
    field = next(f for f in _flatten(fields) if f["name"] == "identityForwardingServiceApiKey")
    assert field["isSecret"] is True


def _flatten(fields) -> list[object]:
    if isinstance(fields, dict):
        return [f for group in fields.values() for f in group]
    return fields


class TestFlatSettings:
    def test_flat_keys_become_settings(self) -> None:
        settings = settings_from_config(
            {
                "configuration": {
                    "identityForwardingEnabled": True,
                    "identityForwardingMode": "template",
                    "identityForwardingPreset": "portkey",
                }
            }
        )
        assert settings.enabled is True
        assert settings.mode == "template"
        assert settings.preset == "portkey"

    def test_the_nested_object_wins(self) -> None:
        settings = settings_from_config(
            {
                "configuration": {
                    "identityForwardingEnabled": False,
                    "identityForwarding": {"enabled": True},
                }
            }
        )
        assert settings.enabled is True

    def test_nothing_set_means_off(self) -> None:
        assert settings_from_config({"configuration": {"endpoint": "x"}}).enabled is False

    def test_an_empty_service_key_is_ignored(self) -> None:
        settings = settings_from_config(
            {"configuration": {"identityForwardingEnabled": True, "identityForwardingServiceApiKey": ""}}
        )
        assert settings.enabled is True


class TestBuildPreview:
    def _preview(self, alice, **settings) -> dict[str, object]:
        return build_preview(
            principal=alice,
            workload=Workload.CHAT,
            settings=ConnectionForwardingSettings.parse({"enabled": True, **settings}),
            governance=DEFAULT_GOVERNANCE,
            pseudonym_key=None,
            groups=GroupMembership(ids=("g1", "g2"), source="token"),
        )

    def test_shows_the_headers_the_hook_would_send(self, alice) -> None:
        preview = self._preview(alice)
        assert preview["headers"]["x-litellm-end-user-id"] == "u-alice"
        assert "g1" in preview["headers"]["x-pipeshub-user-groups"]
        assert preview["identity"]["groups"] == ["g1", "g2"]

    def test_off_means_no_headers(self, alice) -> None:
        preview = build_preview(
            principal=alice,
            workload=Workload.CHAT,
            settings=ConnectionForwardingSettings(),
            governance=DEFAULT_GOVERNANCE,
            pseudonym_key=None,
            groups=None,
        )
        assert preview["headers"] is None
        assert preview["notes"]

    def test_a_background_call_previews_as_the_service(self) -> None:
        preview = build_preview(
            principal=service_principal("indexing", "org-1"),
            workload=Workload.INDEXING,
            settings=ConnectionForwardingSettings.parse({"enabled": True}),
            governance=DEFAULT_GOVERNANCE,
            pseudonym_key=None,
            groups=None,
        )
        assert preview["identity"]["subjectKind"] == "service"
        assert preview["identity"]["subject"].startswith("svc:pipeshub")

    def test_no_groups_is_called_out(self, alice) -> None:
        preview = build_preview(
            principal=alice,
            workload=Workload.CHAT,
            settings=ConnectionForwardingSettings.parse({"enabled": True}),
            governance=DEFAULT_GOVERNANCE,
            pseudonym_key=None,
            groups=None,
        )
        assert any("No groups" in n for n in preview["notes"])

    def test_the_service_key_never_appears(self) -> None:
        preview = build_preview(
            principal=service_principal("indexing", "org-1"),
            workload=Workload.INDEXING,
            settings=ConnectionForwardingSettings.parse(
                {"enabled": True, "serviceApiKey": "sk-secret-123"}
            ),
            governance=DEFAULT_GOVERNANCE,
            pseudonym_key=None,
            groups=None,
        )
        assert "sk-secret-123" not in str(preview)

    def test_redaction(self) -> None:
        out = redact_headers({"Authorization": "Bearer x", "x-api-key": "k", "x-litellm-end-user-id": "u"})
        assert out["Authorization"] == REDACTED
        assert out["x-api-key"] == REDACTED
        assert out["x-litellm-end-user-id"] == "u"


def _client(user, graph_user=None, groups=("g1",)) -> tuple[TestClient, SimpleNamespace]:
    app = FastAPI()
    container = MagicMock()
    container.config_service.return_value = MagicMock()
    provider = MagicMock()
    provider.get_user_by_user_id = AsyncMock(return_value=graph_user)
    container.graph_provider = AsyncMock(return_value=provider)
    app.container = container

    @app.middleware("http")
    async def authenticated(request, call_next) -> Response:
        request.state.user = user
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    runtime = SimpleNamespace(
        governance=AsyncMock(return_value=DEFAULT_GOVERNANCE),
        pseudonym_key=AsyncMock(return_value=None),
        resolve_groups=AsyncMock(return_value=GroupMembership(ids=tuple(groups), source="token")),
    )
    return TestClient(app), runtime


ADMIN = {"userId": "u-admin", "orgId": "org-1", "token_type": "regular", "email": "a@x.io"}
BODY = {"workload": "chat", "configuration": {"identityForwardingEnabled": True}}


def _as_admin(is_admin=True) -> AbstractContextManager[object]:
    return patch("app.edition_config.check_user_is_admin", AsyncMock(return_value=is_admin))


def test_preview_is_admin_only() -> None:
    client, runtime = _client(ADMIN)
    with _as_admin(False), patch(
        "app.services.model_identity.runtime.get_identity_runtime", return_value=runtime
    ):
        assert client.post("/api/v1/ai-models/identity/preview", json=BODY).status_code == 403


def test_preview_defaults_to_the_caller() -> None:
    client, runtime = _client(ADMIN)
    with _as_admin(), patch(
        "app.services.model_identity.runtime.get_identity_runtime", return_value=runtime
    ):
        resp = client.post("/api/v1/ai-models/identity/preview", json=BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["identity"]["subject"] == "u-admin"
    assert data["headers"]["x-litellm-end-user-id"] == "u-admin"


def test_preview_for_another_user_in_the_same_org() -> None:
    client, runtime = _client(ADMIN, graph_user={"orgId": "org-1", "email": "bob@x.io"})
    with _as_admin(), patch(
        "app.services.model_identity.runtime.get_identity_runtime", return_value=runtime
    ):
        resp = client.post("/api/v1/ai-models/identity/preview", json={**BODY, "userId": "u-bob"})
    assert resp.status_code == 200
    assert resp.json()["identity"]["subject"] == "u-bob"


def test_preview_refuses_a_user_from_another_org() -> None:
    client, runtime = _client(ADMIN, graph_user={"orgId": "org-2"})
    with _as_admin(), patch(
        "app.services.model_identity.runtime.get_identity_runtime", return_value=runtime
    ):
        resp = client.post("/api/v1/ai-models/identity/preview", json={**BODY, "userId": "u-eve"})
    assert resp.status_code == 404


def test_preview_rejects_an_unknown_workload() -> None:
    client, runtime = _client(ADMIN)
    with _as_admin(), patch(
        "app.services.model_identity.runtime.get_identity_runtime", return_value=runtime
    ):
        resp = client.post("/api/v1/ai-models/identity/preview", json={**BODY, "workload": "nope"})
    assert resp.status_code == 422


def test_preview_refuses_a_user_whose_org_was_never_recorded() -> None:
    client, runtime = _client(ADMIN, graph_user={"email": "ghost@x.io"})
    with _as_admin(), patch(
        "app.services.model_identity.runtime.get_identity_runtime", return_value=runtime
    ):
        resp = client.post("/api/v1/ai-models/identity/preview", json={**BODY, "userId": "u-ghost"})
    assert resp.status_code == 404


def test_preview_of_a_health_check_is_the_service_not_the_admin() -> None:
    client, runtime = _client(ADMIN)
    with _as_admin(), patch(
        "app.services.model_identity.runtime.get_identity_runtime", return_value=runtime
    ):
        resp = client.post("/api/v1/ai-models/identity/preview", json={**BODY, "workload": "health_check"})
    assert resp.status_code == 200
    assert resp.json()["identity"]["subject"] == "svc:pipeshub:health_check"
