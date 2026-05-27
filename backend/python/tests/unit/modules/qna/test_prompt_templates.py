"""
Unit tests for app.modules.qna.prompt_templates

Verifies that prompt templates and response schemas are properly defined,
contain expected placeholders, and behave correctly under formatting.
"""

import pytest
from pydantic import ValidationError

from app.modules.qna.prompt_templates import (
    AnswerWithMetadataDict,
    AnswerWithMetadataJSON,
    agent_block_group_prompt,
    block_group_prompt,
    qna_prompt_context,
    qna_prompt_context_header,
    qna_prompt_instructions_1,
    qna_prompt_instructions_2,
    qna_prompt_simple,
    qna_prompt_with_retrieval_tool,
    qna_prompt_with_retrieval_tool_second_part,
    table_prompt,
    web_search_system_prompt,
    web_search_user_prompt,
)


# ---------------------------------------------------------------------------
# AnswerWithMetadataJSON
# ---------------------------------------------------------------------------

class TestAnswerWithMetadataJSON:
    def test_valid_instance(self):
        obj = AnswerWithMetadataJSON(
            answer="The policy is reviewed quarterly.",
            reason="From block ref1.",
            confidence="High",
            answerMatchType="Derived From Blocks",
        )
        assert obj.answer == "The policy is reviewed quarterly."
        assert obj.confidence == "High"
        assert obj.answerMatchType == "Derived From Blocks"

    def test_all_confidence_levels(self):
        for level in ("Very High", "High", "Medium", "Low"):
            obj = AnswerWithMetadataJSON(
                answer="x", reason="y", confidence=level,
                answerMatchType="Exact Match",
            )
            assert obj.confidence == level

    def test_all_answer_match_types(self):
        for match_type in (
            "Exact Match",
            "Derived From Blocks",
            "Derived From User Info",
            "Enhanced With Full Record",
        ):
            obj = AnswerWithMetadataJSON(
                answer="x", reason="y", confidence="High",
                answerMatchType=match_type,
            )
            assert obj.answerMatchType == match_type

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValidationError):
            AnswerWithMetadataJSON(
                answer="x", reason="y",
                confidence="Unknown",
                answerMatchType="Exact Match",
            )

    def test_invalid_answer_match_type_raises(self):
        with pytest.raises(ValidationError):
            AnswerWithMetadataJSON(
                answer="x", reason="y",
                confidence="High",
                answerMatchType="Hallucinated Type",
            )

    def test_missing_required_answer_raises(self):
        with pytest.raises(ValidationError):
            AnswerWithMetadataJSON(
                reason="y", confidence="High",
                answerMatchType="Exact Match",
            )

    def test_serialization(self):
        obj = AnswerWithMetadataJSON(
            answer="A", reason="B", confidence="Medium",
            answerMatchType="Derived From User Info",
        )
        d = obj.model_dump()
        assert d["answer"] == "A"
        assert d["confidence"] == "Medium"


# ---------------------------------------------------------------------------
# AnswerWithMetadataDict
# ---------------------------------------------------------------------------

class TestAnswerWithMetadataDict:
    def test_valid_assignment(self):
        d: AnswerWithMetadataDict = {
            "answer": "Security policies are reviewed quarterly.",
            "reason": "From block ref2.",
            "confidence": "High",
            "answerMatchType": "Derived From Blocks",
        }
        assert d["answer"] == "Security policies are reviewed quarterly."
        assert d["confidence"] == "High"

    def test_all_match_types_accepted(self):
        for match_type in (
            "Exact Match",
            "Derived From Blocks",
            "Derived From User Info",
            "Enhanced With Full Record",
        ):
            d: AnswerWithMetadataDict = {
                "answer": "x",
                "reason": "y",
                "confidence": "Low",
                "answerMatchType": match_type,
            }
            assert d["answerMatchType"] == match_type


# ---------------------------------------------------------------------------
# web_search_system_prompt
# ---------------------------------------------------------------------------

class TestWebSearchSystemPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(web_search_system_prompt, str)
        assert len(web_search_system_prompt) > 5

    def test_references_web_research(self):
        assert "web" in web_search_system_prompt.lower() or "research" in web_search_system_prompt.lower()


# ---------------------------------------------------------------------------
# web_search_user_prompt
# ---------------------------------------------------------------------------

class TestWebSearchUserPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(web_search_user_prompt, str)
        assert len(web_search_user_prompt) > 50

    def test_contains_query_placeholder(self):
        # Uses Jinja2 {{ }} style, not Python .format() style
        assert "{{ query }}" in web_search_user_prompt

    def test_contains_fetch_url_guidance(self):
        assert "fetch_url" in web_search_user_prompt

    def test_contains_web_search_tool_reference(self):
        assert "web_search" in web_search_user_prompt

    def test_contains_citation_instructions(self):
        assert "cite" in web_search_user_prompt.lower() or "citation" in web_search_user_prompt.lower()


# ---------------------------------------------------------------------------
# agent_block_group_prompt / block_group_prompt / table_prompt
# ---------------------------------------------------------------------------

class TestBlockGroupPrompts:
    def test_agent_block_group_prompt_is_string(self):
        assert isinstance(agent_block_group_prompt, str)
        assert "block_group_index" in agent_block_group_prompt
        assert "label" in agent_block_group_prompt

    def test_block_group_prompt_is_string(self):
        assert isinstance(block_group_prompt, str)
        assert "block_group_index" in block_group_prompt
        assert "label" in block_group_prompt
        assert "citation_ref" in block_group_prompt

    def test_table_prompt_is_string(self):
        assert isinstance(table_prompt, str)
        assert "block_group_index" in table_prompt
        assert "table_summary" in table_prompt
        assert "table_rows" in table_prompt


