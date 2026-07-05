"""Context-building helpers extracted from `modules/agents/qna/nodes.py`.

Each module here takes explicit parameters (or a `ChatState`-shaped dict via
a `state.get(...)` convention already followed by the originals) rather than
depending on LangGraph — safe to call from both the legacy node functions
and the agent-loop adapter's `PipesHubPromptBuilder`/hooks.
"""
