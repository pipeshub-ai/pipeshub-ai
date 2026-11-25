"""A run that can never execute on this worker: settled failed, never retried."""

from __future__ import annotations


class RunRefused(Exception):
    """The run can never execute here; ``error_type`` is its machine-readable reason."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


__all__ = ["RunRefused"]
