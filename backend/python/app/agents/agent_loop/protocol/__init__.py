"""AG-UI protocol support for the agent-loop path — see the migration
plan's design section for the full rationale. Two entry points:

- `AGUIEventEmitter` (`agui_emitter.py`): `EventEmitter` sibling of
  `SSEEventEmitter`, wired in by `PipesHubAgentFactory` when a request
  negotiates the `agui` protocol. Translates `AgentEvent`s (which carry
  `run_context` — the only place run/parent-run identity and sub-agent
  nesting are visible) into AG-UI-shaped SSE frames.
- `ProtocolFormatter` (`formatter.py`): a `LegacyFormatter`/`AGUIFormatter`
  strategy for the handful of producers that write directly to an
  `EventSink` without ever seeing an `AgentEvent` (`TerminalAnswerStreamer`,
  `AnswerFinalizer`, `emit_pre_run_clarification`, the `ask_user_question`
  hook, `tool_adapter`'s tool_unavailable emission, sandbox artifact
  emission) — selected once per request off `AgentContext.protocol`.
- `TranscriptCollector` (`transcript_collector.py`): an `EventEmitter`
  sibling of `AGUIEventEmitter`, composed alongside it (see
  `factory.py::_build_event_emitter`) to assemble the ordered `MessagePart`
  transcript (text/reasoning/tool_call/sub_agent) attached to
  `completion_data["parts"]` — see the "Parts-Based Agent Message
  Transcript" plan.

Both write the same `{"event": ..., "data": ...}` shape `QueueEventSink`
already expects, so `stream_bridge.py`'s queue/serialization loop needs no
protocol branch at all — see `agui.py::frame()`.
"""

from app.agents.agent_loop.protocol.agui import AGUIEventType, frame, new_id
from app.agents.agent_loop.protocol.agui_emitter import AGUIEventEmitter
from app.agents.agent_loop.protocol.formatter import (
    AGUIFormatter,
    ArtifactSSEPayload,
    LegacyFormatter,
    ProtocolFormatter,
)
from app.agents.agent_loop.protocol.transcript_collector import (
    MessagePart,
    TranscriptCollector,
)

__all__ = [
    "AGUIEventType",
    "frame",
    "new_id",
    "AGUIEventEmitter",
    "ProtocolFormatter",
    "ArtifactSSEPayload",
    "LegacyFormatter",
    "AGUIFormatter",
    "MessagePart",
    "TranscriptCollector",
]
