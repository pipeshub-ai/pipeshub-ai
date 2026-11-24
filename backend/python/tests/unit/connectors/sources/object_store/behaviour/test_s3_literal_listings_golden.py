"""Literal folder filters sync an S3 bucket exactly as the recorded syncs in the golden file.

Folder patterns must not change which keys a literal value lists, writes or
removes, or the sync points and folder scope it stores. The worlds and the
recording are in ``s3_literal_listings.py``.
"""

from __future__ import annotations

import logging

import pytest
from s3_literal_listings import load, sync, worlds

GOLDEN = load()


def test_the_golden_file_holds_the_generated_worlds() -> None:
    assert [entry["case"] for entry in GOLDEN] == worlds()


@pytest.mark.parametrize("entry", GOLDEN, ids=lambda entry: f"world{entry['case']['world']}")
async def test_a_literal_sync_matches_the_recorded_one(entry: dict) -> None:
    logging.disable(logging.CRITICAL)
    try:
        assert await sync(entry["case"]) == entry["sync"]
    finally:
        logging.disable(logging.NOTSET)
