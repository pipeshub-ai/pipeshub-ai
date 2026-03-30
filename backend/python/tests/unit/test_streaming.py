import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError

async def mock_astream_with_role_none(*args, **kwargs):
    """Simulates LiteLLM sending role=None on non-first chunks"""
    
    # First chunk - normal
    chunk1 = MagicMock()
    chunk1.content = "Hello"
    yield chunk1
    
    # Second chunk - role=None causes ValidationError
    raise ValidationError.from_exception_data(
        title='ChatMessageChunk',
        input_type='python',
        line_errors=[{
            'type': 'string_type',
            'loc': ('role',),
            'msg': 'Input should be a valid string',
            'input': None,
            'url': ''
        }]
    )

@pytest.mark.asyncio
async def test_aiter_llm_stream_skips_role_none_validation_error():
    """Test that role=None ValidationError is caught and skipped"""
    from app.utils.streaming import aiter_llm_stream
    
    llm = MagicMock()
    llm.astream = mock_astream_with_role_none
    
    messages = [{"role": "user", "content": "test"}]
    
    results = []
    # Should NOT raise, should skip the bad chunk
    async for token in aiter_llm_stream(llm, messages):
        results.append(token)
    
    # First chunk should still be yielded
    assert len(results) >= 1
    assert results[0] == "Hello"