# ---------------------------------------------------------------------------
# qna_prompt_instructions_1
# ---------------------------------------------------------------------------

class TestQnaPromptInstructions1:
    def test_is_nonempty_string(self):
        assert isinstance(qna_prompt_instructions_1, str)
        assert len(qna_prompt_instructions_1) > 200

    def test_contains_fetch_full_record_tool_reference(self):
        assert "fetch_full_record" in qna_prompt_instructions_1

    def test_contains_user_data_placeholder(self):
        assert "{{ user_data }}" in qna_prompt_instructions_1

    def test_contains_query_placeholder(self):
        assert "{{ query }}" in qna_prompt_instructions_1

    def test_contains_sql_conditional_block(self):
        assert "has_sql_connector" in qna_prompt_instructions_1
        assert "execute_sql_query" in qna_prompt_instructions_1

    def test_contains_citation_instructions(self):
        assert "Citation ID" in qna_prompt_instructions_1

    def test_references_record_concept(self):
        assert "Record" in qna_prompt_instructions_1


# ---------------------------------------------------------------------------
# qna_prompt_with_retrieval_tool
# ---------------------------------------------------------------------------

class TestQnaPromptWithRetrievalTool:
    def test_is_nonempty_string(self):
        assert isinstance(qna_prompt_with_retrieval_tool, str)
        assert len(qna_prompt_with_retrieval_tool) > 200

    def test_contains_search_internal_knowledge_reference(self):
        assert "search_internal_knowledge" in qna_prompt_with_retrieval_tool

    def test_contains_user_data_placeholder(self):
        assert "{{ user_data }}" in qna_prompt_with_retrieval_tool

    def test_contains_query_placeholder(self):
        assert "{{ query }}" in qna_prompt_with_retrieval_tool

    def test_contains_attachment_conditional(self):
        assert "has_attachments" in qna_prompt_with_retrieval_tool

    def test_contains_fetch_full_record_mention(self):
        assert "fetch_full_record" in qna_prompt_with_retrieval_tool


# ---------------------------------------------------------------------------
# qna_prompt_with_retrieval_tool_second_part
# ---------------------------------------------------------------------------

class TestQnaPromptWithRetrievalToolSecondPart:
    def test_is_nonempty_string(self):
        assert isinstance(qna_prompt_with_retrieval_tool_second_part, str)
        assert len(qna_prompt_with_retrieval_tool_second_part) > 100

    def test_contains_citation_instructions(self):
        assert "Citation ID" in qna_prompt_with_retrieval_tool_second_part

    def test_contains_confidence_output_requirement(self):
        assert "Confidence" in qna_prompt_with_retrieval_tool_second_part

    def test_references_ref_format(self):
        assert "ref1" in qna_prompt_with_retrieval_tool_second_part or "refN" in qna_prompt_with_retrieval_tool_second_part


# ---------------------------------------------------------------------------
# qna_prompt_context_header / qna_prompt_context
# ---------------------------------------------------------------------------

class TestQnaPromptContext:
    def test_context_header_is_string(self):
        assert isinstance(qna_prompt_context_header, str)
        assert len(qna_prompt_context_header) > 0

    def test_context_is_string(self):
        assert isinstance(qna_prompt_context, str)

    def test_context_contains_record_tag(self):
        assert "<record>" in qna_prompt_context

    def test_context_contains_context_metadata_placeholder(self):
        assert "context_metadata" in qna_prompt_context


# ---------------------------------------------------------------------------
# qna_prompt_instructions_2
# ---------------------------------------------------------------------------

class TestQnaPromptInstructions2:
    def test_is_nonempty_string(self):
        assert isinstance(qna_prompt_instructions_2, str)
        assert len(qna_prompt_instructions_2) > 200

    def test_contains_json_mode_conditional(self):
        assert 'mode == "json"' in qna_prompt_instructions_2

    def test_contains_confidence_output_requirement(self):
        assert "Confidence" in qna_prompt_instructions_2

    def test_contains_fetch_full_record_guidance(self):
        assert "fetch_full_record" in qna_prompt_instructions_2

    def test_contains_answer_match_type_values(self):
        assert "Exact Match" in qna_prompt_instructions_2
        assert "Derived From Blocks" in qna_prompt_instructions_2

    def test_contains_json_structure_example(self):
        assert '"answer"' in qna_prompt_instructions_2
        assert '"confidence"' in qna_prompt_instructions_2


# ---------------------------------------------------------------------------
# qna_prompt_simple
# ---------------------------------------------------------------------------

class TestQnaPromptSimple:
    def test_is_nonempty_string(self):
        assert isinstance(qna_prompt_simple, str)
        assert len(qna_prompt_simple) > 100

    def test_contains_query_placeholder(self):
        assert "{{ query }}" in qna_prompt_simple

    def test_contains_chunks_loop(self):
        assert "{% for chunk in chunks %}" in qna_prompt_simple

    def test_contains_citation_id_placeholder(self):
        assert "citation_ref" in qna_prompt_simple or "Citation ID" in qna_prompt_simple

    def test_contains_block_content_placeholder(self):
        assert "blockText" in qna_prompt_simple
