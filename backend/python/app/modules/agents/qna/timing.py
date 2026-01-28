"""
Performance Timing Utilities for Agent System

This module provides consistent, low-overhead timing instrumentation
to identify bottlenecks in the agent execution pipeline.

Usage:
    from app.modules.agents.qna.timing import Timer, log_timing

    # Context manager style
    with Timer("operation_name", logger) as t:
        do_something()
    # Automatically logs: "⏱️ operation_name: 123.45ms"

    # Decorator style
    @log_timing("my_function")
    async def my_function():
        ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, TypeVar

# Type variable for generic function decorator
F = TypeVar("F", bound=Callable[..., Any])

# Global timing storage for aggregation
_timing_data: Dict[str, List[float]] = {}
_timing_lock = asyncio.Lock()


class Timer:
    """
    High-precision timer for measuring operation duration.

    Can be used as a context manager or manually with start/stop.

    Example:
        with Timer("my_operation", logger) as t:
            do_something()
        print(f"Duration: {t.duration_ms}ms")
    """

    def __init__(
        self,
        name: str,
        logger: Optional[logging.Logger] = None,
        log_level: int = logging.INFO,
        threshold_ms: float = 0,  # Only log if duration exceeds this
        store_globally: bool = True,  # Store in global timing data
    ):
        self.name = name
        self.logger = logger
        self.log_level = log_level
        self.threshold_ms = threshold_ms
        self.store_globally = store_globally
        self.start_time: float = 0
        self.end_time: float = 0
        self._final_duration_ms: float = 0
        self._stopped: bool = False

    @property
    def duration_ms(self) -> float:
        """Get elapsed time in milliseconds (works during and after timing)."""
        if self._stopped:
            return self._final_duration_ms
        if self.start_time == 0:
            return 0
        return (time.perf_counter() - self.start_time) * 1000

    def start(self) -> "Timer":
        """Start the timer."""
        self.start_time = time.perf_counter()
        self._stopped = False
        return self

    def stop(self) -> float:
        """Stop the timer and return duration in milliseconds."""
        self.end_time = time.perf_counter()
        self._final_duration_ms = (self.end_time - self.start_time) * 1000
        self._stopped = True
        return self._final_duration_ms

    def __enter__(self) -> "Timer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
        self._log_and_store()

    def _log_and_store(self) -> None:
        """Log the timing and optionally store globally."""
        if self.duration_ms >= self.threshold_ms:
            if self.logger:
                # Use emoji for visual scanning in logs
                if self.duration_ms < 100:
                    emoji = "⚡"  # Fast
                elif self.duration_ms < 500:
                    emoji = "⏱️"  # Normal
                elif self.duration_ms < 2000:
                    emoji = "🐢"  # Slow
                else:
                    emoji = "🚨"  # Very slow

                self.logger.log(
                    self.log_level,
                    f"{emoji} {self.name}: {self.duration_ms:.1f}ms"
                )

        if self.store_globally:
            if self.name not in _timing_data:
                _timing_data[self.name] = []
            _timing_data[self.name].append(self.duration_ms)


class TimingContext:
    """
    Hierarchical timing context for tracking nested operations.

    Allows building a complete timing breakdown of an agent run.

    Example:
        ctx = TimingContext("agent_run", logger)
        with ctx.child("planner"):
            with ctx.child("llm_call"):
                ...
        print(ctx.get_summary())
    """

    def __init__(
        self,
        name: str,
        logger: Optional[logging.Logger] = None,
        parent: Optional["TimingContext"] = None
    ):
        self.name = name
        self.logger = logger
        self.parent = parent
        self.children: List[TimingContext] = []
        self.start_time: float = 0
        self.duration_ms: float = 0
        self._timer: Optional[Timer] = None

    def start(self) -> "TimingContext":
        """Start timing this context."""
        self.start_time = time.perf_counter()
        return self

    def stop(self) -> float:
        """Stop timing and return duration."""
        self.duration_ms = (time.perf_counter() - self.start_time) * 1000
        return self.duration_ms

    @contextmanager
    def child(self, name: str):
        """Create a child timing context."""
        child_ctx = TimingContext(name, self.logger, parent=self)
        self.children.append(child_ctx)
        child_ctx.start()
        try:
            yield child_ctx
        finally:
            child_ctx.stop()
            if self.logger:
                depth = self._get_depth()
                indent = "  " * depth
                emoji = "⚡" if child_ctx.duration_ms < 100 else ("⏱️" if child_ctx.duration_ms < 500 else "🐢")
                self.logger.debug(f"{indent}{emoji} {name}: {child_ctx.duration_ms:.1f}ms")

    def _get_depth(self) -> int:
        """Get nesting depth for indentation."""
        depth = 0
        ctx = self.parent
        while ctx:
            depth += 1
            ctx = ctx.parent
        return depth

    def get_summary(self) -> Dict[str, Any]:
        """Get timing summary as a dictionary."""
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "children": [c.get_summary() for c in self.children]
        }

    def format_tree(self, indent: int = 0) -> str:
        """Format as a tree string for logging."""
        lines = []
        prefix = "  " * indent
        emoji = "⚡" if self.duration_ms < 100 else ("⏱️" if self.duration_ms < 500 else "🐢")
        lines.append(f"{prefix}{emoji} {self.name}: {self.duration_ms:.1f}ms")
        for child in self.children:
            lines.append(child.format_tree(indent + 1))
        return "\n".join(lines)


def log_timing(
    name: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    threshold_ms: float = 0
) -> Callable[[F], F]:
    """
    Decorator to log function execution time.

    Works with both sync and async functions.

    Args:
        name: Operation name (defaults to function name)
        logger: Logger instance (uses module logger if not provided)
        threshold_ms: Only log if duration exceeds this

    Example:
        @log_timing("my_operation")
        async def my_function():
            ...
    """
    def decorator(func: F) -> F:
        op_name = name or func.__name__
        func_logger = logger or logging.getLogger(func.__module__)

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with Timer(op_name, func_logger, threshold_ms=threshold_ms):
                    return await func(*args, **kwargs)
            return async_wrapper  # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                with Timer(op_name, func_logger, threshold_ms=threshold_ms):
                    return func(*args, **kwargs)
            return sync_wrapper  # type: ignore

    return decorator


def get_timing_summary() -> Dict[str, Dict[str, float]]:
    """
    Get summary of all recorded timings.

    Returns dict with operation names and their statistics.
    """
    summary = {}
    for name, timings in _timing_data.items():
        if timings:
            summary[name] = {
                "count": len(timings),
                "total_ms": sum(timings),
                "avg_ms": sum(timings) / len(timings),
                "min_ms": min(timings),
                "max_ms": max(timings),
            }
    return summary


def clear_timing_data() -> None:
    """Clear all recorded timing data."""
    _timing_data.clear()


def format_timing_report(logger: logging.Logger) -> None:
    """Log a formatted timing report."""
    summary = get_timing_summary()
    if not summary:
        logger.info("No timing data recorded")
        return

    logger.info("=" * 60)
    logger.info("TIMING REPORT")
    logger.info("=" * 60)

    # Sort by total time descending
    sorted_ops = sorted(
        summary.items(),
        key=lambda x: x[1]["total_ms"],
        reverse=True
    )

    for name, stats in sorted_ops:
        logger.info(
            f"  {name}: {stats['avg_ms']:.1f}ms avg "
            f"({stats['count']}x, total: {stats['total_ms']:.0f}ms, "
            f"min: {stats['min_ms']:.1f}ms, max: {stats['max_ms']:.1f}ms)"
        )

    logger.info("=" * 60)


# Convenience function for quick timing
def timed(name: str, log: Optional[logging.Logger] = None) -> Timer:
    """
    Create a timer for a named operation.

    Example:
        with timed("my_operation", logger):
            do_something()
    """
    return Timer(name, log)


__all__ = [
    "Timer",
    "TimingContext",
    "log_timing",
    "timed",
    "get_timing_summary",
    "clear_timing_data",
    "format_timing_report",
]

