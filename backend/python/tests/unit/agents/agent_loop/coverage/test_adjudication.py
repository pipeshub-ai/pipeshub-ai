"""Source adjudication: scope first, then revision, then the named document type."""

from __future__ import annotations

from app.agents.agent_loop.coverage.adjudication import (
    SET_ASIDE_DOC_TYPE,
    SET_ASIDE_REVISED,
    SET_ASIDE_SCOPE,
    Conflict,
    ConflictValue,
    adjudicate,
    draft_disagrees,
)


def _value(record: str, value: str, **kwargs) -> ConflictValue:
    return ConflictValue(record=record, value=value, **kwargs)


class TestAdjudicate:
    def test_a_record_of_another_scope_is_not_a_rival(self) -> None:
        conflict = Conflict(facet="f1", draft_leads_with=["R2"], values=[
            _value("R1", "30 minutes", scope="Enterprise P1"),
            _value("R2", "hourly", scope="Starter tier", same_scope_as_question=False),
        ])
        ruling = adjudicate(conflict)
        assert ruling is not None
        assert ruling.preferred == ["R1"]
        assert ruling.set_aside == [("R2", SET_ASIDE_SCOPE)]
        assert draft_disagrees(ruling, conflict)

    def test_same_scope_without_revision_or_named_type_is_left_to_the_answer_rules(self) -> None:
        conflict = Conflict(facet="f1", values=[_value("R1", "30 days"), _value("R2", "90 days")])
        assert adjudicate(conflict) is None

    def test_the_named_document_type_wins_over_a_majority(self) -> None:
        conflict = Conflict(facet="f1", draft_leads_with=["R2"], values=[
            _value("R1", "30 days", doc_type="access playbook"),
            _value("R2", "90 days", doc_type="security framework"),
            _value("R3", "90 days", doc_type="wiki page"),
        ])
        ruling = adjudicate(conflict, ["the playbook"])
        assert ruling is not None
        assert ruling.preferred == ["R1"]
        assert {label for label, why in ruling.set_aside if why == SET_ASIDE_DOC_TYPE} == {"R2", "R3"}
        assert draft_disagrees(ruling, conflict)

    def test_a_revision_beats_what_it_revises(self) -> None:
        conflict = Conflict(facet="f1", draft_leads_with=["R1"], values=[
            _value("R1", "v1 limit 100"),
            _value("R2", "v2 limit 150", revises=["R1"]),
        ])
        ruling = adjudicate(conflict)
        assert ruling is not None
        assert ruling.preferred == ["R2"]
        assert ruling.set_aside == [("R1", SET_ASIDE_REVISED)]

    def test_no_record_of_the_question_scope_decides_nothing(self) -> None:
        conflict = Conflict(values=[
            _value("R1", "a", same_scope_as_question=False), _value("R2", "b", same_scope_as_question=False),
        ])
        assert adjudicate(conflict) is None

    def test_a_draft_that_already_leads_with_the_preferred_record_is_left_alone(self) -> None:
        conflict = Conflict(facet="f1", draft_leads_with=["R1"], draft_mentions=["R2"], values=[
            _value("R1", "30 days", doc_type="playbook"), _value("R2", "90 days", doc_type="framework"),
        ])
        ruling = adjudicate(conflict, ["playbook"])
        assert ruling is not None
        # Mentioning a same-scope record of another type as a labelled difference is fine.
        assert not draft_disagrees(ruling, conflict)

    def test_a_draft_that_cites_another_scope_as_a_version_disagrees(self) -> None:
        conflict = Conflict(facet="f1", draft_leads_with=["R1"], draft_mentions=["R2"], values=[
            _value("R1", "15 minutes"), _value("R2", "hourly", same_scope_as_question=False),
        ])
        ruling = adjudicate(conflict)
        assert ruling is not None and draft_disagrees(ruling, conflict)
