from __future__ import annotations

import logging

from app.agent_loop_lib.events.base import AgentEvent, EventEmitter


class LoggingEventEmitter(EventEmitter):
    """Emits all agent events to the Python logging system."""

    def __init__(self, level: int = logging.INFO) -> None:
        self.level = level
        self.logger = logging.getLogger("agent_loop.events")

    async def emit(self, event: AgentEvent) -> None:
        self.logger.log(
            self.level,
            "[%s] run_id=%s %s",
            event.event_type.value,
            event.run_context.run_id,
            event.payload,
        )
