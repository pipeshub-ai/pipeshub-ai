"""Record, scrub, replay and diff harness runs (P0.4; TEST-STRATEGY §4.6).

``python -m harness_core.testing.replay diff expected.json actual.json`` prints
the first step where two recordings behave differently (exit 1 when they do).
"""

from harness_core.testing.replay.diff import (
    DiffReport,
    Divergence,
    describe,
    diff_recordings,
)
from harness_core.testing.replay.recorder import (
    RecordingModel,
    RunRecorder,
    record_response,
    summarize_request,
)
from harness_core.testing.replay.recording import (
    FORMAT,
    FinalOutcome,
    Manifest,
    ModelStep,
    RecordedEffect,
    RecordedError,
    RecordedResponse,
    RecordedToolCall,
    Recording,
    RequestSummary,
    RunInputs,
    ToolStep,
)
from harness_core.testing.replay.replay import (
    FrozenOutcome,
    FrozenTools,
    recording_to_script,
)
from harness_core.testing.replay.scrub import (
    SCRUB_VERSION,
    Scrubber,
    SecretLeftBehind,
    find_secrets,
    scrub_recording,
)

__all__ = [
    "FORMAT",
    "SCRUB_VERSION",
    "DiffReport",
    "Divergence",
    "FinalOutcome",
    "FrozenOutcome",
    "FrozenTools",
    "Manifest",
    "ModelStep",
    "RecordedEffect",
    "RecordedError",
    "RecordedResponse",
    "RecordedToolCall",
    "Recording",
    "RecordingModel",
    "RequestSummary",
    "RunInputs",
    "RunRecorder",
    "Scrubber",
    "SecretLeftBehind",
    "ToolStep",
    "describe",
    "diff_recordings",
    "find_secrets",
    "record_response",
    "recording_to_script",
    "scrub_recording",
    "summarize_request",
]
