import json
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain.chat_models.base import BaseChatModel
from pydantic import BaseModel

from app.modules.agents.qna.chat_state import build_initial_state
from app.modules.agents.qna.graph import qna_graph
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.retrieval.retrieval_service import RetrievalService

router = APIRouter()


class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 50
    previousConversations: List[Dict] = []
    quickMode: bool = False
    filters: Optional[Dict[str, Any]] = None
    retrievalMode: Optional[str] = "HYBRID"


async def get_services(request: Request) -> Dict[str, Any]:
    """Get all required services from the container"""
    container = request.app.container

    # Get services
    retrieval_service = await container.retrieval_service()
    arango_service = await container.arango_service()
    reranker_service = container.reranker_service()
    config_service = container.config_service()
    logger = container.logger()

    # Get and verify LLM
    llm = retrieval_service.llm
    if llm is None:
        llm = await retrieval_service.get_llm_instance()
        if llm is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to initialize LLM service. LLM configuration is missing.",
            )

    return {
        "retrieval_service": retrieval_service,
        "arango_service": arango_service,
        "reranker_service": reranker_service,
        "config_service": config_service,
        "logger": logger,
        "llm": llm,
    }


@router.post("/agent-chat")
async def askAI(request: Request, query_info: ChatQuery) -> JSONResponse:
    """Process chat query using LangGraph agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        reranker_service = services["reranker_service"]
        retrieval_service = services["retrieval_service"]
        llm = services["llm"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
            "sendUserInfo": request.query_params.get("sendUserInfo", True),
        }

        # Build initial state
        initial_state = build_initial_state(
            query_info.model_dump(),
            user_info,
            llm,
            logger,
            retrieval_service,
            arango_service,
            reranker_service,
        )

        # Execute the graph with async
        logger.info(f"Starting LangGraph execution for query: {query_info.query}")
        final_state = await qna_graph.ainvoke(initial_state)  # Using async invoke

        # Check for errors
        if final_state.get("error"):
            error = final_state["error"]
            return JSONResponse(
                status_code=error.get("status_code", 500),
                content={
                    "status": error.get("status", "error"),
                    "message": error.get("message", error.get("detail", "An error occurred")),
                    "searchResults": [],
                    "records": [],
                },
            )

        # Return the response
        return final_state["response"]

    except HTTPException as he:
        # Re-raise HTTP exceptions with their original status codes
        raise he
    except Exception as e:
        logger.error(f"Error in askAI: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def stream_response(
    query_info: ChatQuery,
    user_info: Dict[str, Any],
    llm: BaseChatModel,
    logger: Logger,
    retrieval_service: RetrievalService,
    arango_service: ArangoService,
    reranker_service: RerankerService,
) -> AsyncGenerator[str, None]:
    # Build initial state
    initial_state = build_initial_state(
        query_info.model_dump(),
        user_info,
        llm,
        logger,
        retrieval_service,
        arango_service,
        reranker_service,
    )

    # Execute the graph with async
    logger.info(f"Starting LangGraph execution for query: {query_info.query}")
    async for chunk in qna_graph.astream(initial_state, stream_mode="custom"):
        if isinstance(chunk, dict) and "event" in chunk:
            # Convert dict to JSON string for streaming
            yield f"event: {chunk['event']}\ndata: {json.dumps(chunk['data'])}\n\n"


@router.post("/agent-chat-stream")
async def askAIStream(request: Request, query_info: ChatQuery) -> StreamingResponse:
    """Process chat query using LangGraph agent with streaming"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        reranker_service = services["reranker_service"]
        retrieval_service = services["retrieval_service"]
        llm = services["llm"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
            "sendUserInfo": request.query_params.get("sendUserInfo", True),
        }

        # Stream the response
        return StreamingResponse(
            stream_response(
                query_info, user_info, llm, logger, retrieval_service, arango_service, reranker_service
            ),
            media_type="text/event-stream",
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error in askAIStream: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
