"""Unit tests for app.modules.qna.prompt_templates."""

from app.modules.qna.prompt_templates import (
    agent_block_group_prompt,
    block_group_prompt,
    qna_prompt_context,
    qna_prompt_simple,
    table_prompt,
)


# ---------------------------------------------------------------------------
# Block group prompts
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
# qna_prompt_context
# ---------------------------------------------------------------------------

class TestQnaPromptContext:
    def test_context_is_string(self):
        assert isinstance(qna_prompt_context, str)

    def test_context_contains_record_tag(self):
        assert "<record>" in qna_prompt_context

    def test_context_contains_context_metadata_placeholder(self):
        assert "context_metadata" in qna_prompt_context


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
