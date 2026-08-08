"""PipesHub-specific adapters for `agent_loop_lib`'s skills subsystem.

Every class here composes a generic `agent_loop_lib` ABC (`SkillStore`,
`SkillHistoryReader`, `SkillIndex`, `SkillUsageTracker`, `SkillGovernor`)
against PipesHub's own services — `IGraphDBProvider`, `RetrievalService`'s
embedder — mirroring how `sandbox_bridge.py`/`langchain_transport.py` adapt
the rest of `agent_loop_lib` for this codebase. Nothing in
`agent_loop_lib` imports from this package; the dependency points one way.
"""
