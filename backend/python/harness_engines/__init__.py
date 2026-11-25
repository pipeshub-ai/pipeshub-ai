"""Engine adapters: one sub-package per engine, depending on ``harness_core`` only.

Sub-packages never import each other, and nothing here imports the embedding
application (import-linter contracts in ``backend/python/pyproject.toml``).
"""
