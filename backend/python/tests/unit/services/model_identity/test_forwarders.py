from __future__ import annotations

import json
from typing import TYPE_CHECKING, Never

import httpx
import pytest

from app.services.model_identity.call_context import ModelCallContext
from app.services.model_identity.forwarders.base import HeaderSet
from app.services.model_identity.forwarders.litellm_headers import (
    LiteLLMHeadersForwarder,
)
from app.services.model_identity.forwarders.registry import (
    ForwarderRegistry,
    build_default_registry,
    get_forwarder_registry,
    reset_forwarder_registry,
)
from app.services.model_identity.forwarders.template_headers import (
    TemplateHeadersForwarder,
    placeholder_values,
    render_template,
)
from app.services.model_identity.groups import GroupMembership
from app.services.model_identity.principal import service_principal
from app.services.model_identity.projector import IdentityProjector
from app.services.model_identity.settings import (
    ConnectionForwardingSettings,
    IdentityGovernancePolicy,
)
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from app.services.model_identity.projector import ForwardedIdentity


def identity(principal, workload=Workload.CHAT, member_ids=("g-b", "g-a"), complete=True, purpose="main", **gov) -> ForwardedIdentity:
    ctx = ModelCallContext(
        principal=principal, workload=workload, request_id="req-1", conversation_id="conv-1"
    )
    return IdentityProjector().project(
        ctx,
        IdentityGovernancePolicy.parse(gov),
        groups=GroupMembership(ids=tuple(member_ids), source="token", complete=complete),
        purpose=purpose,
    )


def conn(**raw) -> ConnectionForwardingSettings:
    return ConnectionForwardingSettings.parse({"enabled": True, **raw})


class TestLiteLLMHeaders:
    def test_golden_headers_for_a_user(self, alice) -> None:
        out = LiteLLMHeadersForwarder().render(identity(alice), conn()).as_dict()
        assert out == {
            "x-litellm-end-user-id": "u-alice",
            "x-litellm-tags": "workload:chat,org:org-1,group:g-a,group:g-b",
            "x-pipeshub-workload": "chat",
            "x-pipeshub-request-id": "req-1",
            "x-pipeshub-conversation-id": "conv-1",
            "x-pipeshub-user-groups": "g-a,g-b",
        }

    def test_no_pii_headers_by_default(self, alice) -> None:
        out = LiteLLMHeadersForwarder().render(identity(alice), conn()).as_dict()
        assert "alice" not in json.dumps(out).replace("u-alice", "")

    def test_incomplete_groups_are_flagged(self, alice) -> None:
        out = LiteLLMHeadersForwarder().render(identity(alice, complete=False), conn()).as_dict()
        assert out["x-pipeshub-groups-incomplete"] == "true"

    def test_purpose_header_only_for_sub_purposes(self, alice) -> None:
        main = LiteLLMHeadersForwarder().render(identity(alice), conn()).as_dict()
        sub = LiteLLMHeadersForwarder().render(identity(alice, purpose="compaction"), conn()).as_dict()
        assert "x-pipeshub-purpose" not in main and sub["x-pipeshub-purpose"] == "compaction"

    def test_service_identity_has_no_groups_and_uses_its_own_key(self) -> None:
        ident = identity(service_principal("indexing", "org-1"), Workload.INDEXING)
        plain = LiteLLMHeadersForwarder().render(ident, conn()).as_dict()
        assert plain["x-litellm-end-user-id"] == "svc:pipeshub:indexing"
        assert plain["x-litellm-tags"] == "workload:indexing,org:org-1"
        assert "authorization" not in plain and "x-pipeshub-groups-incomplete" not in plain
        keyed = LiteLLMHeadersForwarder().render(ident, conn(serviceApiKey="sk-svc")).as_dict()
        assert keyed["authorization"] == "Bearer sk-svc"

    def test_users_never_get_the_service_key(self, alice) -> None:
        out = LiteLLMHeadersForwarder().render(identity(alice), conn(serviceApiKey="sk-svc")).as_dict()
        assert "authorization" not in out

    def test_group_ids_cannot_inject_tags_or_headers(self, alice) -> None:
        out = LiteLLMHeadersForwarder().render(
            identity(alice, member_ids=("evil,group:admin", "x\r\nx-admin: 1")), conn()
        ).as_dict()
        assert "\r" not in json.dumps(out) and "\n" not in json.dumps(out)
        tags = out["x-litellm-tags"].split(",")
        assert not any(t == "group:admin" for t in tags)

    def test_many_groups_stay_within_the_header_cap(self, alice) -> None:
        groups = [f"{'g' * 60}{i:03d}" for i in range(500)]
        out = LiteLLMHeadersForwarder().render(
            identity(alice, member_ids=groups, groups={"maxForwarded": 500}), conn()
        ).as_dict()
        assert len(out["x-litellm-tags"]) <= 4096 and len(out["x-pipeshub-user-groups"]) <= 4096

    def test_requires_groups(self) -> None:
        assert LiteLLMHeadersForwarder().requires_groups(conn())


