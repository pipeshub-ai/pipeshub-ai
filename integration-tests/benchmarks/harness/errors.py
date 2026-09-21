"""Harness exception taxonomy.

Preflight errors abort before any paid work. Per-document and per-question
failures are recorded on the item and scored — never swallowed.
"""

from __future__ import annotations


class FramesError(Exception):
    """Base class for every harness error."""


class ConfigError(FramesError):
    """Invalid run configuration or environment."""


class MissingSecretError(ConfigError):
    """A required credential is not set in the environment."""


class ResumeMismatchError(ConfigError):
    """`--resume` pointed at a run created from a different configuration."""


class DatasetIntegrityError(FramesError):
    """The dataset does not match the pinned revision or expected shape."""


class CorpusError(FramesError):
    """The corpus build failed beyond the tolerated threshold."""


class ModelNotRegisteredError(FramesError):
    """A selected model is not configured in the PipesHub model registry."""


class IngestError(FramesError):
    """Uploading the corpus into PipesHub failed."""


class IndexTimeoutError(FramesError):
    """Indexing did not reach the coverage gate in time."""


class BackendContractError(FramesError):
    """The PipesHub build lacks a stream contract the harness depends on."""


class CircuitOpenError(FramesError):
    """Too many items failed; the stage was aborted to stop wasting spend."""


class CostLimitError(FramesError):
    """The configured spend limit was reached."""


class StreamError(FramesError):
    """Per-question chat stream failure, recorded on the prediction."""


class StreamProtocolError(StreamError):
    """The stream ended or framed events in a way the harness cannot parse."""


class StreamTimeoutError(StreamError):
    """The stream exceeded its wall-clock deadline."""


class JudgeParseError(FramesError):
    """Judge output carried no parseable verdict."""


class TransientHTTPError(FramesError):
    """A retryable HTTP status (429/5xx) — see `retry.http_retry`."""

    def __init__(self, status: int, retry_after: float | None = None, url: str = "") -> None:
        super().__init__(f"HTTP {status} {url}".strip())
        self.status = status
        self.retry_after = retry_after
