"""Shared-chat redaction attributes text to the citation that follows it (D-22
citation spans), so the answer prompts must keep telling the model to cite after
the claim. A citation that leads its sentence would leave that sentence uncited."""
from app.agents.agent_loop import prompt_builder
from app.modules.qna import prompt_templates


def test_agent_prompt_puts_citations_after_the_claim() -> None:
    rules = prompt_builder._CITATION_RULES_TEMPLATE
    assert "inline right after the key claim it supports" in rules
    assert '"Revenue grew 29% [source](ref5)."' in rules


def test_qna_prompt_puts_citations_after_the_claim() -> None:
    source = open(prompt_templates.__file__, encoding="utf-8").read()
    assert "Place citations immediately after the relevant claim." in source
