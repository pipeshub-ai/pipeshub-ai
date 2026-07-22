"""Offline-runnable regression evals for the deep-mode agent loop.

Distinct from `app.agent_loop_lib.eval` (the generic, framework-level
trajectory export / rubric-grading library any agent-loop consumer can
use): this package holds PipesHub-specific eval ASSETS — the actual query
datasets and scoring logic for deep mode's own behavior (starting with
decomposition quality, see `decomposition_queries.py`/`decomposition_
scorer.py`/`decomposition_harness.py`) — built on top of that generic
library rather than duplicating it.
"""
