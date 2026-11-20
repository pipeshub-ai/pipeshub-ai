"""The answer label's wire shape: what Python sends, and what a reader refuses."""

from __future__ import annotations

import pytest

from app.modules.agents.privacy.label import (
    LABEL_DIRECT_MAX,
    LABEL_VERSION,
    AgentRef,
    AnswerProvenance,
    ChatUpload,
    LabelDirect,
    LabelError,
    ParentRef,
    merge_wire,
)


def _label(**direct: object) -> AnswerProvenance:
    return AnswerProvenance(direct=LabelDirect(**direct), complete=True)


class TestWireShape:
    def test_an_empty_label_is_complete_and_public(self) -> None:
        wire = AnswerProvenance(direct=LabelDirect(), complete=True).to_wire()

        assert wire == {
            "v": LABEL_VERSION,
            "ver": "",
            "contentHash": "",
            "direct": {
                "records": [], "vrids": [], "nodes": [], "saVrids": [], "personal": [],
                "agents": [], "chat": [], "projects": [], "marks": [],
            },
            "parents": [],
            "complete": True,
            "tagset": wire["tagset"],
            "pub": True,
        }
        assert isinstance(wire["tagset"], int)

    def test_python_leaves_the_version_and_hash_for_node(self) -> None:
        wire = _label(records=("r1",)).to_wire()

        assert wire["ver"] == "" and wire["contentHash"] == ""

    def test_ids_are_sorted_and_unique(self) -> None:
        wire = _label(records=("r2", "r1", "r2"), personal=("u1",)).to_wire()

        assert wire["direct"]["records"] == ["r1", "r2"]
        assert wire["direct"]["personal"] == ["u1"]

    def test_entries_keep_their_wire_names(self) -> None:
        wire = AnswerProvenance(
            direct=LabelDirect(
                agents=(AgentRef("agent-1", "service"),),
                chat=(ChatUpload("conv-1", "up-1"),),
            ),
            parents=(ParentRef("m1", "v1", "text"), ParentRef("", "", "full", artifact_id="a1")),
            complete=True,
        ).to_wire()

        assert wire["direct"]["agents"] == [{"key": "agent-1", "kind": "service"}]
        assert wire["direct"]["chat"] == [{"conversationId": "conv-1", "recordId": "up-1"}]
        assert wire["parents"] == [
            {"id": "", "ver": "", "mode": "full", "artifactId": "a1"},
            {"id": "m1", "ver": "v1", "mode": "text"},
        ]

    def test_a_file_parent_names_the_run_that_wrote_it_and_reads_back_the_same(self) -> None:
        label = AnswerProvenance(
            direct=LabelDirect(),
            parents=(ParentRef("", "", "full", artifact_id="a1", run_id="run-0"), ParentRef("", "", "full", artifact_id="a2")),
            complete=True,
        )
        wire = label.to_wire()

        assert wire["parents"] == [
            {"id": "", "ver": "", "mode": "full", "artifactId": "a1", "runId": "run-0"},
            {"id": "", "ver": "", "mode": "full", "artifactId": "a2"},
        ]
        assert AnswerProvenance.from_wire(wire).parents == label.parents

    def test_one_file_noted_with_and_without_its_run_is_two_parents_in_a_stable_order(self) -> None:
        wire = AnswerProvenance(
            direct=LabelDirect(),
            parents=(ParentRef("", "", "full", artifact_id="a1", run_id="run-0"), ParentRef("", "", "full", artifact_id="a1")),
            complete=True,
        ).to_wire()

        assert wire["parents"] == [
            {"id": "", "ver": "", "mode": "full", "artifactId": "a1"},
            {"id": "", "ver": "", "mode": "full", "artifactId": "a1", "runId": "run-0"},
        ]

    def test_incomplete_is_sent_only_with_its_reasons(self) -> None:
        wire = AnswerProvenance(direct=LabelDirect(), complete=False, incomplete=("b", "a", "b")).to_wire()

        assert wire["complete"] is False
        assert wire["incomplete"] == ["a", "b"]
        assert "incomplete" not in _label().to_wire()

    @pytest.mark.parametrize(
        "direct",
        [
            {"records": ("r1",)}, {"nodes": ("n1",)}, {"personal": ("u1",)},
            {"agents": (AgentRef("a", "config"),)}, {"chat": (ChatUpload("c", "r"),)},
            {"projects": ("p1",)}, {"sa_vrids": ("v1",)}, {"vrids": ("v1",)},
        ],
    )
    def test_any_restriction_makes_the_label_not_public(self, direct: dict) -> None:
        assert _label(**direct).to_wire()["pub"] is False

    def test_an_incomplete_label_or_a_parent_is_never_public(self) -> None:
        assert AnswerProvenance(direct=LabelDirect(), complete=False).to_wire()["pub"] is False
        assert AnswerProvenance(
            direct=LabelDirect(), parents=(ParentRef("m1", "v1", "text"),), complete=True,
        ).to_wire()["pub"] is False


