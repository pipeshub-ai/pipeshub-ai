import json
from typing import Any, Dict, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from pydantic import BaseModel, Field

MIN_DECOMPOSE_AND_EXPAND_QUERIES = 1
MAX_DECOMPOSE_AND_EXPAND_QUERIES = 5

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
    reason: str = Field(description="Reasoning for how the query was processed")
    operation: str = Field(description="The operation performed: decompose_and_expand, expansion, or none")


class QueryDecompositionExpansionService:
    """Service for intelligently decomposing and expanding queries using LLM-driven decisions"""

    def __init__(self, llm, logger) -> None:
        """Initialize the query decomposition service with an LLM"""
        self.llm = llm
        self.logger = logger

        # LLM-driven template for intelligent query processing
        self.decomposition_template = ChatPromptTemplate.from_template(
            """You are an expert query analyst with deep understanding of information retrieval and question answering. Your task is to intelligently analyze queries and determine the optimal processing strategy.

            QUERY TO ANALYZE: {query}

            **YOUR DECISION-MAKING PROCESS:**

            1. **ANALYZE THE QUERY:**
               - Assess complexity (simple factual vs multi-faceted)
               - Identify scope (narrow vs broad topic coverage needed)
               - Determine information depth required
               - Consider potential ambiguities or missing context

            2. **CHOOSE THE OPTIMAL STRATEGY:**

            **DECOMPOSE_AND_EXPAND** - Use when:
            - Query has multiple complex components that need individual attention
            - Query asks about relationships between different concepts
            - Query would benefit from both breaking down AND additional context
            - Query is compound (contains "and", "but", multiple questions, etc.)
            - Example: "How do climate policies affect economic growth and what strategies balance environment with development?"

            **EXPANSION** - Use when:
            - Query is focused but would benefit from broader context
            - Query is about a concept that has related important aspects
            - Query could be enhanced with background, comparisons, or implications
            - Query is clear but somewhat narrow in scope
            - Example: "What is quantum computing?" → expand to include types, applications, challenges, etc.

            **NONE** - Use when:
            - Query is already well-structured and complete
            - Query is very specific and doesn't need additional context
            - Query is procedural or instructional
            - Adding more queries would create noise rather than value
            - Example: "What is the capital of France?"

            3. **GENERATE QUERIES STRATEGICALLY:**

            For **DECOMPOSE_AND_EXPAND**:
            - Create 2-4 core decomposition queries (Very High/High confidence)
            - Add 2-4 expansion queries for context (Medium/High confidence)
            - Ensure decomposition queries cover all aspects of original query
            - Ensure expansion queries add valuable related information

            For **EXPANSION**:
            - Include the original query (Very High confidence)
            - Add 3-5 related queries (High/Medium confidence)
            - Cover: background, types/categories, applications, comparisons, implications, challenges

            For **NONE**:
            - Return only the original query (Very High confidence)

            4. **CONFIDENCE SCORING GUIDELINES:**
            - **Very High**: Essential for answering the original query
            - **High**: Important for comprehensive understanding
            - **Medium**: Valuable context but not critical
            - **Low**: Potentially relevant but uncertain value

            **OUTPUT FORMAT:**
            Provide your analysis and decision as a JSON object:

            {{
                "queries": [
                    {{"query": "query text", "confidence": "confidence level"}},
                    // ... more queries
                ],
                "reason": "Detailed explanation of your analysis and why you chose this strategy. Explain what makes this query complex/simple, what information gaps you identified, and how your chosen queries address the user's information needs.",
                "operation": "decompose_and_expand|expansion|none"
            }}

            **EXAMPLES:**

            Complex Query → DECOMPOSE_AND_EXPAND:
            Input: "How do social media algorithms affect democracy and what can be done to mitigate negative impacts while preserving free speech?"
            Analysis: This is a complex, multi-faceted query with two main components (effects on democracy + solutions) plus a constraint (preserving free speech). It needs decomposition to address each aspect thoroughly, plus expansion for crucial context.
            Output:
            {{
                "queries": [
                    {{"query": "How do social media algorithms influence democratic processes and voter behavior?", "confidence": "Very High"}},
                    {{"query": "What are the documented negative impacts of social media on democratic institutions?", "confidence": "Very High"}},
                    {{"query": "What regulatory and technical solutions exist for addressing algorithmic bias in social media?", "confidence": "High"}},
                    {{"query": "How can content moderation balance harm prevention with free speech principles?", "confidence": "High"}},
                    {{"query": "What are successful examples of social media regulation in different countries?", "confidence": "Medium"}},
                    {{"query": "How do different social media platforms' algorithms compare in terms of democratic impact?", "confidence": "Medium"}}
                ],
                "reason": "This query requires decompose_and_expand because it contains multiple complex components: algorithmic effects on democracy, solution identification, and free speech considerations. Decomposition separates these core aspects, while expansion adds crucial context about regulation examples and platform comparisons.",
                "operation": "decompose_and_expand"
            }}

            Simple Query → EXPANSION:
            Input: "What is blockchain technology?"
            Analysis: This is a straightforward definitional query, but blockchain is a complex technology that benefits from contextual understanding including applications, types, and challenges.

            Output:
            {{
                "queries": [
                    {{"query": "What is blockchain technology?", "confidence": "Very High"}},
                    {{"query": "What are the main types of blockchain networks?", "confidence": "High"}},
                    {{"query": "How does blockchain differ from traditional databases?", "confidence": "High"}},
                    {{"query": "What are the primary use cases and applications of blockchain?", "confidence": "High"}},
                    {{"query": "What are the current limitations and challenges of blockchain technology?", "confidence": "Medium"}},
                    {{"query": "How does blockchain ensure security and immutability?", "confidence": "Medium"}}
                ],
                "reason": "This query benefits from expansion because while the core question is simple, blockchain is a foundational technology that requires understanding of its types, applications, and limitations for comprehensive knowledge.",
                "operation": "expansion"
            }}

            **IMPORTANT:** Your entire response must be a single valid JSON object. Do not wrap it in markdown code blocks or add any other text.
            """
        )

    def _parse_decomposition_response(self, response: str) -> Dict[str, Any]:
        """Parse the LLM response to extract the JSON structure with confidence scores"""
        try:
            # Handle different response formats
            if hasattr(response, "content"):
                if '</think>' in response.content:
                    response.content = response.content.split('</think>')[-1]
                    response.content = response.content.strip()
                json_str = response.content
            elif isinstance(response, dict):
                if '</think>' in response.get("content", str(response)):
                    response.content = response.content.split('</think>')[-1]
                    response.content = response.content.strip()
                json_str = response.get("content", str(response))
            else:
                if '</think>' in str(response):
                    response = str(response).split('</think>')[-1]
                    response = response.strip()
                json_str = str(response)

            # Clean the JSON string
            json_str = json_str.strip()

            # Remove markdown code blocks if present
            if json_str.startswith('```json'):
                json_str = json_str[7:]
            if json_str.startswith('```'):
                json_str = json_str[3:]
            if json_str.endswith('```'):
                json_str = json_str[:-3]

            json_str = json_str.strip()

            # Parse the JSON
            parsed_json = json.loads(json_str)
            # Validate and normalize the structure
            if not isinstance(parsed_json, dict):
                raise ValueError("Response is not a JSON object")

            # Handle legacy format without confidence (convert to new format)
            if parsed_json.get("queries") and isinstance(parsed_json["queries"], list):
                for i, query in enumerate(parsed_json["queries"]):
                    if isinstance(query, str):
                        # Convert old format string to new format dict
                        parsed_json["queries"][i] = {"query": query, "confidence": "High"}
                    elif not isinstance(query, dict) or "confidence" not in query:
                        # Ensure confidence field exists
                        if isinstance(query, dict) and "query" in query:
                            query["confidence"] = "High"
                        else:
                            parsed_json["queries"][i] = {"query": str(query), "confidence": "High"}

            # Ensure required fields exist
            if "queries" not in parsed_json:
                parsed_json["queries"] = []
            if "reason" not in parsed_json:
                parsed_json["reason"] = "LLM processing completed"
            if "operation" not in parsed_json:
                # Infer operation based on number of queries
                num_queries = len(parsed_json["queries"])
                if num_queries <= MIN_DECOMPOSE_AND_EXPAND_QUERIES:
                    parsed_json["operation"] = "none"
                elif num_queries > MAX_DECOMPOSE_AND_EXPAND_QUERIES:
                    parsed_json["operation"] = "decompose_and_expand"
                else:
                    parsed_json["operation"] = "expansion"

            return parsed_json

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parsing error: {str(e)}, Response: {json_str[:200]}...")
            return {"error": f"JSON parsing failed: {str(e)}"}
        except Exception as e:
            self.logger.error(f"Response parsing error: {str(e)}")
            return {"error": f"Response parsing failed: {str(e)}"}

    async def transform_query(self, query: str) -> Dict[str, Any]:
        """
        Use LLM to intelligently analyze and process queries with decomposition/expansion

        Args:
            query: The query to analyze and process

        Returns:
            Dictionary with queries list (with confidence), reason, and operation type
        """
        try:
            try:
                self.llm.with_structured_output(DecomposedQueries)
            except NotImplementedError as e:
                self.logger.warning(f"LLM provider or api does not support structured output: {e}")

            # Create the processing chain
            decomposition_chain = (
                {"query": RunnablePassthrough()}
                | self.decomposition_template
                | self.llm
                | self._parse_decomposition_response
            )

            # Execute the chain
            result = await decomposition_chain.ainvoke(query)
            # Handle errors from parsing
            if "error" in result:
                self.logger.error(f"LLM processing failed: {result['error']}")
                return {
                    "queries": [{"query": query, "confidence": "Very High"}],
                    "reason": f"LLM processing failed: {result['error']}. Using original query.",
                    "operation": "none"
                }

            # Validate and clean the result
            result = self._validate_and_clean_result(result, query)

            return result

        except Exception as e:
            self.logger.error(f"Query processing exception: {str(e)}")
            return {
                "queries": [{"query": query, "confidence": "Very High"}],
                "reason": f"Error during LLM processing: {str(e)}. Using original query.",
                "operation": "none"
            }

    def _validate_and_clean_result(self, result: Dict[str, Any], original_query: str) -> Dict[str, Any]:
        """Validate and clean the LLM result"""

        # Ensure queries exist
        if not result.get("queries"):
            result["queries"] = [{"query": original_query, "confidence": "Very High"}]
            result["reason"] = result.get("reason", "No queries generated by LLM. Using original query.")
            result["operation"] = "none"

        # Validate confidence scores
        valid_confidence_levels = ["Very High", "High", "Medium", "Low"]
        for query_obj in result["queries"]:
            if not isinstance(query_obj, dict):
                continue

            if "confidence" not in query_obj or query_obj["confidence"] not in valid_confidence_levels:
                query_obj["confidence"] = self._map_to_valid_confidence(
                    query_obj.get("confidence", "High")
                )

        # Validate operation field
        valid_operations = ["decompose_and_expand", "expansion", "none"]
        if result.get("operation") not in valid_operations:
            # Let LLM decision stand but log warning
            self.logger.warning(f"Unexpected operation type: {result.get('operation')}")
            # Infer based on number of queries as fallback
            num_queries = len(result.get("queries", []))
            if num_queries <= MIN_DECOMPOSE_AND_EXPAND_QUERIES:
                result["operation"] = "none"
            elif num_queries > MAX_DECOMPOSE_AND_EXPAND_QUERIES:
                result["operation"] = "decompose_and_expand"
            else:
                result["operation"] = "expansion"

        # Ensure reason exists
        if not result.get("reason"):
            result["reason"] = f"LLM performed {result['operation']} operation on the query."

        return result

    def _map_to_valid_confidence(self, confidence: str) -> str:
        """Map an invalid confidence value to the nearest valid confidence level"""
        if not isinstance(confidence, str):
            return "Medium"

        confidence_lower = confidence.lower().strip()

        # Direct mappings for common variations
        if any(phrase in confidence_lower for phrase in ["very high", "highest", "maximum", "critical"]):
            return "Very High"
        elif any(phrase in confidence_lower for phrase in ["high", "important", "strong"]):
            return "High"
        elif any(phrase in confidence_lower for phrase in ["medium", "moderate", "mid", "average"]):
            return "Medium"
        elif any(phrase in confidence_lower for phrase in ["low", "weak", "uncertain"]):
            return "Low"

        # Default to Medium if no match
        return "Medium"

    async def expand_query(self, query: str) -> Dict[str, Any]:
        """
        Specifically request expansion of a query (LLM will still make the final decision)
        Args:
            query: The query to expand
        Returns:
            Dictionary with processed queries list (with confidence), reason, and operation type
        """
        # The LLM will make the intelligent decision about whether expansion is appropriate
        return await self.transform_query(query)

    async def get_query_analysis(self, query: str) -> Dict[str, Any]:
        """
        Get LLM's analysis of a query without processing it
        Args:
            query: The query to analyze
        Returns:
            Dictionary with analysis results
        """
        analysis_template = ChatPromptTemplate.from_template(
            """Analyze the following query and provide insights about its complexity, scope, and information needs:

            QUERY: {query}

            Provide your analysis as a JSON object:
            {{
                "complexity": "simple|moderate|complex",
                "scope": "narrow|broad|very_broad",
                "information_type": "factual|analytical|comparative|procedural|creative",
                "recommended_strategy": "decompose_and_expand|expansion|none",
                "reasoning": "explanation of your analysis",
                "key_concepts": ["concept1", "concept2", "..."],
                "potential_ambiguities": ["ambiguity1", "ambiguity2", "..."]
            }}
            """
        )

        try:
            analysis_chain = (
                {"query": RunnablePassthrough()}
                | analysis_template
                | self.llm
                | self._parse_decomposition_response
            )

            return await analysis_chain.ainvoke(query)
        except Exception as e:
            self.logger.error(f"Query analysis failed: {str(e)}")
            return {
                "error": str(e),
                "complexity": "unknown",
                "scope": "unknown",
                "recommended_strategy": "none"
            }
