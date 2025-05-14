
import functools
from typing import Any, Dict, List, Optional
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.agents.research.chat_state import ChatState
from app.modules.agents.research.chat_state import ChatMessage, Document
from app.modules.agents.research.chat_state import ChatState
from app.modules.agents.research.nodes import check_for_error
from app.modules.agents.research.nodes import (
    decompose_query_node,
    transform_query_node,
    retrieve_documents_node,
    get_user_info_node,
    rerank_results_node,
    prepare_prompt_node,
    generate_answer_node
)

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
