"""LLM resolver for the search route."""

from fastapi import HTTPException, Request

from app.modules.retrieval.retrieval_service import RetrievalService


async def resolve_llm_for_search(request: Request, retrieval_service: RetrievalService):
    llm = retrieval_service.llm
    if llm is None:
        llm = await retrieval_service.get_llm_instance()
        if llm is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to initialize LLM service. LLM configuration is missing.",
            )
    return llm
