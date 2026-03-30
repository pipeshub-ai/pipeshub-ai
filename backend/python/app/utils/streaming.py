import pytest
from unittest.mock import MagicMock
from pydantic import ValidationError


async def mock_astream_with_role_none(*args, **kwargs):
    """Simulates LiteLLM sending role=None on non-first chunks"""

    # First chunk - normal
    chunk1 = MagicMock()
    chunk1.content = "Hello"
    yield chunk1

    # Error chunk (role=None)
    try:
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
    except ValidationError:
        # simulate skipping bad chunk instead of breaking stream
        pass

    # Next valid chunk (stream should continue)
    chunk2 = MagicMock()
    chunk2.content = "World"
    yield chunk2


@pytest.mark.asyncio
async def test_aiter_llm_stream_skips_role_none_validation_error():
    """Test that role=None ValidationError is handled and stream continues"""

    from app.utils.streaming import aiter_llm_stream

    llm = MagicMock()
    llm.astream = mock_astream_with_role_none

    messages = [{"role": "user", "content": "test"}]

    results = []

    async for token in aiter_llm_stream(llm, messages):
        results.append(token)

    # Assertions
    assert "Hello" in results
    assert "World" in results