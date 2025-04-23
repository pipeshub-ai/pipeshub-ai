import json
from typing import Any, Dict, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from pydantic import BaseModel, Field


class DecomposedQuery(BaseModel):
    """Schema for a single decomposed query with confidence"""

    query: str = Field(description="The decomposed sub-query")
    confidence: str = Field(
        description="Confidence level: Very High, High, Medium, or Low"
    )


class DecomposedQueries(BaseModel):
    """Schema for decomposed queries"""

    queries: List[DecomposedQuery] = Field(
        description="List of decomposed sub-queries with confidence scores"
    )
    reason: str = Field(description="Reasoning for how the query was decomposed")


class QueryDecompositionService:
    """Service for decomposing complex queries into simpler sub-queries"""

    def __init__(self, llm, logger):
        """Initialize the query decomposition service with an LLM"""
        self.llm = llm
        self.logger = logger

        # Template for query decomposition with confidence scores
        self.decomposition_template = ChatPromptTemplate.from_template(
            """You are an expert at breaking down complex questions into simpler sub-questions.

            Given the following complex query, break it down into 2-4 simpler sub-queries that together would help answer the original query.
            Focus on different aspects of the question, and make sure each sub-query is self-contained and specific.

            COMPLEX QUERY: {query}

            First explain your reasoning for how you're decomposing this query, then provide the list of sub-queries.
            For each sub-query, assign a confidence score as follows:
            - Very High: You're extremely confident this sub-query directly addresses a core aspect of the original query
            - High: You're confident this sub-query addresses an important aspect of the original query
            - Medium: This sub-query likely addresses a relevant aspect but may be tangential
            - Low: This sub-query might be relevant but you're less confident

            Format your response as a JSON object with the following structure:

            {{
                "queries": [
                    {{"query": "sub-query 1", "confidence": "Very High"}},
                    {{"query": "sub-query 2", "confidence": "High"}},
                    {{"query": "sub-query 3", "confidence": "Medium"}}
                ],
                "reason": "explanation of decomposition approach"
            }}

            For simple queries that don't need decomposition, return just 1 query in the list (the original query) with "Very High" confidence.
            "Your entire response/output is going to consist of a single JSON, and you will NOT wrap it within JSON md markers"
            """
        )

    def _parse_decomposition_response(self, response: str) -> Dict[str, Any]:
        """Parse the LLM response to extract the JSON structure with confidence scores"""
        # Extract JSON from the response
        try:
            # If response is an AIMessage object, access the content attribute directly
            if hasattr(response, "content"):
                json_str = response.content
            elif isinstance(response, dict):
                json_str = response.get("content")
            else:
                json_str = response  # Assume it's already a string
                # Parse the JSON
            parsed_json = json.loads(json_str)

            # Handle legacy format without confidence (convert to new format)
            if parsed_json.get("queries") and isinstance(parsed_json["queries"], list):
                if parsed_json["queries"] and isinstance(
                    parsed_json["queries"][0], str
                ):
                    # Convert old format ["query1", "query2"] to new format with confidence
                    parsed_json["queries"] = [
                        {"query": q, "confidence": "High"}
                        for q in parsed_json["queries"]
                    ]

            return parsed_json
        except Exception as e:
            # If parsing fails, return error
            self.logger.error("error: ", str(e))
            return {"error": str(e)}  # Return error in structured format

    async def decompose_query(self, query: str) -> Dict[str, Any]:
        """
        Decompose a complex query into simpler sub-queries with confidence scores

        Args:
            query: The complex query to decompose

        Returns:
            Dictionary with queries list (with confidence) and reason
        """
        try:
            decomposition_chain = (
                {"query": RunnablePassthrough()}
                | self.decomposition_template
                | self.llm
                | self._parse_decomposition_response
            )
            result = await decomposition_chain.ainvoke(query)

            # Validate the result
            if not result.get("queries"):
                # If no queries were returned, use the original query with Very High confidence
                result["queries"] = [{"query": query, "confidence": "Very High"}]
                if not result.get("reason"):
                    result["reason"] = "Using original query as is."

            # Validate confidence scores
            valid_confidence_levels = ["Very High", "High", "Medium", "Low"]
            for q in result["queries"]:
                if not isinstance(q, dict) or "confidence" not in q:
                    q["confidence"] = "High"  # Default confidence
                elif q["confidence"] not in valid_confidence_levels:
                    # Map invalid confidence to nearest valid level
                    q["confidence"] = self._map_to_valid_confidence(q["confidence"])

            return result
        except Exception as e:
            self.logger.error("exception", {str(e)})
            return {
                "queries": [{"query": query, "confidence": "Very High"}],
                "reason": f"Error during decomposition: {str(e)}",
            }

    def _map_to_valid_confidence(self, confidence: str) -> str:
        """Map an invalid confidence value to the nearest valid confidence level"""
        # Convert to lowercase for comparison
        confidence_lower = confidence.lower()

        # Direct mappings for common variations
        if "very high" in confidence_lower or "highest" in confidence_lower:
            return "Very High"
        elif "high" in confidence_lower:
            return "High"
        elif (
            "medium" in confidence_lower
            or "moderate" in confidence_lower
            or "mid" in confidence_lower
        ):
            return "Medium"
        elif "low" in confidence_lower:
            return "Low"

        # Default to Medium if no match
        return "Medium"
