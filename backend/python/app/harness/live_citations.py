"""The streamed answer's citations on the LiveBus (CITESTREAM).

Legacy's ``TerminalAnswerStreamer`` numbers the answer's citation refs while it
streams, so a marker is a link as soon as its source is known. A durable run's
live frames carry only raw text, and Node cannot number refs (the sources live
in the worker's tool state), so its markers stayed inert until the final answer.
This sink adds, to a ``text_delta`` frame, the attempt's text so far as legacy
would show it: ``answer`` (refs numbered, confidence trailer hidden) and,
when they changed, ``citations``. Node relays them as the answer delta legacy sent.

Numbering re-reads the whole text, so it runs at most once per
``PIPESHUB_ANSWER_DELTA_INTERVAL_MS`` as legacy does, and at once on a frame
that closes a citation ref, so a marker never waits on the window.
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.answer_streamer import (
    answer_delta_min_interval,
    resolve_streamed_answer,
)
from app.agents.agent_loop.hooks.citations import CitationCollector
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.journal.ports import LiveSinkPort
    from harness_core.journal.types import LiveFrame

# The end of a ref the normalizer numbers: `[source](ref3)`, `[ref3]`, a web URL link.
_CLOSED_REF = re.compile(r"(?:ref\d+|https?://[^\s)\]]+)\s*[)\]]")
# How far back a ref closed by this frame may have started.
_LOOKBACK = 2048


class CitedLiveSink:
    """Wraps the run's live sink; every frame still goes through, in order."""

    def __init__(
        self,
        inner: LiveSinkPort,
        context: Any,  # noqa: ANN401 - the legacy AgentContext the tools run in
        *,
        interval_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        log: HarnessLog | None = None,
    ) -> None:
        self._inner = inner
        self._log = log or HarnessLog.named("app.harness.live_citations")
        self._collector = CitationCollector(context)
        self._interval = answer_delta_min_interval() if interval_s is None else interval_s
        self._clock = clock
        self._attempt: tuple[int, int] | None = None
        self._text = ""
        self._resolved = 0
        self._at = 0.0
        # What this attempt last sent; the first numbering always sends its own.
        self._citations: list[dict[str, Any]] | None = None

    async def publish(self, frame: LiveFrame) -> None:
        text = frame.data.get("text")
        if frame.kind == "text_delta" and isinstance(text, str):
            try:
                frame = self._annotated(frame, text)
            except Exception as exc:
                # The text still streams; only its markers wait for the final answer.
                self._log.warning("live.citations_failed", errorType=type(exc).__name__)
        await self._inner.publish(frame)

    def _annotated(self, frame: LiveFrame, text: str) -> LiveFrame:
        attempt = (frame.fence, frame.model_attempt)
        if attempt != self._attempt:
            self._attempt, self._text, self._resolved, self._at, self._citations = attempt, "", 0, 0.0, None
        self._text += text
        now = self._clock()
        if now - self._at < self._interval and not self._closes_ref():
            return frame
        ref_mapper = self._collector.citation_ref_mapper
        answer, citations, _confidence = resolve_streamed_answer(
            self._text,
            self._collector,
            ref_to_url=ref_mapper.ref_to_url if ref_mapper is not None else None,
            web_records=self._collector.web_records,
        )
        self._resolved, self._at = len(self._text), now
        data: dict[str, Any] = {**frame.data, "answer": answer}
        if citations != self._citations:
            self._citations = citations
            # The bus writes plain JSON; a source's metadata may hold values it cannot.
            data["citations"] = json.loads(json.dumps(citations, default=str))
        return dataclasses.replace(frame, data=data)

    def _closes_ref(self) -> bool:
        start = max(0, self._resolved - _LOOKBACK)
        return any(m.end() > self._resolved for m in _CLOSED_REF.finditer(self._text, start))


__all__ = ["CitedLiveSink"]
