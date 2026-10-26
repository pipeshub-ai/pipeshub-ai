from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

import app.extraction_main as extraction_main


@pytest.mark.parametrize(("raw", "slots"), [(None, 8), ("eight", 8), ("", 8), ("0", 1), ("3", 3)])
async def test_entity_extraction_slots_fall_back_instead_of_failing_startup(monkeypatch, raw, slots):
    if raw is None:
        monkeypatch.delenv("MAX_CONCURRENT_ENTITY_EXTRACTIONS", raising=False)
    else:
        monkeypatch.setenv("MAX_CONCURRENT_ENTITY_EXTRACTIONS", raw)
    container = MagicMock()
    app = FastAPI()
    with (
        patch.object(extraction_main, "_get_initialized_container", AsyncMock(return_value=container)),
        patch.object(extraction_main, "DocumentExtraction"),
        patch.object(extraction_main, "mark_process_non_dumpable"),
    ):
        async with extraction_main.lifespan(app):
            assert await _free_slots(app.state.entity_extraction_semaphore) == slots


async def _free_slots(semaphore) -> int:
    """Count permits through the public API, then give them back."""
    taken = 0
    while not semaphore.locked():
        await semaphore.acquire()
        taken += 1
    for _ in range(taken):
        semaphore.release()
    return taken

