"""Context-building helpers extracted from `modules/agents/qna/nodes.py`
(deleted with the rest of LangGraph in the agent-loop migration).

Each module here takes explicit parameters (or a `ChatState`-shaped dict via
a `state.get(...)` convention already followed by the originals) rather than
depending on LangGraph — used by the agent-loop adapter's
`PipesHubPromptBuilder`/hooks.
"""
