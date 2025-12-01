"""
Performance Tracking Module for Agent Execution

Tracks timing, metrics, and performance data for each step of agent execution.
Critical for building a world-class, production-ready agent.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StepTiming:
    """Tracks timing for a single step"""
    name: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def finish(self) -> float:
        """Mark step as finished and calculate duration"""
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        return self.duration_ms

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2) if self.duration_ms else None,
            "metadata": self.metadata
        }


class PerformanceTracker:
    """
    Tracks performance metrics throughout agent execution.

    Features:
    - Step-by-step timing
    - Tool execution metrics
    - LLM invocation tracking
    - Bottleneck identification
    - Performance reporting
    """

    def __init__(self):
        self.start_time = time.time()
        self.steps: List[StepTiming] = []
        self.current_step: Optional[StepTiming] = None
        self.metrics: Dict[str, Any] = defaultdict(lambda: {"count": 0, "total_ms": 0, "min_ms": float('inf'), "max_ms": 0})

    def start_step(self, name: str, **metadata) -> StepTiming:
        """Start tracking a new step"""
        # Finish current step if any
        if self.current_step and not self.current_step.end_time:
            self.current_step.finish()

        step = StepTiming(name=name, start_time=time.time(), metadata=metadata)
        self.steps.append(step)
        self.current_step = step
        return step

    def finish_step(self, **additional_metadata) -> Optional[float]:
        """Finish the current step and return duration"""
        if not self.current_step:
            return None

        if additional_metadata:
            self.current_step.metadata.update(additional_metadata)

        duration = self.current_step.finish()

        # Update metrics
        step_type = self.current_step.name
        self.metrics[step_type]["count"] += 1
        self.metrics[step_type]["total_ms"] += duration
        self.metrics[step_type]["min_ms"] = min(self.metrics[step_type]["min_ms"], duration)
        self.metrics[step_type]["max_ms"] = max(self.metrics[step_type]["max_ms"], duration)

        return duration

    def track_llm_call(self, duration_ms: float, tokens: Optional[int] = None):
        """Track an LLM API call"""
        self.metrics["llm_calls"]["count"] += 1
        self.metrics["llm_calls"]["total_ms"] += duration_ms
        if tokens:
            self.metrics["llm_calls"]["total_tokens"] = self.metrics["llm_calls"].get("total_tokens", 0) + tokens

    def track_tool_execution(self, tool_name: str, duration_ms: float, success: bool):
        """Track a tool execution"""
        key = f"tool_{tool_name}"
        self.metrics[key]["count"] += 1
        self.metrics[key]["total_ms"] += duration_ms
        self.metrics[key]["successes"] = self.metrics[key].get("successes", 0) + (1 if success else 0)
        self.metrics[key]["failures"] = self.metrics[key].get("failures", 0) + (0 if success else 1)

    def get_total_duration(self) -> float:
        """Get total execution time in milliseconds"""
        return (time.time() - self.start_time) * 1000

    def get_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        total_ms = self.get_total_duration()

        # Calculate step breakdown
        step_breakdown = []
        for step in self.steps:
            if step.duration_ms:
                step_breakdown.append({
                    "step": step.name,
                    "duration_ms": round(step.duration_ms, 2),
                    "percentage": round((step.duration_ms / total_ms) * 100, 1) if total_ms > 0 else 0,
                    "metadata": step.metadata
                })

        # Sort by duration to identify bottlenecks
        step_breakdown.sort(key=lambda x: x["duration_ms"], reverse=True)

        # Calculate aggregated metrics
        aggregated_metrics = {}
        for key, data in self.metrics.items():
            if data["count"] > 0:
                avg_ms = data["total_ms"] / data["count"]
                aggregated_metrics[key] = {
                    "count": data["count"],
                    "total_ms": round(data["total_ms"], 2),
                    "avg_ms": round(avg_ms, 2),
                    "min_ms": round(data.get("min_ms", 0), 2) if data.get("min_ms") != float('inf') else 0,
                    "max_ms": round(data.get("max_ms", 0), 2)
                }

                # Add success rate for tools
                if key.startswith("tool_"):
                    successes = data.get("successes", 0)
                    failures = data.get("failures", 0)
                    total = successes + failures
                    aggregated_metrics[key]["success_rate"] = round((successes / total) * 100, 1) if total > 0 else 0

        return {
            "total_duration_ms": round(total_ms, 2),
            "step_count": len(self.steps),
            "step_breakdown": step_breakdown,
            "metrics": aggregated_metrics,
            "bottlenecks": self._identify_bottlenecks(step_breakdown, total_ms)
        }

    def _identify_bottlenecks(self, step_breakdown: List[Dict], total_ms: float) -> List[Dict[str, Any]]:
        """Identify performance bottlenecks"""
        bottlenecks = []

        for step in step_breakdown:
            percentage = step["percentage"]
            duration = step["duration_ms"]

            # Flag steps taking >25% of total time or >1000ms
            if percentage > 25 or duration > 1000:
                bottlenecks.append({
                    "step": step["step"],
                    "duration_ms": duration,
                    "percentage": percentage,
                    "severity": "high" if percentage > 40 else "medium"
                })

        return bottlenecks

    def log_summary(self, logger_instance=None):
        """Log performance summary"""
        log = logger_instance or logger
        summary = self.get_summary()

        log.info("=" * 80)
        log.info(f"⚡ PERFORMANCE SUMMARY - Total: {summary['total_duration_ms']:.0f}ms")
        log.info("=" * 80)

        # Log top 5 slowest steps
        log.info("📊 Top Steps by Duration:")
        for i, step in enumerate(summary['step_breakdown'][:5], 1):
            log.info(f"  {i}. {step['step']}: {step['duration_ms']:.0f}ms ({step['percentage']:.1f}%)")

        # Log bottlenecks
        if summary['bottlenecks']:
            log.warning("⚠️  Performance Bottlenecks Detected:")
            for bottleneck in summary['bottlenecks']:
                log.warning(f"  - {bottleneck['step']}: {bottleneck['duration_ms']:.0f}ms ({bottleneck['percentage']:.1f}%) [{bottleneck['severity'].upper()}]")

        # Log tool performance
        tool_metrics = {k: v for k, v in summary['metrics'].items() if k.startswith('tool_')}
        if tool_metrics:
            log.info("🔧 Tool Performance:")
            for tool_name, metrics in tool_metrics.items():
                log.info(f"  - {tool_name.replace('tool_', '')}: {metrics['count']} calls, avg {metrics['avg_ms']:.0f}ms, {metrics.get('success_rate', 100):.0f}% success")

        # Log LLM performance
        if 'llm_calls' in summary['metrics']:
            llm = summary['metrics']['llm_calls']
            log.info(f"🤖 LLM Calls: {llm['count']} calls, avg {llm['avg_ms']:.0f}ms, total {llm['total_ms']:.0f}ms")

        log.info("=" * 80)


def get_performance_tracker(state: Dict[str, Any]) -> PerformanceTracker:
    """Get or create performance tracker from state"""
    if "_performance_tracker" not in state:
        state["_performance_tracker"] = PerformanceTracker()
    return state["_performance_tracker"]

