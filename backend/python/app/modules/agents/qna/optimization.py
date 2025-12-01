"""
Optimization Module - LLM and Data Optimization for Agent Performance

This module provides optimization utilities for:
1. Data optimization - removing metadata, compressing JSON
2. Prompt optimization - optimizing message history and system prompts
3. LLM optimization - efficient LLM invocation with timeout protection

All optimizations preserve essential data while improving performance.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)


class DataOptimizer:
    """
    Optimizes data payloads while preserving essential information.

    Removes verbose metadata and internal fields that LLMs don't need,
    while keeping all user-facing data intact.
    """

    # Fields safe to remove (not needed by LLM)
    METADATA_FIELDS = {
        'metadata', 'debug', 'trace', 'raw', 'internal',
        'cache_info', 'request_id', 'correlation_id', 'trace_id',
        '__v', 'modified_at', 'last_modified', 'etag', 'version'
    }

    # Fields to always preserve (essential for responses)
    ESSENTIAL_FIELDS = {
        'id', 'name', 'title', 'content', 'text', 'body', 'message',
        'description', 'summary', 'value', 'data', 'results',
        'items', 'list', 'channels', 'messages', 'users', 'issues'
    }

    @staticmethod
    def optimize_tool_result(result: Any) -> str:
        """
        Optimize tool result for LLM consumption.

        Removes metadata and uses compact JSON formatting while preserving
        all user-facing data.

        Args:
            result: Tool result to optimize (dict, list, or string)

        Returns:
            Optimized JSON string with essential data preserved
        """
        try:
            # Parse if string
            if isinstance(result, str):
                data = json.loads(result)
            else:
                data = result

            # Optimize recursively
            optimized = DataOptimizer._optimize_structure(data)

            # Return compact JSON (no whitespace)
            return json.dumps(optimized, separators=(',', ':'), ensure_ascii=False)

        except (json.JSONDecodeError, TypeError, AttributeError):
            # Not JSON - return as-is
            return str(result)

    @staticmethod
    def _optimize_structure(data: Any) -> Any:
        """Recursively optimize dict/list structures."""
        if isinstance(data, dict):
            optimized = {}
            for key, value in data.items():
                # Skip metadata fields
                if key.lower() in DataOptimizer.METADATA_FIELDS:
                    continue
                if key.startswith('_') and key not in ['_id', '_key']:
                    continue

                # Recursively optimize nested structures
                if isinstance(value, (dict, list)):
                    optimized[key] = DataOptimizer._optimize_structure(value)
                else:
                    optimized[key] = value

            return optimized

        elif isinstance(data, list):
            # Keep ALL list items (don't truncate data)
            return [DataOptimizer._optimize_structure(item) for item in data]

        else:
            return data

    @staticmethod
    def create_summary(tool_result: Dict[str, Any], tool_name: str) -> str:
        """
        Create intelligent summary of tool results.

        Extracts key metadata and statistics while keeping representative samples.

        Args:
            tool_result: Tool execution result
            tool_name: Name of the tool

        Returns:
            Compact JSON summary string
        """
        try:
            # Parse if needed
            if isinstance(tool_result, str):
                data = json.loads(tool_result)
            else:
                data = tool_result

            # Extract core data
            core_data = data.get("data", data)

            # Create summary based on data type
            summary = DataOptimizer._summarize_data(core_data, tool_name)

            return json.dumps({
                "tool": tool_name,
                "status": "success" if data.get("success", True) else "error",
                "summary": summary
            }, separators=(',', ':'))

        except Exception as e:
            logger.debug(f"Summary creation failed: {e}")
            return json.dumps({
                "tool": tool_name,
                "status": "success",
                "summary": {"note": "Data retrieved successfully"}
            }, separators=(',', ':'))

    @staticmethod
    def _summarize_data(data: Any, tool_name: str) -> Dict[str, Any]:
        """Create summary based on data structure."""
        if isinstance(data, dict):
            summary = {}

            # Look for list/array fields (common in API responses)
            for key, value in data.items():
                if isinstance(value, list) and len(value) > 0:
                    summary[key] = {
                        "count": len(value),
                        "type": type(value[0]).__name__ if value else "unknown",
                        "sample": value[0] if value else None
                    }
                elif isinstance(value, dict):
                    summary[key] = {"type": "object", "keys": list(value.keys())[:5]}
                else:
                    summary[key] = value

            return summary

        elif isinstance(data, list):
            return {
                "count": len(data),
                "type": "list",
                "sample": data[0] if data else None
            }

        else:
            return {"value": str(data)[:200]}


class PromptOptimizer:
    """
    Optimizes prompts and message history for efficient LLM calls.

    Manages token budgets, compresses verbose content, and maintains
    only essential conversation context.
    """

    # Token budgets for different prompt sections
    MAX_SYSTEM_TOKENS = 500  # ~2000 chars
    MAX_TOOL_RESULT_TOKENS = 1000  # ~4000 chars per tool result
    MAX_CONTEXT_TOKENS = 3000  # ~12000 chars total context
    MAX_HISTORY_MESSAGES = 6  # Keep only recent messages

    @staticmethod
    def optimize_message_history(
        messages: List[BaseMessage],
        is_complex: bool = False,
        max_messages: Optional[int] = None,
        max_context_chars: Optional[int] = None
    ) -> List[BaseMessage]:
        """
        Optimize message history by keeping only recent, relevant messages.

        Args:
            messages: List of messages to optimize
            is_complex: Whether this is a complex query (keeps more messages)
            max_messages: Maximum messages to keep (None = use defaults)
            max_context_chars: Maximum total characters (optional, for compatibility)

        Returns:
            Optimized list of messages
        """
        if not messages:
            return []

        # Determine message limit
        if max_messages is None:
            max_messages = PromptOptimizer.MAX_HISTORY_MESSAGES if is_complex else 3

        # Always keep system messages
        system_messages = [msg for msg in messages if isinstance(msg, SystemMessage)]
        other_messages = [msg for msg in messages if not isinstance(msg, SystemMessage)]

        # Keep only recent messages
        recent_messages = other_messages[-max_messages:] if len(other_messages) > max_messages else other_messages

        # If max_context_chars specified, filter by total char length
        if max_context_chars:
            result_messages = system_messages + recent_messages
            total_chars = sum(len(str(msg.content)) for msg in result_messages if hasattr(msg, 'content'))

            # If over limit, progressively remove older messages (keep system always)
            while total_chars > max_context_chars and len(recent_messages) > 1:
                recent_messages.pop(0)  # Remove oldest non-system message
                result_messages = system_messages + recent_messages
                total_chars = sum(len(str(msg.content)) for msg in result_messages if hasattr(msg, 'content'))

            return result_messages

        return system_messages + recent_messages

    @staticmethod
    def compress_system_prompt(prompt: str, max_chars: int = 2000) -> str:
        """
        Compress system prompt by removing examples and verbose explanations.

        Args:
            prompt: System prompt to compress
            max_chars: Maximum characters allowed

        Returns:
            Compressed prompt string
        """
        if len(prompt) <= max_chars:
            return prompt

        # Remove example sections
        prompt = re.sub(r'Example[s]?:.*?(?=\n\n|\Z)', '', prompt, flags=re.DOTALL | re.IGNORECASE)
        prompt = re.sub(r'For example[,:].*?(?=\n\n|\Z)', '', prompt, flags=re.DOTALL | re.IGNORECASE)

        # Remove excessive whitespace
        prompt = re.sub(r'\n{3,}', '\n\n', prompt)
        prompt = re.sub(r'  +', ' ', prompt)

        # Truncate if still too long
        if len(prompt) > max_chars:
            prompt = prompt[:max_chars] + "..."

        return prompt.strip()

    @staticmethod
    def optimize_tool_message(message: ToolMessage, max_length: int = 4000) -> ToolMessage:
        """
        Optimize tool message content.

        Args:
            message: Tool message to optimize
            max_length: Maximum content length

        Returns:
            Optimized tool message
        """
        content = str(message.content)

        if len(content) <= max_length:
            return message

        # Try to optimize as JSON
        try:
            optimized_content = DataOptimizer.optimize_tool_result(content)
            if len(optimized_content) <= max_length:
                message.content = optimized_content
                return message
        except Exception:
            pass

        # Fallback: truncate with indicator
        message.content = content[:max_length] + "... (truncated)"
        return message

    @staticmethod
    def compress_tool_result(
        result: Any,
        max_chars: Optional[int] = None,
        preserve_data: bool = True
    ) -> str:
        """
        Compress tool result for LLM context.

        Args:
            result: Tool result to compress
            max_chars: Maximum characters (None = no limit)
            preserve_data: Whether to preserve all user-facing data

        Returns:
            Compressed result string
        """
        if preserve_data:
            # Use DataOptimizer which preserves data
            return DataOptimizer.optimize_tool_result(result)
        else:
            # Aggressive compression with truncation
            result_str = str(result)
            if max_chars and len(result_str) > max_chars:
                return result_str[:max_chars] + "... (truncated)"
            return result_str

    @staticmethod
    def validate_message_sequence(messages: List[BaseMessage]) -> tuple[bool, str]:
        """
        Validate message sequence for OpenAI API compatibility.

        Ensures ToolMessages have preceding AIMessages with tool_calls.

        Args:
            messages: List of messages to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        pending_tool_calls = set()

        for i, msg in enumerate(messages):
            if isinstance(msg, AIMessage):
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                        if tool_id:
                            pending_tool_calls.add(tool_id)

            elif isinstance(msg, ToolMessage):
                tool_call_id = getattr(msg, 'tool_call_id', None)
                if not tool_call_id:
                    return False, f"ToolMessage at index {i} missing tool_call_id"

                if tool_call_id not in pending_tool_calls:
                    return False, f"ToolMessage at index {i} has tool_call_id {tool_call_id} but no preceding AIMessage with matching tool_call"

                pending_tool_calls.discard(tool_call_id)

        # All ToolMessages should have been matched
        return True, ""

    @staticmethod
    def create_concise_tool_context(tool_results: List[Dict[str, Any]]) -> str:
        """
        Create concise tool context for agent iteration.

        Args:
            tool_results: List of tool results

        Returns:
            Concise context string
        """
        if not tool_results:
            return ""

        context_parts = ["\n\n**Recent Tool Results:**"]

        for result in tool_results[-3:]:  # Last 3 results
            tool_name = result.get("tool_name", "unknown")
            status = result.get("status", "unknown")

            if status == "success":
                context_parts.append(f"✅ {tool_name}: Success")
            else:
                context_parts.append(f"❌ {tool_name}: Failed")

        return "\n".join(context_parts)


