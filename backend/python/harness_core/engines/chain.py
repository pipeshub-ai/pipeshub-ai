"""The wrappers every engine session is driven through, in their one order: the controller's and the conformance
suite's (so a lane tests what production runs).

``stop_bounded`` (CF-09) innermost, then ``overflow_compacting`` (MG-10, with the raw session's summary-and-restart
where it offers one, C3.67), then ``literal_session`` (SLASH0) outermost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.literal import literal_session
from harness_core.engines.overflow import overflow_compacting
from harness_core.engines.stopping import stop_bounded

if TYPE_CHECKING:
    from harness_core.engines.ports import EngineSession


def engine_session_chain(session: EngineSession) -> EngineSession:
    return literal_session(overflow_compacting(stop_bounded(session), restart=session))


__all__ = ["engine_session_chain"]
