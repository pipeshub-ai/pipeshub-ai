import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
# Import placeholder for services that would need to be adapted

from app.modules.agents.research.chat_state import build_initial_state
from app.modules.agents.research.graph import create_qna_graph
router = APIRouter()



class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 50
    previousConversations: List[Dict] = []
    useDecomposition: bool = True
    filters: Optional[Dict[str, Any]] = None
    retrieval_mode: Optional[str] = "HYBRID"

async def get_services(request: Request):
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
        "llm": llm
    }

@router.post("/agent-chat")
async def askAI(request: Request, query_info: ChatQuery):
    """Process chat query using LangGraph agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get('orgId'),
            "userId": request.state.user.get('userId'),
            "sendUserInfo": request.query_params.get('sendUserInfo', True)
        }

        # Create the LangGraph agent
        qna_graph = create_qna_graph(
            llm=services["llm"],
            retrieval_service=services["retrieval_service"],
            arango_service=services["arango_service"],
            reranker_service=services["reranker_service"],
            logger=logger
        )

        # Build initial state
        initial_state = build_initial_state(query_info.model_dump(), user_info)

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
                    "records": []
                }
            )

        # Return the response
        return final_state["response"]

    except HTTPException as he:
        # Re-raise HTTP exceptions with their original status codes
        raise he
    except Exception as e:
        logger.error(f"Error in askAI: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
