"""
Agent Configuration Module

Centralized configuration for the QNA agent system.
All constants, thresholds, and configuration values are defined here.
"""

from typing import Dict


class MessageConfig:
    """Configuration for message and context management."""

    # Message history limits
    # CRITICAL: Follow-up queries need MORE context to understand conversation flow
    # Reduced limits were causing context loss and poor decision-making
    MAX_MESSAGES_COMPLEX = 20  # For complex queries and follow-ups (increased from 12)
    MAX_MESSAGES_SIMPLE = 20  # For simple queries (increased from 12)

    # Content length limits
    MAX_TOOL_RESULT_LENGTH = 3000  # Characters per tool result
    MAX_CONTEXT_CHARS = 100000     # Total context limit (~25k tokens)

    # Result preview
    RESULT_PREVIEW_LENGTH = 150
    RESULT_PREVIEW_MAX = 200
    RESULT_STR_LONG_THRESHOLD = 1000


class PerformanceConfig:
    """Configuration for performance optimization."""

    # Streaming delays (seconds)
    STREAMING_CHUNK_DELAY = 0.01
    STREAMING_FALLBACK_DELAY = 0.02

    # Execution limits
    MAX_ITERATION_COUNT = 15
    MAX_TOOLS_PER_ITERATION = 5
    MAX_RETRIES_PER_TOOL = 3  # Increased from 2 - allow more attempts to adapt

    # Loop detection
    LOOP_DETECTION_MIN_CALLS = 5
    LOOP_DETECTION_MAX_UNIQUE_TOOLS = 2

    # Failure thresholds
    MIN_FAILURES_FOR_STOP_RETRY = 3  # Stop retrying after this many failures
    MAX_UNIQUE_TOOLS_FAILED = 2  # Max unique tools before considering loop

    # Retrieval limits
    MAX_RETRIEVAL_CALLS = 2  # Maximum retrieval calls per query
    MIN_RESULTS_BEFORE_ANSWER = 10  # Minimum results before suggesting answer

    # Content truncation
    MAX_CONTENT_BLOCK_LENGTH = 1500  # Max chars per content block in context

    # Query similarity
    QUERY_SIMILARITY_THRESHOLD = 0.6  # Similarity threshold (60% overlap) for blocking duplicate queries


class AnalysisConfig:
    """Configuration for query and result analysis."""

    # Text analysis thresholds
    MARKDOWN_MIN_LENGTH = 100
    HEADER_LENGTH_THRESHOLD = 50
    SHORT_ERROR_TEXT_THRESHOLD = 100
    SUSPICIOUS_RESPONSE_MIN = 100

    # Pattern detection minimums
    TUPLE_RESULT_LEN = 2
    JSON_RICH_OBJECT_MIN_KEYS = 3
    KEY_VALUE_PATTERN_MIN_COUNT = 3
    ID_VALUE_MIN_LENGTH = 10

    # Success/failure tracking
    RECENT_CALLS_WINDOW = 5
    RECENT_FAILURE_WINDOW = 3
    REPETITION_MIN_COUNT = 2
    REPEATED_SUCCESS_MIN_COUNT = 2
    PING_REPEAT_MIN = 3

    # Comprehensive data detection
    COMPREHENSIVE_SUCCESS_MIN = 3
    COMPREHENSIVE_TYPES_MIN = 2
    PARTIAL_SUCCESS_MIN = 2
    PARTIAL_DATA_MIN = 2


class AgentConfig:
    """
    Main agent configuration class.

    Provides centralized access to all configuration settings.
    """

    # Component configurations
    messages = MessageConfig
    performance = PerformanceConfig
    analysis = AnalysisConfig

    @classmethod
    def get_message_limit(cls, is_complex: bool) -> int:
        """
        Get appropriate message limit based on query complexity.

        Args:
            is_complex: Whether the query is complex

        Returns:
            Maximum number of messages to keep in history
        """
        return cls.messages.MAX_MESSAGES_COMPLEX if is_complex else cls.messages.MAX_MESSAGES_SIMPLE

    @classmethod
    def should_optimize_aggressively(cls, query_length: int, is_simple: bool) -> bool:
        """
        Determine if aggressive optimization should be used.

        Args:
            query_length: Length of the query
            is_simple: Whether query is classified as simple

        Returns:
            True if aggressive optimization is appropriate
        """
        _SIMPLE_QUERY_LENGTH_THRESHOLD = 100
        return is_simple and query_length < _SIMPLE_QUERY_LENGTH_THRESHOLD

    @classmethod
    def get_all_constants(cls) -> Dict[str, int]:
        """
        Get all configuration constants as a dictionary.

        Useful for logging and debugging.

        Returns:
            Dictionary of all configuration values
        """
        return {
            # Message config
            "MAX_MESSAGES_COMPLEX": cls.messages.MAX_MESSAGES_COMPLEX,
            "MAX_MESSAGES_SIMPLE": cls.messages.MAX_MESSAGES_SIMPLE,
            "MAX_TOOL_RESULT_LENGTH": cls.messages.MAX_TOOL_RESULT_LENGTH,
            "MAX_CONTEXT_CHARS": cls.messages.MAX_CONTEXT_CHARS,

            # Performance config
            "MAX_ITERATION_COUNT": cls.performance.MAX_ITERATION_COUNT,
            "MAX_TOOLS_PER_ITERATION": cls.performance.MAX_TOOLS_PER_ITERATION,
            "MAX_RETRIES_PER_TOOL": cls.performance.MAX_RETRIES_PER_TOOL,

            # Analysis config
            "MARKDOWN_MIN_LENGTH": cls.analysis.MARKDOWN_MIN_LENGTH,
            "SHORT_ERROR_TEXT_THRESHOLD": cls.analysis.SHORT_ERROR_TEXT_THRESHOLD,
            "JSON_RICH_OBJECT_MIN_KEYS": cls.analysis.JSON_RICH_OBJECT_MIN_KEYS,
        }


# Backwards compatibility: Export commonly used constants at module level
MAX_MESSAGES_HISTORY = MessageConfig.MAX_MESSAGES_COMPLEX
MAX_MESSAGES_HISTORY_SIMPLE = MessageConfig.MAX_MESSAGES_SIMPLE
MAX_TOOL_RESULT_LENGTH = MessageConfig.MAX_TOOL_RESULT_LENGTH
MAX_CONTEXT_CHARS = MessageConfig.MAX_CONTEXT_CHARS
MAX_ITERATION_COUNT = PerformanceConfig.MAX_ITERATION_COUNT
MAX_TOOLS_PER_ITERATION = PerformanceConfig.MAX_TOOLS_PER_ITERATION
MAX_RETRIES_PER_TOOL = PerformanceConfig.MAX_RETRIES_PER_TOOL
RESULT_PREVIEW_LENGTH = MessageConfig.RESULT_PREVIEW_LENGTH
MARKDOWN_MIN_LENGTH = AnalysisConfig.MARKDOWN_MIN_LENGTH

