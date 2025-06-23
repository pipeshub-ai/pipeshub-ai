# Create node functions properly designed for LangGraph
import asyncio

from app.config.utils.named_constants.arangodb_constants import (
    AccountType,
    CollectionNames,
)
from app.modules.agents.qna.chat_state import ChatState
from app.modules.qna.prompt_templates import qna_prompt
from app.utils.citations import process_citations
from app.utils.query_transform import setup_query_transformation


# 1. Decomposition Node (FIXED - made async compatible)
async def decompose_query_node(
    state: ChatState,
) -> ChatState:
    """Node to decompose the query into sub-queries"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        if not state["should_decompose"]:
            state["decomposed_queries"] = [{"query": state["query"]}]
            return state

        # Import here to avoid circular imports
        from app.utils.query_decompose import QueryDecompositionService

        # Call the async function directly
        decomposition_service = QueryDecompositionService(llm=llm, logger=logger)
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
    state: ChatState
) -> ChatState:
    """Node to transform and expand the queries"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        rewrite_chain, expansion_chain = setup_query_transformation(llm=llm)

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
) -> ChatState:
    """Node to retrieve documents based on queries"""
    try:
        logger = state["logger"]
        retrieval_service = state["retrieval_service"]
        arango_service = state["arango_service"]

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
) -> ChatState:
    """Node to fetch user and organization information"""
    try:
        logger = state["logger"]
        arango_service = state["arango_service"]

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
) -> ChatState:
    """Node to rerank the search results"""
    try:
        logger = state["logger"]
        reranker_service = state["reranker_service"]

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
) -> ChatState:
    """Node to prepare the prompt for the LLM"""
    try:
        logger = state["logger"]
        if state.get("error"):
            return state

        # Format user info if available
        user_data = ""
        if state["send_user_info"] and state["user_info"] and state["org_info"]:
            if state["org_info"].get("accountType") in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value]:
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
) -> ChatState:
    """Node to generate the answer from the LLM"""
    try:
        logger = state["logger"]
        llm = state["llm"]

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
