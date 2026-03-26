"""
Tests for query transformation utilities:
  - setup_query_transformation(llm) - Returns rewrite/expansion chains
  - setup_followup_query_transformation(llm) - Returns follow-up chain
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import Runnable

from app.utils.query_transform import (
    setup_followup_query_transformation,
    setup_query_transformation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm():
    """Create a mock LLM that behaves like a LangChain Runnable."""
    llm = MagicMock(spec=Runnable)
    # Allow pipe operator chaining
    llm.__or__ = MagicMock(return_value=MagicMock(spec=Runnable))
    llm.__ror__ = MagicMock(return_value=MagicMock(spec=Runnable))
    return llm


# ===========================================================================
# setup_query_transformation
# ===========================================================================


class TestSetupQueryTransformation:
    """Test setup_query_transformation returns correct chain structure."""

    def test_returns_tuple_of_two(self, mock_llm):
        result = setup_query_transformation(mock_llm)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_rewrite_chain_is_runnable(self, mock_llm):
        rewrite_chain, _ = setup_query_transformation(mock_llm)
        assert isinstance(rewrite_chain, Runnable)

    def test_expansion_chain_is_runnable(self, mock_llm):
        _, expansion_chain = setup_query_transformation(mock_llm)
        assert isinstance(expansion_chain, Runnable)

    def test_chains_are_different_objects(self, mock_llm):
        rewrite_chain, expansion_chain = setup_query_transformation(mock_llm)
        assert rewrite_chain is not expansion_chain

    def test_works_with_different_llm_instances(self):
        """Each LLM should produce independent chains."""
        llm1 = MagicMock(spec=Runnable)
        llm1.__or__ = MagicMock(return_value=MagicMock(spec=Runnable))
        llm1.__ror__ = MagicMock(return_value=MagicMock(spec=Runnable))

        llm2 = MagicMock(spec=Runnable)
        llm2.__or__ = MagicMock(return_value=MagicMock(spec=Runnable))
        llm2.__ror__ = MagicMock(return_value=MagicMock(spec=Runnable))

        r1, e1 = setup_query_transformation(llm1)
        r2, e2 = setup_query_transformation(llm2)

        assert r1 is not r2
        assert e1 is not e2


# ===========================================================================
# setup_followup_query_transformation
# ===========================================================================


class TestSetupFollowupQueryTransformation:
    """Test setup_followup_query_transformation returns correct chain."""

    def test_returns_runnable(self, mock_llm):
        result = setup_followup_query_transformation(mock_llm)
        assert isinstance(result, Runnable)

    def test_returns_single_chain(self, mock_llm):
        result = setup_followup_query_transformation(mock_llm)
        # Should not be a tuple
        assert not isinstance(result, tuple)

    def test_different_from_rewrite_chain(self, mock_llm):
        """Follow-up chain should be a different object than rewrite chain."""
        rewrite, _ = setup_query_transformation(mock_llm)
        followup = setup_followup_query_transformation(mock_llm)
        assert rewrite is not followup