class TestTemplates:
    def test_placeholders(self, alice) -> None:
        ident = identity(alice, includeEmail=True)
        s = conn(
            mode="template",
            headerTemplates={
                "X-User": "{{SUBJECT}}",
                "X-Mail": "{{ EMAIL }}",
                "X-Groups": "{{GROUP_IDS}}",
                "X-Where": "{{ORG_ID}}/{{WORKLOAD}}/{{REQUEST_ID}}/{{CONVERSATION_ID}}",
            },
        )
        out = TemplateHeadersForwarder().render(ident, s).as_dict()
        assert out == {
            "x-user": "u-alice",
            "x-mail": "alice@example.com",
            "x-groups": "g-a,g-b",
            "x-where": "org-1/chat/req-1/conv-1",
        }

    def test_governance_still_applies_to_templates(self, alice) -> None:
        s = conn(mode="template", headerTemplates={"X-Mail": "{{EMAIL}}", "X-U": "{{SUBJECT}}"})
        out = TemplateHeadersForwarder().render(identity(alice), s).as_dict()
        assert "x-mail" not in out and out["x-u"] == "u-alice"

    def test_json_templates_cannot_be_broken_by_a_value(self, alice) -> None:
        evil = identity(alice, member_ids=('a"}, "admin": true, "x":"',))
        s = conn(mode="template", headerTemplates={"X-Meta": '{"u":"{{SUBJECT}}","g":"{{GROUP_IDS}}"}'})
        meta = json.loads(TemplateHeadersForwarder().render(evil, s).as_dict()["x-meta"])
        assert set(meta) == {"u", "g"}

    def test_unknown_placeholder_renders_empty(self, alice) -> None:
        assert render_template("a{{NOPE}}b", placeholder_values(identity(alice))) == "ab"

    def test_requires_groups_only_when_used(self) -> None:
        f = TemplateHeadersForwarder()
        assert not f.requires_groups(conn(mode="template", headerTemplates={"X": "{{SUBJECT}}"}))
        assert f.requires_groups(conn(mode="template", headerTemplates={"X": "{{GROUP_IDS}}"}))
        assert f.requires_groups(conn(mode="template", preset="portkey"))

    @pytest.mark.parametrize(
        "preset,header",
        [
            ("portkey", "x-portkey-metadata"),
            ("cloudflare", "cf-aig-metadata"),
        ],
    )
    def test_json_presets_are_valid_json(self, alice, preset, header) -> None:
        out = TemplateHeadersForwarder().render(identity(alice), conn(mode="template", preset=preset)).as_dict()
        parsed = json.loads(out[header])
        assert "u-alice" in parsed.values()
        assert "g-a,g-b" in parsed.values()

    def test_helicone_and_apim_presets(self, alice) -> None:
        h = TemplateHeadersForwarder().render(identity(alice), conn(mode="template", preset="helicone")).as_dict()
        assert h["helicone-user-id"] == "u-alice" and h["helicone-property-org"] == "org-1"
        a = TemplateHeadersForwarder().render(identity(alice), conn(mode="template", preset="apim")).as_dict()
        assert a["x-pipeshub-counter-key"] == "u-alice"

    def test_explicit_templates_win_over_the_preset(self, alice) -> None:
        s = conn(mode="template", preset="helicone", headerTemplates={"Helicone-User-Id": "team-{{ORG_ID}}"})
        out = TemplateHeadersForwarder().render(identity(alice), s).as_dict()
        assert out["helicone-user-id"] == "team-org-1"


