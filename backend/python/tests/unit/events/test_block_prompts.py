"""Test that block_prompts module loads and contains the expected prompt template."""

def test_block_extraction_prompt_exists():
    from app.events.block_prompts import block_extraction_prompt
    assert isinstance(block_extraction_prompt, str)
    assert "sections" in block_extraction_prompt
    assert "contiguous" in block_extraction_prompt.lower()
    assert len(block_extraction_prompt) > 100
