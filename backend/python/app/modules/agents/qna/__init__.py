"""
⚡ Trillion Dollar Agent - QnA Module ⚡

Enterprise-grade question answering and agent system with:
- Advanced conversation memory
- Intelligent follow-up detection
- Context-aware response generation
- Multi-turn conversation support

NOTE: Do NOT eagerly import heavy submodules (graph, nodes, deep_agent, etc.)
here. app.utils.streaming imports schemas from this package, and those submodules
import from streaming — creating a circular dependency. Import directly from the
submodules instead (e.g. from app.modules.agents.qna.graph import agent_graph).
"""

