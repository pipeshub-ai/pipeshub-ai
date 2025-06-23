
from logging import Logger

from langchain.chat_models.base import BaseChatModel
from langgraph.graph import END, StateGraph

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.nodes import (
    check_for_error,
    decompose_query_node,
    generate_answer_node,
    get_user_info_node,
    prepare_prompt_node,
    rerank_results_node,
    retrieve_documents_node,
    transform_query_node,
)
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.retrieval.retrieval_service import RetrievalService


def create_qna_graph(
    llm: BaseChatModel,
    retrieval_service: RetrievalService,
    arango_service: ArangoService,
    reranker_service: RerankerService,
    logger: Logger
) -> StateGraph:
    """Create the LangGraph for QnA processing"""

    workflow = StateGraph(ChatState)

    workflow.add_node("decompose", decompose_query_node)
    workflow.add_node("transform", transform_query_node)
    workflow.add_node("retrieve", retrieve_documents_node)
    workflow.add_node("get_user", get_user_info_node)
    workflow.add_node("rerank", rerank_results_node)
    workflow.add_node("prompt", prepare_prompt_node)
    workflow.add_node("answer", generate_answer_node)

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