class LLMOptimizer:
    """
    Optimizes LLM invocation for performance and reliability.

    Provides timeout protection, retry logic, and performance tracking
    for LLM calls.
    """

    def __init__(self):
        """Initialize LLM optimizer with performance tracking."""
        self.call_history: List[float] = []
        self.avg_latency: float = 0.0

    async def invoke_with_timeout(
        self,
        llm,
        messages: List[BaseMessage],
        timeout: float = 30.0,
        max_tokens: Optional[int] = None
    ) -> Any:
        """
        Invoke LLM with timeout protection.

        Args:
            llm: LLM instance to invoke
            messages: Messages to send to LLM
            timeout: Timeout in seconds
            max_tokens: Maximum tokens in response

        Returns:
            LLM response

        Raises:
            TimeoutError: If LLM call exceeds timeout
        """
        start_time = time.time()

        try:
            # Invoke with timeout protection
            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=timeout
            )

            # Track latency
            latency = (time.time() - start_time) * 1000
            self.call_history.append(latency)

            # Keep only recent history
            if len(self.call_history) > 10:
                self.call_history = self.call_history[-10:]

            # Update average
            self.avg_latency = sum(self.call_history) / len(self.call_history)

            return response

        except asyncio.TimeoutError:
            raise TimeoutError(f"LLM call exceeded {timeout}s timeout")
        except Exception as e:
            logger.error(f"LLM invocation error: {e}")
            raise

    async def optimized_invoke(
        self,
        llm,
        messages: List[BaseMessage],
        timeout: float = 30.0,
        max_tokens: Optional[int] = None
    ) -> Any:
        """
        Optimized LLM invocation with performance tracking.

        This is the primary method to use for LLM calls.

        Args:
            llm: LLM instance to invoke
            messages: Messages to send to LLM
            timeout: Timeout in seconds
            max_tokens: Maximum tokens in response

        Returns:
            LLM response

        Raises:
            TimeoutError: If LLM call exceeds timeout
        """
        # Use invoke_with_timeout for actual invocation
        return await self.invoke_with_timeout(llm, messages, timeout, max_tokens)

    @staticmethod
    def estimate_tokens(messages: List[BaseMessage]) -> int:
        """
        Estimate token count for messages.

        Uses rough approximation of 4 characters per token.

        Args:
            messages: Messages to estimate

        Returns:
            Estimated token count
        """
        total_chars = sum(
            len(str(msg.content))
            for msg in messages
            if hasattr(msg, 'content')
        )
        return total_chars // 4

    @staticmethod
    def estimate_token_count(messages: List[BaseMessage]) -> int:
        """
        Alias for estimate_tokens for backward compatibility.

        Args:
            messages: Messages to estimate

        Returns:
            Estimated token count
        """
        return LLMOptimizer.estimate_tokens(messages)

    @staticmethod
    def get_optimal_max_tokens(query_complexity: str = "simple") -> int:
        """
        Get optimal max_tokens based on query complexity.

        Args:
            query_complexity: "simple", "medium", or "complex"

        Returns:
            Optimal max_tokens value
        """
        token_limits = {
            "simple": 500,      # Quick answers
            "medium": 1000,     # Detailed responses
            "complex": 2000     # Complex multi-step answers
        }
        return token_limits.get(query_complexity, 1000)

    def get_performance_stats(self) -> Dict[str, float]:
        """
        Get performance statistics.

        Returns:
            Dict with latency statistics
        """
        if not self.call_history:
            return {
                "avg_latency_ms": 0,
                "min_latency_ms": 0,
                "max_latency_ms": 0,
                "total_calls": 0
            }

        return {
            "avg_latency_ms": self.avg_latency,
            "min_latency_ms": min(self.call_history),
            "max_latency_ms": max(self.call_history),
            "total_calls": len(self.call_history)
        }

