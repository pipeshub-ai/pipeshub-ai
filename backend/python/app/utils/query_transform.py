from typing import Tuple

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough

from app.llm.prompt_cache.metrics import (
    detect_langchain_provider,
    log_cache_usage,
    model_name_of,
    usage_from_ai_message,
)


def _cache_usage_tap(llm, call_site: str) -> RunnableLambda:
    """Phase 0 measurement only. Inserted between `llm` and the output
    parser in a `Runnable` chain: observes the raw `AIMessage` for
    cache usage and passes it through unchanged, so it never alters the
    chain's behavior or output.
    """
    provider = detect_langchain_provider(llm)
    model = model_name_of(llm)

    def _tap(ai_message):
        try:
            log_cache_usage(
                usage_from_ai_message(
                    ai_message, provider=provider, model=model, call_site=call_site
                )
            )
        except Exception:
            pass
        return ai_message

    return RunnableLambda(_tap)


def setup_query_transformation(llm) -> Tuple[Runnable, Runnable]:
    """Setup query rewriting and expansion with async support"""

    # Query rewriting prompt
    query_rewrite_prompt = ChatPromptTemplate.from_template(
        """You are an expert at reformulating search queries to make them more effective.
        Given the original query below, rewrite it to make it more specific and detailed:

        Original Query: {query}

        Rewritten Query:"""
    )

    # Query expansion prompt
    query_expansion_prompt = ChatPromptTemplate.from_template(
        """Generate 2 additional search queries that capture different aspects or perspectives of the original query.
        These should help in retrieving a diverse set of relevant documents.

        Original Query: {query}

        Return only the list of queries, one per line without any numbering:"""
    )

    # Create async-compatible chains
    rewrite_chain = (
        {"query": RunnablePassthrough()}
        | query_rewrite_prompt
        | llm
        | _cache_usage_tap(llm, "query_rewrite")
        | StrOutputParser()
    )

    expansion_chain = (
        {"query": RunnablePassthrough()}
        | query_expansion_prompt
        | llm
        | _cache_usage_tap(llm, "query_expansion")
        | StrOutputParser()
    )

    return rewrite_chain, expansion_chain

def setup_followup_query_transformation(llm) -> Runnable:
    """Setup query rewriting for follow-up questions based on conversation history."""

    # Query rewriting prompt
    query_rewrite_prompt = ChatPromptTemplate.from_template(
        """You are an expert at reformulating search queries to make them more effective.
        Given the original query below, rewrite it to make it more specific and detailed as per the previous conversations and the follow up question
        so that it can be used to search for relevant documents:

        Previous Conversations: {previous_conversations}
        Follow up question: {query}

        Return only the rewritten query, no other text or formatting.
        Rewritten Query:"""
    )

    # Create async-compatible chains
    rewrite_chain = (
        {"query": RunnablePassthrough(), "previous_conversations": RunnablePassthrough()}
        | query_rewrite_prompt
        | llm
        | _cache_usage_tap(llm, "followup_query_rewrite")
        | StrOutputParser()
    )


    return rewrite_chain