class TestTemplateGroups:
    def test_a_preset_wants_groups_even_with_an_unrelated_template(self) -> None:
        forwarder = TemplateHeadersForwarder()
        settings = conn(
            mode="template", preset="helicone", headerTemplates=[{"name": "X-Team", "value": "{{ORG_ID}}"}]
        )
        assert forwarder.requires_groups(settings) is True

    def test_custom_with_only_non_group_templates_does_not(self) -> None:
        settings = conn(
            mode="template", preset="custom", headerTemplates=[{"name": "X-Org", "value": "{{ORG_ID}}"}]
        )
        assert TemplateHeadersForwarder().requires_groups(settings) is False


class TestLiteLLMTagCap:
    def test_workload_and_org_tags_survive_a_full_group_list(self, alice) -> None:
        many = tuple(f"group-{i:04d}-{'x' * 40}" for i in range(200))
        out = LiteLLMHeadersForwarder().render(identity(alice, member_ids=many), conn()).as_dict()
        tags = out["x-litellm-tags"].split(",")
        assert tags[:2] == ["workload:chat", "org:org-1"]
        assert len(out["x-litellm-tags"]) <= 4096


class TestHeaderSet:
    def test_apply_sets_and_removes(self) -> None:
        headers = httpx.Headers({"Authorization": "Bearer sdk", "x-keep": "1"})
        HeaderSet(set=(("x-new", "v"),), remove=("authorization",)).apply(headers)
        assert "authorization" not in headers and headers["x-new"] == "v" and headers["x-keep"] == "1"

    def test_apply_overrides_an_existing_header(self) -> None:
        headers = httpx.Headers({"x-litellm-end-user-id": "spoofed"})
        HeaderSet(set=(("x-litellm-end-user-id", "real"),)).apply(headers)
        assert headers.get_list("x-litellm-end-user-id") == ["real"]


class TestRegistry:
    def test_defaults(self) -> None:
        registry = build_default_registry()
        assert isinstance(registry.get(conn()), LiteLLMHeadersForwarder)
        assert isinstance(registry.get(conn(mode="template", preset="portkey")), TemplateHeadersForwarder)
        assert registry.get(conn(mode="signedJwt", jwtAudience="a")) is None

    def test_preset_specific_registration_wins(self) -> None:
        registry = build_default_registry()
        special = TemplateHeadersForwarder()
        registry.register("template", special, preset="portkey")
        assert registry.get(conn(mode="template", preset="portkey")) is special
        assert registry.get(conn(mode="template", preset="helicone")) is not special

    def test_edition_hook_can_add_a_mode(self, monkeypatch) -> None:
        from app import edition_services

        marker = object()
        monkeypatch.setattr(
            edition_services, "register_identity_forwarders", lambda r: r.register("signedJwt", marker), raising=False
        )
        reset_forwarder_registry()
        try:
            assert get_forwarder_registry().get(conn(mode="signedJwt", jwtAudience="a")) is marker
        finally:
            reset_forwarder_registry()

    def test_a_failing_edition_hook_leaves_the_oss_forwarders(self, monkeypatch) -> None:
        from app import edition_services

        def boom(_) -> Never:
            raise RuntimeError("boom")

        monkeypatch.setattr(edition_services, "register_identity_forwarders", boom, raising=False)
        reset_forwarder_registry()
        try:
            assert isinstance(get_forwarder_registry().get(conn()), LiteLLMHeadersForwarder)
        finally:
            reset_forwarder_registry()

    def test_oss_edition_defaults(self) -> None:
        from app import edition_services
        from app.services.model_identity.groups import NoopGroupResolver

        assert isinstance(edition_services.get_group_membership_resolver(None), NoopGroupResolver)
        registry = ForwarderRegistry()
        edition_services.register_identity_forwarders(registry)
        assert registry.get(conn()) is None
