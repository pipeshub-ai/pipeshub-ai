from typing import Annotated, Any, Dict, List, Optional, TypedDict, Union, Callable
import asyncio
import functools 
from pydantic import BaseModel, Field
from typing_extensions import TypedDict
from langchain.prompts import PromptTemplate
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

# Import placeholder for services that would need to be adapted
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.config.configuration_service import ConfigurationService
from app.modules.qna.prompt_templates import qna_prompt
from app.utils.citations import process_citations
from app.config.utils.named_constants.arangodb_constants import CollectionNames
from app.utils.query_transform import setup_query_transformation

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

router = APIRouter()

# Type definitions for our state
class ChatMessage(TypedDict):
    role: str
    content: str

class Document(TypedDict):
    page_content: str
    metadata: Dict[str, Any]

class ChatState(TypedDict):
    query: str
    limit: int
    messages: List[ChatMessage]
    previous_conversations: List[Dict[str, str]]
    should_decompose: bool  # Renamed from decompose_query to avoid conflict
    filters: Optional[Dict[str, Any]]
    retrieval_mode: str
    decomposed_queries: List[Dict[str, str]]
    rewritten_queries: List[str]
    expanded_queries: List[str]
    search_results: List[Document]
    final_results: List[Document]
    user_info: Optional[Dict[str, Any]]
    org_info: Optional[Dict[str, Any]]
    response: Optional[str]
    error: Optional[Dict[str, Any]]
    org_id: str
    user_id: str
    send_user_info: bool

# Define your initial state builder
def build_initial_state(chat_query: Dict[str, Any], user_info: Dict[str, Any]) -> ChatState:
    """Build the initial state from the chat query and user info"""
    return {
        "query": chat_query.get("query", ""),
        "limit": chat_query.get("limit", 50),
        "messages": [{"role": "system", "content": "You are an enterprise questions answering expert"}],
        "previous_conversations": chat_query.get("previousConversations", []),
        "should_decompose": chat_query.get("useDecomposition", True),  # Renamed
        "filters": chat_query.get("filters"),
        "retrieval_mode": chat_query.get("retrieval_mode", "HYBRID"),
        "decomposed_queries": [],
        "rewritten_queries": [],
        "expanded_queries": [],
        "search_results": [],
        "final_results": [],
        "user_info": None,
        "org_info": None,
        "response": None,
        "error": None,
        "org_id": user_info.get("orgId", ""),
        "user_id": user_info.get("userId", ""),
        "send_user_info": user_info.get("sendUserInfo", True)
    }

# Create node functions properly designed for LangGraph

