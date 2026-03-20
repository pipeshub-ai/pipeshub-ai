"""
Deep Agent Module - Orchestrator + Sub-Agent Architecture

Provides a multi-agent system with:
- Orchestrator/Supervisor for task decomposition
- Sub-agents with isolated context windows
- Automatic context compaction and summarization
- Tool routing by domain
- Result aggregation with quality evaluation
- Retry/continue loops for complex multi-step tasks
- Full compatibility with the existing respond_node for citations
"""

from app.modules.agents.deep.graph import create_deep_agent_graph, deep_agent_graph

__all__ = [
    "create_deep_agent_graph",
    "deep_agent_graph",
]
