from typing import Tuple

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnablePassthrough


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
        | StrOutputParser()
    )

    expansion_chain = (
        {"query": RunnablePassthrough()}
        | query_expansion_prompt
        | llm
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
        | StrOutputParser()
    )


    return rewrite_chain
