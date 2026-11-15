"""LLM resolver for the search route."""

from fastapi import Request

from app.modules.retrieval.retrieval_service import RetrievalService


async def resolve_llm_for_search(request: Request, retrieval_service: RetrievalService):
    """The chat model that rewrites the query, or None when none is configured."""
    llm = retrieval_service.llm
    if llm is None:
        llm = await retrieval_service.get_llm_instance()
    return llm
