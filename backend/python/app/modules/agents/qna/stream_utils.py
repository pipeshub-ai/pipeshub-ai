"""
Stream Utilities Module

Provides shared utilities for streaming operations across the agent system.
Consolidates context restoration logic to avoid code duplication.
"""

import logging
from typing import Any, Dict, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.types import StreamWriter

logger = logging.getLogger(__name__)


def safe_stream_write(
    writer: Optional[StreamWriter],
    event_data: Dict[str, Any],
    config: Optional[RunnableConfig] = None,
    log_errors: bool = True
) -> bool:
    """
    Safely write to stream writer with automatic context restoration.

    This is the STANDARD way to bridge context gaps in LangChain/LangGraph.
    Uses 'config' to restore context if it's missing (common in async/threaded flows).

    Args:
        writer: StreamWriter instance passed to the node (None if streaming disabled)
        event_data: Dict with 'event' and 'data' keys
        config: RunnableConfig from the node (for context preservation)
        log_errors: Whether to log errors (default: True)

    Returns:
        True if write succeeded, False otherwise

    Example:
        >>> safe_stream_write(writer, {
        ...     "event": "status",
        ...     "data": {"status": "processing", "message": "Working..."}
        ... }, config)
    """
    if writer is None:
        return False

    try:
        # Try writing normally
        writer(event_data)
        return True

    except RuntimeError as e:
        # If context is lost, explicitly restore it using the node's config
        if "get_config" in str(e) and config:
            try:
                # Set the context variable temporarily for this operation
                token = var_child_runnable_config.set(config)
                try:
                    writer(event_data)
                    return True
                finally:
                    var_child_runnable_config.reset(token)
            except Exception as restore_error:
                if log_errors:
                    logger.warning(
                        f"Stream write failed even after context restoration: {restore_error}",
                        extra={"event": event_data.get("event")}
                    )
                return False
        else:
            if log_errors:
                logger.warning(
                    f"Stream write failed (no config for restoration): {e}",
                    extra={"event": event_data.get("event")}
                )
            return False

    except Exception as e:
        if log_errors:
            logger.warning(
                f"Unexpected stream write error: {e}",
                extra={"event": event_data.get("event")}
            )
        return False


def stream_status(
    writer: Optional[StreamWriter],
    status: str,
    message: str,
    config: Optional[RunnableConfig] = None,
    **extra_data
) -> bool:
    """
    Convenience method to stream a status update.

    Args:
        writer: StreamWriter instance
        status: Status identifier (e.g., "analyzing", "processing")
        message: Human-readable status message
        config: RunnableConfig for context preservation
        **extra_data: Additional data to include in the status

    Returns:
        True if write succeeded, False otherwise

    Example:
        >>> stream_status(writer, "analyzing", "🧠 Analyzing your request...", config)
    """
    event_data = {
        "event": "status",
        "data": {
            "status": status,
            "message": message,
            **extra_data
        }
    }
    return safe_stream_write(writer, event_data, config)


def stream_error(
    writer: Optional[StreamWriter],
    error_message: str,
    error_code: Optional[str] = None,
    config: Optional[RunnableConfig] = None
) -> bool:
    """
    Stream an error message to the user.

    Args:
        writer: StreamWriter instance
        error_message: Human-readable error message
        error_code: Optional error code for categorization
        config: RunnableConfig for context preservation

    Returns:
        True if write succeeded, False otherwise
    """
    event_data = {
        "event": "error",
        "data": {
            "message": error_message,
            "code": error_code
        }
    }
    return safe_stream_write(writer, event_data, config, log_errors=False)

