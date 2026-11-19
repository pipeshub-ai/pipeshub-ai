"""``input_digest_of``: a stage fingerprints the record's text digest unless its IO supplies another source."""
from __future__ import annotations

from types import SimpleNamespace

from app.modules.pipeline.stage import input_digest_of

JOB = SimpleNamespace(text_digest="text-digest")


async def test_an_io_without_a_source_gives_the_records_own_digest() -> None:
    assert await input_digest_of(JOB, SimpleNamespace()) == "text-digest"


async def test_an_io_with_a_source_is_asked_for_the_job() -> None:
    seen = []

    async def input_digest(job: object) -> str:
        seen.append(job)
        return "other-digest"

    assert await input_digest_of(JOB, SimpleNamespace(input_digest=input_digest)) == "other-digest"
    assert seen == [JOB]
