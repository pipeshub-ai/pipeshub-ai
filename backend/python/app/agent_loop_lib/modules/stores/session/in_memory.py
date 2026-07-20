from __future__ import annotations

from app.agent_loop_lib.modules.stores.session.base import Session, SessionStore


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._runs: dict[str, list[str]] = {}

    async def create(self, session: Session) -> str:
        self._sessions[session.session_id] = session
        self._runs[session.session_id] = []
        return session.session_id

    async def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def add_run(self, session_id: str, run_id: str) -> None:
        if session_id in self._runs:
            self._runs[session_id].append(run_id)

    async def list_runs(self, session_id: str) -> list[str]:
        return list(self._runs.get(session_id, []))

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._runs.pop(session_id, None)
