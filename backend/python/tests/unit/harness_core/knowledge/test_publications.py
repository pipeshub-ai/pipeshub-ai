"""P2.13 PA-1..PA-5: the publication rule (pure)."""

from __future__ import annotations

import pytest

from harness_core.knowledge.errors import PublicationAnswerOnly, PublicationInvalid
from harness_core.knowledge.memory import InMemoryPublicationDirectory
from harness_core.knowledge.publications import (
    MAX_AUDIENCE_ENTRIES,
    Admitted,
    Audience,
    Principal,
    Publication,
    PublicationDraft,
    ScopeInputs,
    admitting,
    decide_read,
    effective_scope,
    permits,
    require_raw,
    validate_draft,
)
from harness_core.testing.contracts.publications import PublicationDirectoryContract

NOW = 1_000_000
ALICE = Principal("org-1", "u-alice", frozenset({"t-hr"}))
BOB = Principal("org-1", "u-bob")
EVE = Principal("org-2", "u-alice", frozenset({"t-hr"}))


def _pub(pid: str = "p1", **overrides: object) -> Publication:
    fields: dict[str, object] = {
        "id": pid,
        "org_id": "org-1",
        "container_id": "kb-1",
        "audience": Audience(user_ids=frozenset({"u-alice"})),
        "mode": "answer_only",
        "created_by": "u-owner",
        "created_at_ms": 1,
    }
    fields.update(overrides)
    return Publication(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("audience", "principal", "expected"),
    [
        (Audience(user_ids=frozenset({"u-alice"})), ALICE, True),
        (Audience(user_ids=frozenset({"u-alice"})), BOB, False),
        (Audience(group_ids=frozenset({"t-hr"})), ALICE, True),
        (Audience(group_ids=frozenset({"t-hr"})), BOB, False),
        (Audience(org=True), BOB, True),
        (Audience(), ALICE, False),
    ],
)
def test_pa1_audience(audience: Audience, principal: Principal, expected: bool) -> None:
    assert audience.contains(principal) is expected
    assert _pub(audience=audience).admits(principal, NOW) is expected


def test_pa1_never_another_org() -> None:
    assert not _pub(audience=Audience(org=True)).admits(EVE, NOW)
    assert not _pub().admits(EVE, NOW)


@pytest.mark.parametrize(
    ("overrides", "live"),
    [
        ({}, True),
        ({"expires_at_ms": NOW + 1}, True),
        ({"expires_at_ms": NOW}, False),
        ({"expires_at_ms": NOW - 1}, False),
        ({"revoked_at_ms": NOW - 5, "revoked_by": "u-owner"}, False),
    ],
)
def test_pa2_live(overrides: dict[str, object], live: bool) -> None:
    publication = _pub(**overrides)
    assert publication.is_live(NOW) is live
    assert publication.admits(ALICE, NOW) is live


@pytest.mark.parametrize(
    ("own", "covering", "decision", "answer", "raw"),
    [
        (True, [], "own", True, True),
        (True, [_pub(mode="answer_only")], "own", True, True),
        (False, [_pub(mode="full")], "published_full", True, True),
        (False, [_pub(mode="answer_only")], "published_answer_only", True, False),
        (False, [_pub("a", mode="answer_only"), _pub("b", mode="full")], "published_full", True, True),
        (False, [_pub(mode="full", revoked_at_ms=1)], "denied", False, False),
        (False, [_pub(mode="full", audience=Audience(user_ids=frozenset({"u-bob"})))], "denied", False, False),
        (False, [], "denied", False, False),
    ],
)
def test_pa3_read_decision(own: bool, covering: list[Publication], decision: str, answer: bool, raw: bool) -> None:
    got = decide_read(own=own, covering=covering, principal=ALICE, now_ms=NOW)
    assert got == decision
    assert permits(got, "answer") is answer
    assert permits(got, "raw") is raw


def test_pa3_raw_read_of_answer_only_is_a_typed_refusal() -> None:
    with pytest.raises(PublicationAnswerOnly) as refused:
        require_raw("published_answer_only")
    assert refused.value.reason_code == "publication_answer_only"
    assert require_raw("published_full") and require_raw("own")
    assert require_raw("denied") is False


def test_pa4_interactive_scope_is_own_union_published() -> None:
    pub = _pub()
    scope = effective_scope(ScopeInputs(principal_own={"v1": "r1"}, published={"v1": ("r1", pub), "v2": ("r2", pub)}))
    assert scope == {"v1": Admitted("r1"), "v2": Admitted("r2", pub)}


def test_pa4_creator_scope_is_bounded_by_the_creator_and_the_caller() -> None:
    pub = _pub()
    scope = effective_scope(
        ScopeInputs(
            principal_own={"v1": "r1", "v2": "r2", "v3": "r3"},
            caller_own={"v1": "r1"},
            published={"v2": ("r2", pub), "v9": ("r9", pub)},
        )
    )
    # v3: the creator's only; v9: published, but beyond the creator's clearance.
    assert scope == {"v1": Admitted("r1"), "v2": Admitted("r2", pub)}


def test_pa4_an_unresolved_caller_gets_nothing() -> None:
    assert effective_scope(ScopeInputs(principal_own={"v1": "r1"}, published={}, caller_own={})) == {}


def test_pa4_admitting_is_stable_and_filtered() -> None:
    pubs = [_pub("b"), _pub("a"), _pub("c", revoked_at_ms=1), _pub("d", audience=Audience(user_ids=frozenset({"u-bob"})))]
    assert [p.id for p in admitting(ALICE, pubs, NOW)] == ["a", "b"]


def _draft(**overrides: object) -> PublicationDraft:
    fields: dict[str, object] = {
        "org_id": "org-1",
        "container_id": "kb-1",
        "audience": Audience(org=True),
        "mode": "full",
        "created_by": "u-owner",
    }
    fields.update(overrides)
    return PublicationDraft(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "read_everything"},
        {"audience": Audience()},
        {"audience": Audience(user_ids=frozenset({""}))},
        {"audience": Audience(user_ids=frozenset(f"u{i}" for i in range(MAX_AUDIENCE_ENTRIES + 1)))},
        {"expires_at_ms": NOW},
        {"container_id": ""},
        {"created_by": ""},
    ],
)
def test_pa5_invalid_drafts_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(PublicationInvalid):
        validate_draft(_draft(**overrides), NOW)


def test_pa5_a_valid_draft_passes() -> None:
    validate_draft(_draft(expires_at_ms=NOW + 1), NOW)


class TestInMemoryPublicationDirectory(PublicationDirectoryContract):
    @pytest.fixture
    def directory(self) -> InMemoryPublicationDirectory:
        return InMemoryPublicationDirectory()