# 1. Decomposition Node (FIXED - made async compatible)
async def decompose_query_node(
    state: ChatState,
    llm: Any,
    logger: Any
) -> ChatState:
    """Node to decompose the query into sub-queries"""
    try:
        if not state["should_decompose"]:
            state["decomposed_queries"] = [{"query": state["query"]}]
            return state

        # Import here to avoid circular imports
        from app.utils.query_decompose import QueryDecompositionService
        
        # Call the async function directly
        decomposition_service = QueryDecompositionService(llm, logger=logger)
        decomposition_result = await decomposition_service.decompose_query(state["query"])
        
        decomposed_queries = decomposition_result.get("queries", [])
        
        if not decomposed_queries:
            state["decomposed_queries"] = [{"query": state["query"]}]
        else:
            state["decomposed_queries"] = decomposed_queries
            
        logger.debug(f"decomposed_queries {state['decomposed_queries']}")
        return state
    except Exception as e:
        logger.error(f"Error in decomposition node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state

# 2. Query Transformation Node (FIXED - made async compatible)
async def transform_query_node(
    state: ChatState,
    llm: Any,
    logger: Any
) -> ChatState:
    """Node to transform and expand the queries"""
    try:
        rewrite_chain, expansion_chain = setup_query_transformation(llm)
        
        transformed_queries = []
        expanded_queries_set = set()
        
        for query_dict in state["decomposed_queries"]:
            query = query_dict.get("query")
            
            # Run query transformations in parallel
            rewritten_query, expanded_queries = await asyncio.gather(
                rewrite_chain.ainvoke(query), expansion_chain.ainvoke(query)
            )
            
            # Process rewritten query
            if rewritten_query.strip():
                transformed_queries.append(rewritten_query.strip())
            
            # Process expanded queries
            expanded_queries_list = [q.strip() for q in expanded_queries.split("\n") if q.strip()]
            for q in expanded_queries_list:
                if q.lower() not in expanded_queries_set:
                    expanded_queries_set.add(q.lower())
                    transformed_queries.append(q)
        
        # Remove duplicates while preserving order
        unique_queries = []
        seen = set()
        for q in transformed_queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                unique_queries.append(q)
                
        state["rewritten_queries"] = unique_queries
        return state
    except Exception as e:
        logger.error(f"Error in transformation node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state

# 3. Document Retrieval Node (FIXED - made async compatible)
async def retrieve_documents_node(
    state: ChatState,
    retrieval_service: RetrievalService,
    arango_service: ArangoService,
    logger: Any
) -> ChatState:
    """Node to retrieve documents based on queries"""
    try:
        if state.get("error"):
            return state
            
        unique_queries = state.get("rewritten_queries", [])
        if not unique_queries:
            unique_queries = [state["query"]]  # Fallback to original query
            
        results = await retrieval_service.search_with_filters(
            queries=unique_queries,
            org_id=state["org_id"],
            user_id=state["user_id"],
            limit=state["limit"],
            filter_groups=state["filters"],
            arango_service=arango_service,
        )
        
        status_code = results.get("status_code", 200)
        if status_code in [202, 500, 503]:
            state["error"] = {
                "status_code": status_code,
                "status": results.get("status", "error"),
                "message": results.get("message", "No results found"),
            }
            return state
            
        search_results = results.get("searchResults", [])
        logger.debug(f"Retrieved {len(search_results)} documents")
        
        state["search_results"] = search_results
        return state
    except Exception as e:
        logger.error(f"Error in retrieval node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state

# 4. User Data Node (FIXED - made async compatible)
async def get_user_info_node(
    state: ChatState,
    arango_service: ArangoService,
    logger: Any
) -> ChatState:
    """Node to fetch user and organization information"""
    try:
        if state.get("error") or not state["send_user_info"]:
            return state
            
        user_info = await arango_service.get_user_by_user_id(state["user_id"])
        org_info = await arango_service.get_document(
            state["org_id"], CollectionNames.ORGS.value
        )
        
        state["user_info"] = user_info
        state["org_info"] = org_info
        return state
    except Exception as e:
        logger.error(f"Error in user info node: {str(e)}", exc_info=True)
        # Don't fail the whole process if user info can't be fetched
        return state

# 5. Reranker Node (FIXED - made async compatible)
async def rerank_results_node(
    state: ChatState,
    reranker_service: RerankerService,
    logger: Any
) -> ChatState:
    """Node to rerank the search results"""
    try:
        if state.get("error"):
            return state
            
        search_results = state.get("search_results", [])
        
        # Deduplicate results based on document ID
        seen_ids = set()
        flattened_results = []
        for result in search_results:
            result_id = result["metadata"].get("_id")
            if result_id not in seen_ids:
                seen_ids.add(result_id)
                flattened_results.append(result)
                
        # Rerank if we have multiple results
        if len(flattened_results) > 1:
            final_results = await reranker_service.rerank(
                query=state["query"],  # Use original query for final ranking
                documents=flattened_results,
                top_k=state["limit"],
            )
        else:
            final_results = flattened_results
        
        logger.debug(f"Final reranked results: {len(final_results)} documents")
        state["final_results"] = final_results
        return state
    except Exception as e:
        logger.error(f"Error in reranking node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state

# 6. Prompt Creation Node (no async needed)
def prepare_prompt_node(
    state: ChatState,
    logger: Any
) -> ChatState:
    """Node to prepare the prompt for the LLM"""
    try:
        if state.get("error"):
            return state
            
        # Format user info if available
        user_data = ""
        if state["send_user_info"] and state["user_info"] and state["org_info"]:
            if state["org_info"].get("accountType") in ["enterprise", "business"]:
                user_data = (
                    "I am the user of the organization. "
                    f"My name is {state['user_info'].get('fullName', 'a user')} "
                    f"({state['user_info'].get('designation', '')}) "
                    f"from {state['org_info'].get('name', 'the organization')}. "
                    "Please provide accurate and relevant information based on the available context."
                )
            else:
                user_data = (
                    "I am the user. "
                    f"My name is {state['user_info'].get('fullName', 'a user')} "
                    f"({state['user_info'].get('designation', '')}) "
                    "Please provide accurate and relevant information based on the available context."
                )
        
        from jinja2 import Template
        template = Template(qna_prompt)
        rendered_prompt = template.render(
            user_data=user_data,
            query=state["query"],
            rephrased_queries=[],  # This keeps all query results for reference
            chunks=state["final_results"],
        )
        
        # Add conversation history to the messages
        messages = [{"role": "system", "content": "You are an enterprise questions answering expert"}]
        
        for conversation in state["previous_conversations"]:
            if conversation.get("role") == "user_query":
                messages.append({"role": "user", "content": conversation.get("content")})
            elif conversation.get("role") == "bot_response":
                messages.append({"role": "assistant", "content": conversation.get("content")})
        
        # Add current query with context
        messages.append({"role": "user", "content": rendered_prompt})
        
        state["messages"] = messages
        return state
    except Exception as e:
        logger.error(f"Error in prompt preparation node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state

# 7. Answer Generation Node (FIXED - made async compatible)
async def generate_answer_node(
    state: ChatState,
    llm: Any,
    logger: Any
) -> ChatState:
    """Node to generate the answer from the LLM"""
    try:
        if state.get("error"):
            return state
            
        # Make async LLM call
        response = await llm.ainvoke(state["messages"])
        # Process citations
        processed_response = process_citations(response, state["final_results"])
        
        state["response"] = processed_response
        return state
    except Exception as e:
        logger.error(f"Error in answer generation node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state

# Error checking function
def check_for_error(state: ChatState) -> str:
    """Check if there's an error in the state"""
    return "error" if state.get("error") else "continue"

# Define the LangGraph for QnA processing with async compatibility
# Define the LangGraph for QnA processing with async compatibility
def create_qna_graph(
    llm: Any,
    retrieval_service: RetrievalService,
    arango_service: ArangoService,
    reranker_service: RerankerService,
    logger: Any
) -> StateGraph:
    """Create the LangGraph for QnA processing"""

    # --- START CHANGES ---
    # Use functools.partial to correctly wrap async node functions
    # while preserving their awaitable nature and pre-filling arguments.

    decomp_node = functools.partial(decompose_query_node, llm=llm, logger=logger)
    transform_node = functools.partial(transform_query_node, llm=llm, logger=logger)
    retrieval_node = functools.partial(retrieve_documents_node,
                                       retrieval_service=retrieval_service,
                                       arango_service=arango_service,
                                       logger=logger)
    user_node = functools.partial(get_user_info_node,
                                  arango_service=arango_service,
                                  logger=logger)
    rerank_node = functools.partial(rerank_results_node,
                                    reranker_service=reranker_service,
                                    logger=logger)
    # Sync nodes can use lambda or partial, using partial for consistency:
    prompt_node = functools.partial(prepare_prompt_node, logger=logger)
    answer_node = functools.partial(generate_answer_node, llm=llm, logger=logger)

    # --- END CHANGES ---

    # Define the workflow as a graph
    workflow = StateGraph(ChatState)

    # Add all nodes with non-overlapping names using the partial objects
    workflow.add_node("decompose", decomp_node)
    workflow.add_node("transform", transform_node)
    workflow.add_node("retrieve", retrieval_node)
    workflow.add_node("get_user", user_node)
    workflow.add_node("rerank", rerank_node)
    workflow.add_node("prompt", prompt_node)
    workflow.add_node("answer", answer_node)

    # Add conditional edges for error handling (keep this logic as is)
    workflow.add_conditional_edges(
        "decompose",
        check_for_error,
        {
            "continue": "transform",
            "error": END
        }
    )

    workflow.add_conditional_edges(
        "transform",
        check_for_error,
        {
            "continue": "retrieve",
            "error": END
        }
    )

    workflow.add_conditional_edges(
        "retrieve",
        check_for_error,
        {
            "continue": "get_user",
            "error": END
        }
    )

    # User info node always continues to rerank
    workflow.add_edge("get_user", "rerank")

    workflow.add_conditional_edges(
        "rerank",
        check_for_error,
        {
            "continue": "prompt",
            "error": END
        }
    )

    workflow.add_conditional_edges(
        "prompt",
        check_for_error,
        {
            "continue": "answer",
            "error": END
        }
    )

    workflow.add_edge("answer", END)

    # Set the entry point
    workflow.set_entry_point("decompose")

    return workflow.compile()

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
        initial_state = build_initial_state(query_info.dict(), user_info)
        
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