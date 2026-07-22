"""Prompt constants extracted from `modules/agents/qna/nodes.py`.

Pure string data (planner prompts, connector guidance blocks, ReAct base
prompt) with no LangGraph/ChatState coupling — safe to import from both the
legacy LangGraph node functions and the agent-loop adapter's
`PipesHubPromptBuilder` without either depending on the other.
"""
