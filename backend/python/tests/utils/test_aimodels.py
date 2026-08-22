import pytest
import app.config.ai_models.providers  # noqa: F401
from app.utils.aimodels import get_output_limit_kwargs, LLMProvider

def test_get_output_limit_kwargs_openai_compatible():
    """Test that OPENAI_COMPATIBLE gets the correct max_tokens limit mapping."""
    # Try with raw string literal
    config1 = {"provider": "openAICompatible"}
    assert get_output_limit_kwargs(config1, limit=4096) == {"max_tokens": 4096}
    
    # Try with actual enum value to strictly satisfy regression test requirement
    config2 = {"provider": LLMProvider.OPENAI_COMPATIBLE.value}
    assert get_output_limit_kwargs(config2, limit=4096) == {"max_tokens": 4096}