class TestReading:
    def test_a_label_reads_back_as_written(self) -> None:
        label = AnswerProvenance(
            direct=LabelDirect(records=("r1",), personal=("u1",), agents=(AgentRef("k", "config"),)),
            parents=(ParentRef("m1", "v1", "full"),),
            complete=False,
            incomplete=("unreferenced_row",),
        )

        assert AnswerProvenance.from_wire(label.to_wire()).to_wire() == label.to_wire()

    def test_an_unknown_version_is_refused(self) -> None:
        wire = _label().to_wire()
        wire["v"] = LABEL_VERSION + 1

        with pytest.raises(LabelError):
            AnswerProvenance.from_wire(wire)

    def test_an_unknown_direct_key_is_refused(self) -> None:
        wire = _label().to_wire()
        wire["direct"]["future"] = ["x"]

        with pytest.raises(LabelError):
            AnswerProvenance.from_wire(wire)

    def test_an_unknown_parent_mode_is_refused(self) -> None:
        wire = _label().to_wire()
        wire["parents"] = [{"id": "m1", "ver": "v1", "mode": "everyone"}]

        with pytest.raises(LabelError):
            AnswerProvenance.from_wire(wire)


class TestCap:
    def test_a_label_past_the_cap_is_never_truncated_it_is_incomplete(self) -> None:
        many = tuple(f"r{i}" for i in range(LABEL_DIRECT_MAX + 1))

        wire = _label(records=many).to_wire()

        assert wire["complete"] is False
        assert "ledger_over_cap" in wire["incomplete"]
        assert len(wire["direct"]["records"]) == LABEL_DIRECT_MAX + 1


class TestMerge:
    def test_two_passes_are_unioned(self) -> None:
        fast = _label(records=("r1",)).to_wire()
        strong = AnswerProvenance(
            direct=LabelDirect(records=("q1",), personal=("u1",)), complete=False, incomplete=("x",),
        ).to_wire()

        merged = merge_wire([fast, strong])

        assert merged["direct"]["records"] == ["q1", "r1"]
        assert merged["direct"]["personal"] == ["u1"]
        assert merged["complete"] is False and merged["incomplete"] == ["x"]

    def test_a_pass_with_no_label_makes_the_union_incomplete(self) -> None:
        merged = merge_wire([_label(records=("r1",)).to_wire(), None])

        assert merged["direct"]["records"] == ["r1"]
        assert merged["complete"] is False
        assert "no_final_event" in merged["incomplete"]

    def test_a_label_that_cannot_be_read_makes_the_union_incomplete(self) -> None:
        merged = merge_wire([_label(records=("r1",)).to_wire(), {"v": 99}])

        assert merged["complete"] is False
        assert "label_unknown_version" in merged["incomplete"]
