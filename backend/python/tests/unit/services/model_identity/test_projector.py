from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.model_identity import metrics
from app.services.model_identity.call_context import ModelCallContext
from app.services.model_identity.groups import GroupMembership
from app.services.model_identity.principal import (
    PrincipalKind,
    service_account_principal,
    service_principal,
    user_principal,
)
from app.services.model_identity.projector import IdentityProjector, pseudonymize
from app.services.model_identity.settings import IdentityGovernancePolicy
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from app.services.model_identity.projector import ForwardedIdentity

KEY = b"deployment-secret"


def gov(**raw) -> IdentityGovernancePolicy:
    return IdentityGovernancePolicy.parse(raw)


def ctx_for(principal, workload=Workload.CHAT, **kw) -> ModelCallContext:
    return ModelCallContext(principal=principal, workload=workload, **kw)


class TestAdminAndBackgroundActAsService:
    @pytest.mark.parametrize(
        "workload",
        [Workload.HEALTH_CHECK, Workload.DISCOVERY, Workload.INDEXING, Workload.VLM_OCR, Workload.IMAGE_DESCRIPTION],
    )
    def test_a_person_bound_to_non_interactive_work_goes_out_as_the_service(self, alice, workload) -> None:
        out = IdentityProjector().project(ctx_for(alice, workload), gov(), groups=GroupMembership(ids=("g",)))
        assert out.subject == f"svc:pipeshub:{workload.value}"
        assert out.subject_kind == "service"
        assert out.group_ids == () and out.email is None

    @pytest.mark.parametrize("workload", [Workload.CHAT, Workload.AGENT, Workload.SEARCH, Workload.SPEECH])
    def test_interactive_work_still_carries_the_person(self, alice, workload) -> None:
        out = IdentityProjector().project(ctx_for(alice, workload), gov())
        assert out.subject == "u-alice" and out.is_user


class TestPrincipals:
    def test_user_needs_ids(self) -> None:
        with pytest.raises(ValueError):
            user_principal({"email": "x@y"}, "org")
        with pytest.raises(ValueError):
            user_principal({"userId": "u"}, None)

    def test_user_principal_reads_session_fields(self, alice) -> None:
        assert alice.subject_id == "u-alice" and alice.org_id == "org-1"
        assert alice.email == "alice@example.com" and alice.display_name == "Alice A"
        assert alice.is_user

    def test_service_account_and_service(self) -> None:
        sa = service_account_principal("agent-9", "org-1")
        assert sa.kind is PrincipalKind.SERVICE_ACCOUNT and not sa.is_user
        assert service_principal().subject_id == "system"
        with pytest.raises(ValueError):
            service_account_principal("", "org-1")


class TestSubject:
    def test_default_is_the_user_id_only(self, alice) -> None:
        out = IdentityProjector().project(ctx_for(alice), gov())
        assert out.subject == "u-alice" and out.subject_kind == "user"
        assert out.email is None and out.display_name is None

    def test_subject_source_email_and_idp_object(self, alice) -> None:
        assert IdentityProjector().project(ctx_for(alice), gov(subjectSource="email")).subject == "alice@example.com"
        assert (
            IdentityProjector().project(ctx_for(alice), gov(subjectSource="idpObjectId")).subject
            == "tid:oid-alice"
        )

    def test_missing_source_field_falls_back_to_the_user_id(self, bob) -> None:
        assert IdentityProjector().project(ctx_for(bob), gov(subjectSource="idpObjectId")).subject == "u-bob"

    def test_email_and_name_only_when_governance_allows(self, alice) -> None:
        out = IdentityProjector().project(ctx_for(alice), gov(includeEmail=True, includeDisplayName=True))
        assert out.email == "alice@example.com" and out.display_name == "Alice A"

    def test_pseudonym_is_stable_and_hides_the_id(self, alice, bob) -> None:
        p = IdentityProjector(pseudonym_key=KEY)
        a1 = p.project(ctx_for(alice), gov(pseudonymize=True)).subject
        a2 = p.project(ctx_for(alice), gov(pseudonymize=True)).subject
        b = p.project(ctx_for(bob), gov(pseudonymize=True)).subject
        assert a1 == a2 != b
        assert "alice" not in a1 and a1.startswith("u_")
        assert pseudonymize("u-alice", KEY) != pseudonymize("u-alice", b"other")

    def test_pseudonymize_without_a_key_never_sends_the_raw_id(self, alice) -> None:
        out = IdentityProjector().project(ctx_for(alice), gov(pseudonymize=True))
        assert out.subject_kind == "service" and "alice" not in out.subject
        assert metrics.counter_value(metrics.IDENTITY_MISSING, "no_pseudonym_key") == 1


