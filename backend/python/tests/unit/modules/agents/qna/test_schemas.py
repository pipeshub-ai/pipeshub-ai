"""
Unit tests for app.modules.agents.qna.schemas

Tests the Pydantic model and TypedDicts for agent response schemas.
"""

import pytest
from pydantic import ValidationError

from app.modules.agents.qna.schemas import (
    AgentAnswerWithMetadataDict,
    AgentAnswerWithMetadataJSON,
    ReferenceDataItem,
)


# ---------------------------------------------------------------------------
# ReferenceDataItem
# ---------------------------------------------------------------------------

class TestReferenceDataItem:
    def test_all_fields_optional(self):
        # total=False means all fields are optional
        item: ReferenceDataItem = {}
        assert item == {}

    def test_with_all_fields(self):
        item: ReferenceDataItem = {
            "name": "My Doc",
            "id": "doc-123",
            "type": "FILE",
            "app": "drive",
            "webUrl": "https://example.com/doc",
            "metadata": {"key": "PROJ-42", "siteId": "site-1"},
        }
        assert item["name"] == "My Doc"
        assert item["id"] == "doc-123"
        assert item["type"] == "FILE"
        assert item["app"] == "drive"
        assert item["webUrl"] == "https://example.com/doc"
        assert item["metadata"]["key"] == "PROJ-42"

    def test_partial_fields(self):
        item: ReferenceDataItem = {"name": "Ticket", "id": "PROJ-1"}
        assert item["name"] == "Ticket"
        assert "type" not in item


# ---------------------------------------------------------------------------
# AgentAnswerWithMetadataJSON
# ---------------------------------------------------------------------------

class TestAgentAnswerWithMetadataJSON:
    def test_valid_full_instance(self):
        obj = AgentAnswerWithMetadataJSON(
            answer="The answer is 42.",
            reason="Based on context block ref1.",
            confidence="High",
            answerMatchType="Derived From Blocks",
        )
        assert obj.answer == "The answer is 42."
        assert obj.reason == "Based on context block ref1."
        assert obj.confidence == "High"
        assert obj.answerMatchType == "Derived From Blocks"

    def test_reason_is_optional(self):
        obj = AgentAnswerWithMetadataJSON(
            answer="Yes",
            confidence="Medium",
            answerMatchType="Exact Match",
        )
        assert obj.reason is None

    def test_reference_data_defaults_to_none(self):
        obj = AgentAnswerWithMetadataJSON(
            answer="No",
            confidence="Low",
            answerMatchType="Derived From User Info",
        )
        assert obj.referenceData is None

    def test_reference_data_can_be_list_of_dicts(self):
        ref = [{"name": "Doc A", "id": "123", "type": "FILE"}]
        obj = AgentAnswerWithMetadataJSON(
            answer="Found it",
            confidence="Very High",
            answerMatchType="Derived From Tool Execution",
            referenceData=ref,
        )
        assert obj.referenceData == ref

    def test_all_confidence_values(self):
        for level in ("Very High", "High", "Medium", "Low"):
            obj = AgentAnswerWithMetadataJSON(
                answer="x",
                confidence=level,
                answerMatchType="Exact Match",
            )
            assert obj.confidence == level

    def test_all_answer_match_types(self):
        valid_types = [
            "Exact Match",
            "Derived From Blocks",
            "Derived From User Info",
            "Enhanced With Full Record",
            "Derived From Tool Execution",
        ]
        for match_type in valid_types:
            obj = AgentAnswerWithMetadataJSON(
                answer="x",
                confidence="High",
                answerMatchType=match_type,
            )
            assert obj.answerMatchType == match_type

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValidationError):
            AgentAnswerWithMetadataJSON(
                answer="x",
                confidence="Ultra",  # not a valid Literal
                answerMatchType="Exact Match",
            )

    def test_invalid_answer_match_type_raises(self):
        with pytest.raises(ValidationError):
            AgentAnswerWithMetadataJSON(
                answer="x",
                confidence="High",
                answerMatchType="Unknown Type",
            )

    def test_missing_required_answer_raises(self):
        with pytest.raises(ValidationError):
            AgentAnswerWithMetadataJSON(
                confidence="High",
                answerMatchType="Exact Match",
            )

    def test_missing_required_confidence_raises(self):
        with pytest.raises(ValidationError):
            AgentAnswerWithMetadataJSON(
                answer="x",
                answerMatchType="Exact Match",
            )

    def test_serialization_to_dict(self):
        obj = AgentAnswerWithMetadataJSON(
            answer="Yes",
            reason="From ref1",
            confidence="High",
            answerMatchType="Derived From Blocks",
        )
        d = obj.model_dump()
        assert d["answer"] == "Yes"
        assert d["confidence"] == "High"
        assert d["answerMatchType"] == "Derived From Blocks"


# ---------------------------------------------------------------------------
# AgentAnswerWithMetadataDict
# ---------------------------------------------------------------------------

class TestAgentAnswerWithMetadataDict:
    def test_valid_assignment(self):
        d: AgentAnswerWithMetadataDict = {
            "answer": "Here is what I found.",
            "reason": "Derived from block ref2.",
            "confidence": "Very High",
            "answerMatchType": "Derived From Tool Execution",
        }
        assert d["answer"] == "Here is what I found."
        assert d["confidence"] == "Very High"

    def test_reference_data_field(self):
        ref: list[ReferenceDataItem] = [{"name": "Jira ticket", "id": "PROJ-1", "type": "TICKET"}]
        d: AgentAnswerWithMetadataDict = {
            "answer": "Ticket found",
            "reason": "Tool returned result",
            "confidence": "High",
            "answerMatchType": "Derived From Tool Execution",
            "referenceData": ref,
        }
        assert d["referenceData"][0]["id"] == "PROJ-1"

    def test_all_answer_match_types_are_defined(self):
        # Verify all five match types are accepted by the TypedDict annotation
        valid_types = [
            "Exact Match",
            "Derived From Blocks",
            "Derived From User Info",
            "Enhanced With Full Record",
            "Derived From Tool Execution",
        ]
        for match_type in valid_types:
            d: AgentAnswerWithMetadataDict = {
                "answer": "x",
                "reason": "",
                "confidence": "Low",
                "answerMatchType": match_type,
            }
            assert d["answerMatchType"] == match_type

    def test_total_false_allows_partial(self):
        # total=False means no keys are required at runtime
        d: AgentAnswerWithMetadataDict = {}
        assert d == {}
