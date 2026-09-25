import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request

# ---------------------------------------------------------
# 1 & 2. Citation Checks (Mocking the block from chatbot.py)
# ---------------------------------------------------------
# Since chatbot.py's websocket endpoint is massive, we can test the logic
# natively in a small isolated test simulating the cache bypass logic.
async def cache_validation_logic(cached_entry, graph_provider, _chat_user):
    if cached_entry:
        if isinstance(cached_entry, str):
            cached_entry = None
        elif not isinstance(cached_entry, dict) or not cached_entry.get("text"):
            cached_entry = None

    if cached_entry:
        cached_citations = cached_entry.get("citations") or []
        if not cached_citations:
            cached_entry = None
        else:
            virtual_ids_to_check = set()
            for c in cached_citations:
                meta = c.get("metadata") or {}
                vrid = meta.get("virtualRecordId")
                if not vrid:
                    cached_entry = None
                    break
                virtual_ids_to_check.add(vrid)

            if cached_entry:
                accessible_map = await graph_provider.filter_accessible_virtual_record_ids(
                    virtual_record_ids=list(virtual_ids_to_check),
                    user_id=_chat_user.get("userId"),
                    org_id=_chat_user.get("orgId"),
                )
                if len(accessible_map) != len(virtual_ids_to_check):
                    cached_entry = None

    return cached_entry

@pytest.mark.asyncio
async def test_citation_free_cache_bypass():
    graph_provider = AsyncMock()
    _chat_user = {"userId": "u1", "orgId": "o1"}
    
    # 1. Empty citations
    entry = {"text": "Answer", "citations": []}
    result = await cache_validation_logic(entry, graph_provider, _chat_user)
    assert result is None
    
    # 2. Missing virtualRecordId
    entry = {"text": "Answer", "citations": [{"metadata": {}}]}
    result = await cache_validation_logic(entry, graph_provider, _chat_user)
    assert result is None

    # 3. Denied citation
    entry = {"text": "Answer", "citations": [{"metadata": {"virtualRecordId": "v1"}}]}
    graph_provider.filter_accessible_virtual_record_ids.return_value = {} # Empty means denied
    result = await cache_validation_logic(entry, graph_provider, _chat_user)
    assert result is None
    
    # 4. Valid citation
    entry = {"text": "Answer", "citations": [{"metadata": {"virtualRecordId": "v1"}}]}
    graph_provider.filter_accessible_virtual_record_ids.return_value = {"v1": True}
    result = await cache_validation_logic(entry, graph_provider, _chat_user)
    assert result is not None


# ---------------------------------------------------------
# 3. Worker-loop shutdown path
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_worker_loop_shutdown_path():
    from app.indexing_main import lifespan
    
    mock_container = MagicMock()
    mock_gp = AsyncMock()
    mock_container._graph_provider = mock_gp
    
    mock_app = MagicMock()
    mock_app.state = MagicMock()
    mock_app.state.governor = MagicMock()
    mock_app.state.governor_task = AsyncMock()
    
    # Mock inv.close
    mock_inv = AsyncMock()
    
    mock_worker_loop = MagicMock()
    mock_worker_loop.is_running.return_value = True
    
    mock_consumer = MagicMock()
    mock_consumer.worker_loop = mock_worker_loop
    
    # We test that run_coroutine_threadsafe is called with inv.close()
    with patch("app.indexing_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container), \
         patch("app.indexing_main.recover_in_progress_records", new_callable=AsyncMock), \
         patch("app.indexing_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[("record", mock_consumer)]), \
         patch("app.indexing_main.stop_kafka_consumers", new_callable=AsyncMock), \
         patch("app.services.cache.invalidation_hooks.get_accessible_records_invalidator", return_value=mock_inv), \
         patch("asyncio.run_coroutine_threadsafe") as mock_rct, \
         patch("asyncio.wrap_future", new_callable=AsyncMock) as mock_wrap:
        
        async with lifespan(mock_app):
            pass
            
        mock_rct.assert_called()
        # Verify it was called with the worker_loop
        args, _ = mock_rct.call_args
        assert args[1] == mock_worker_loop


# ---------------------------------------------------------
# 4. Redis purge before any chat request
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_redis_purge_initializes_cache():
    from app.query_main import lifespan
    
    mock_container = MagicMock()
    mock_cache_svc = AsyncMock()
    mock_container.semantic_cache_service.return_value = mock_cache_svc
    mock_gp = AsyncMock()
    mock_container.graph_provider = AsyncMock(return_value=mock_gp)
    mock_container._graph_provider = mock_gp
    
    mock_app = MagicMock()
    mock_app.state = MagicMock()
    
    with patch("app.query_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container), \
         patch("app.query_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]), \
         patch("app.query_main.stop_kafka_consumers", new_callable=AsyncMock), \
         patch("app.agents.mcp.registry.get_mcp_registry", return_value=MagicMock()):
        
        async with lifespan(mock_app):
            # Sleep briefly to let the background task run
            await asyncio.sleep(0.1)
            
        # The background task should have called initialize
        mock_cache_svc.initialize.assert_awaited()


# ---------------------------------------------------------
# 5. KB deletion outcomes
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_kb_deletion_outcomes():
    from app.connectors.sources.localKB.api.kb_router import delete_records_in_kb
    from fastapi.exceptions import HTTPException
    
    mock_request = MagicMock(spec=Request)
    mock_request.json = AsyncMock(return_value={"recordIds": ["r1"]})
    mock_request.state.user = {"userId": "u1"}
    
    mock_gp = AsyncMock()
    mock_request.app.state.graph_provider = mock_gp
    
    mock_kb_service = AsyncMock()
    
    # Outcome 1: Successful deletion bumps corpus revision
    mock_gp.get_document.return_value = {"orgId": "org1"}
    mock_kb_service.delete_records_in_kb.return_value = {"success": True}
    
    with patch("app.connectors.sources.localKB.api.kb_router.increment_org_corpus_revision_with_retry", new_callable=AsyncMock) as mock_bump:
        await delete_records_in_kb(kb_id="kb1", request=mock_request, kb_service=mock_kb_service)
        mock_bump.assert_awaited_once_with(mock_gp, "org1")
        
    # Outcome 2: Failed lookup warns and skips bump
    mock_gp.get_document.return_value = None # Failed lookup
    mock_kb_service.delete_records_in_kb.return_value = {"success": True}
    
    with patch("app.connectors.sources.localKB.api.kb_router.increment_org_corpus_revision_with_retry", new_callable=AsyncMock) as mock_bump:
        await delete_records_in_kb(kb_id="kb1", request=mock_request, kb_service=mock_kb_service)
        mock_bump.assert_not_called()