class TestServiceIdentities:
    def test_service_uses_the_prefix(self) -> None:
        out = IdentityProjector().project(
            ctx_for(service_principal("indexing", "org-1"), Workload.INDEXING), gov(serviceIdentityPrefix="svc:acme")
        )
        assert out.subject == "svc:acme:indexing" and out.subject_kind == "service"
        assert out.workload_class == "background" and out.org_id == "org-1"

    def test_service_account_agent(self) -> None:
        out = IdentityProjector().project(ctx_for(service_account_principal("agent-9", "org-1")), gov())
        assert out.subject == "svc:pipeshub:agent:agent-9"
        assert out.subject_kind == "service_account" and out.group_ids == ()

    def test_query_embedding_can_be_billed_to_the_service(self, alice) -> None:
        c = ctx_for(alice, Workload.QUERY_EMBEDDING)
        assert IdentityProjector().project(c, gov()).subject == "u-alice"
        out = IdentityProjector().project(c, gov(queryEmbeddingIdentity="service"))
        assert out.subject == "svc:pipeshub:query_embedding"


class TestGroups:
    def project(self, alice, membership, **raw) -> ForwardedIdentity:
        return IdentityProjector().project(ctx_for(alice), gov(**raw), groups=membership)

    def test_sorted_deduped(self, alice) -> None:
        out = self.project(alice, GroupMembership(ids=("b", "a", "b"), source="token"))
        assert out.group_ids == ("a", "b") and out.groups_complete and out.groups_source == "token"

    def test_allow_list(self, alice) -> None:
        out = self.project(alice, GroupMembership(ids=("a", "b", "c")), groups={"allowList": ["b", "x"]})
        assert out.group_ids == ("b",)

    def test_cap_marks_incomplete(self, alice) -> None:
        ids = tuple(f"g{i:03d}" for i in range(80))
        out = self.project(alice, GroupMembership(ids=ids))
        assert len(out.group_ids) == 50 and not out.groups_complete

    def test_incomplete_membership_stays_incomplete(self, alice) -> None:
        out = self.project(alice, GroupMembership(ids=("a",), complete=False))
        assert out.group_ids == ("a",) and not out.groups_complete

    def test_disabled_sends_none(self, alice) -> None:
        out = self.project(alice, GroupMembership(ids=("a",)), groups={"enabled": False})
        assert out.group_ids == () and out.groups_source == "disabled"

    def test_not_yet_resolved(self, alice) -> None:
        out = self.project(alice, None)
        assert out.group_ids == () and not out.groups_complete and out.groups_source == "pending"

    def test_services_never_carry_groups(self) -> None:
        out = IdentityProjector().project(
            ctx_for(service_principal("indexing"), Workload.INDEXING),
            gov(),
            groups=GroupMembership(ids=("a",)),
        )
        assert out.group_ids == ()


def test_values_are_sanitised(alice) -> None:
    evil = user_principal({"userId": "u\r\nx-admin: 1", "email": "a@b\n.c"}, "org\n1")
    out = IdentityProjector().project(ctx_for(evil, request_id="r\r\n1"), gov(includeEmail=True))
    for value in (out.subject, out.email, out.org_id, out.request_id):
        assert "\r" not in value and "\n" not in value


def test_request_metadata_is_carried(alice) -> None:
    out = IdentityProjector().project(
        ctx_for(alice, request_id="r1", conversation_id="c1", agent_id="a1"), gov(), purpose="compaction"
    )
    assert (out.request_id, out.conversation_id, out.agent_id, out.purpose) == ("r1", "c1", "a1", "compaction")
    assert out.workload == "chat" and out.workload_class == "interactive"
