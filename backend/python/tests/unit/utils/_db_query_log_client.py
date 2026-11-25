"""A stand-in database client for test_db_query_log: its frames are the "client" the walk skips."""
from app.utils import db_query_log

SKIP = (__name__,)


def describe(exc: Exception) -> tuple[object, str, dict, bool]:
    return getattr(exc, "code", None), getattr(exc, "cls", "internal"), {}, getattr(exc, "hide", True)


class FakeClient:
    def __init__(self, logger, behaviour) -> None:
        self.logger, self.behaviour = logger, behaviour

    @db_query_log.query_logged("fakedb", "run", describe=describe, skip=SKIP)
    async def run(self) -> str:
        return await self.behaviour()

    @db_query_log.query_logged("fakedb", "outer", describe=describe, skip=SKIP)
    async def outer(self) -> str:
        return await self.run()
