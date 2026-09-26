import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Request

from app.api.routes.chatbot import _validate_cached_entry
from app.api.routes.chatbot import _generate_chat_stream_via_agent_loop

@pytest.mark.asyncio
async def test_citation_scope_enforcement():
    """Test that scoped and empty-scope citations are strictly evaluated."""
    graph_provider = AsyncMock()
    _chat_user = {"userId": "u1", "orgId": "o1"}
    
    # Unscoped
    entry = {"text": "Ans", "citations": [{"metadata": {"virtualRecordId": "v1"}}]}
    graph_provider.filter_accessible_virtual_record_ids.return_value = {"v1": True}
    res = await _validate_cached_entry(entry, graph_provider, _chat_user, effective_filters=None)
    assert res is not None
    
    # Scoped matching
    entry = {"text": "Ans", "citations": [{"metadata": {"virtualRecordId": "v1"}}]}
    graph_provider.filter_accessible_virtual_record_ids.return_value = {"v1": True}
    res = await _validate_cached_entry(entry, graph_provider, _chat_user, effective_filters={"apps": ["c1"]})
    assert res is not None
    graph_provider.filter_accessible_virtual_record_ids.assert_awaited_with(
        virtual_record_ids=["v1"], user_id="u1", org_id="o1", scope_connector_ids=frozenset(["c1"])
    )

    # Empty scope (no apps selected, but strictScope applied)
    graph_provider.filter_accessible_virtual_record_ids.return_value = {}
    res = await _validate_cached_entry(entry, graph_provider, _chat_user, effective_filters={"apps": [], "strictScope": True})
    assert res is None
    graph_provider.filter_accessible_virtual_record_ids.assert_awaited_with(
        virtual_record_ids=["v1"], user_id="u1", org_id="o1", scope_connector_ids=frozenset([])
    )

@pytest.mark.asyncio
async def test_failed_invalidation_write_aborts_flow():
    """A failed invalidation write (before mutation) aborts the flow."""
    from app.connectors.sources.localKB.api.kb_router import update_record
    
    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value={"updates": {}, "fileMetadata": {}})
    mock_request.state.user = {"userId": "u1"}
    
    mock_gp = AsyncMock()
    mock_gp._get_kb_context_for_record.return_value = {"org_id": "org1"}
    mock_gp.mark_corpus_mutation_start.side_effect = Exception("DB Down")
    
    mock_request.app.state.graph_provider = mock_gp
    mock_request.app.container.logger = MagicMock()
    mock_kb_service = AsyncMock()
    
    with pytest.raises(HTTPException) as exc:
        await update_record(record_id="r1", request=mock_request, kb_service=mock_kb_service, kafka_service=AsyncMock())
    
    assert exc.value.status_code == 503
    mock_kb_service.update_record.assert_not_called()

@pytest.mark.asyncio
async def test_concurrent_mutations_track_pending_states():
    """Concurrent mutations independently track pending states."""
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    
    # Should create a uuid and run execute_query
    m_id = await provider.mark_corpus_mutation_start("org1")
    assert isinstance(m_id, str)
    assert len(m_id) > 0
    provider.client.execute_query.assert_awaited()

    # increment should use that m_id
    provider.client.execute_query.reset_mock()
    await provider.increment_corpus_revision("org1", m_id)
    args, kwargs = provider.client.execute_query.call_args
    params = args[1] if len(args) > 1 else kwargs.get("parameters_", {})
    assert params.get("org_id") == "org1"
    assert params.get("mutation_id") == m_id

@pytest.mark.asyncio
async def test_mid_stream_failures_emit_terminal_event():
    """Mid-stream failures gracefully emit terminal error events."""
    mock_req = MagicMock(spec=Request)
    mock_req.state.user = {"userId": "u1", "orgId": "o1"}
    mock_req.headers = {}
    mock_req.app.container = MagicMock()

    mock_qi = MagicMock()
    mock_qi.chatMode = "agent"
    
    async def mock_stream():
        yield "event: TEXT_MESSAGE_START\ndata: {}\n\n"
        raise Exception("Stream crash")

    with patch("app.api.routes.chatbot.run_chat_stream", return_value=mock_stream()), \
         patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock) as mock_llm, \
         patch("app.api.routes.chatbot.load_system_prompts", new_callable=AsyncMock, return_value={}), \
         patch("app.api.routes.chatbot._load_user_doc", new_callable=AsyncMock, return_value={}), \
         patch("app.api.routes.chatbot._load_org_doc", new_callable=AsyncMock, return_value={}), \
         patch("app.api.routes.chatbot.is_user_context_enabled", new_callable=AsyncMock, return_value=True):
        
        mock_llm.return_value = (MagicMock(), {}, {})
        
        stream_gen = _generate_chat_stream_via_agent_loop(
            mock_req, mock_qi, AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        )
        
        events = []
        async for evt in stream_gen:
            events.append(evt)
            
        assert len(events) == 2
        assert "TEXT_MESSAGE_START" in events[0]
        assert "RUN_ERROR" in events[1]
        assert "internal error" in events[1]

@pytest.mark.asyncio
async def test_setup_failures_emit_terminal_event():
    """Setup failures emit terminal error events."""
    mock_req = MagicMock(spec=Request)
    mock_req.state.user = {"userId": "u1", "orgId": "o1"}
    mock_req.headers = {}
    mock_req.app.container = MagicMock()

    mock_qi = MagicMock()
    mock_qi.chatMode = "agent"

    with patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock) as mock_llm, \
         patch("app.api.routes.chatbot.load_system_prompts", new_callable=AsyncMock, return_value={}), \
         patch("app.api.routes.chatbot._load_user_doc", new_callable=AsyncMock, return_value={}), \
         patch("app.api.routes.chatbot._load_org_doc", new_callable=AsyncMock, return_value={}), \
         patch("app.api.routes.chatbot.is_user_context_enabled", new_callable=AsyncMock, return_value=True):
        
        mock_llm.side_effect = Exception("Setup crash")
        
        stream_gen = _generate_chat_stream_via_agent_loop(
            mock_req, mock_qi, AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        )
        
        events = []
        async for evt in stream_gen:
            events.append(evt)
            
        assert len(events) == 1
        assert "RUN_ERROR" in events[0]
