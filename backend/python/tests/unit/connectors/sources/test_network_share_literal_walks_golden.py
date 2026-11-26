"""Literal folder filters walk a share exactly as the recorded walks in the golden file.

A change to folder matching must not change what a literal value syncs, in
which order, or how many listings it costs. Worlds where comparing names as a
file share does could decide differently (not N-neutral) may differ only as
declared in ``network_share_walk_deltas.py``. The worlds and the recording are
in ``network_share_literal_walks.py``.
"""

from __future__ import annotations

import logging

import pytest
from network_share_literal_walks import delta_kinds, load, walk, worlds
from network_share_walk_deltas import DELTA_KINDS, EXPECTED_DELTAS

GOLDEN = load()
NEUTRAL = {entry["case"]["world"] for entry in GOLDEN if entry["case"]["n_neutral"]}


def test_the_golden_file_holds_the_generated_worlds() -> None:
    assert [entry["case"] for entry in GOLDEN] == worlds()


def test_both_kinds_of_world_are_recorded() -> None:
    assert 50 <= len(NEUTRAL) <= len(GOLDEN) - 50


def test_only_worlds_that_are_not_neutral_declare_a_delta() -> None:
    assert not NEUTRAL & EXPECTED_DELTAS.keys()
    for world, expected in EXPECTED_DELTAS.items():
        assert expected["deltas"], world
        assert set(expected["deltas"]) <= DELTA_KINDS, world


def test_each_declared_delta_names_why_the_world_is_not_neutral() -> None:
    cases = {entry["case"]["world"]: entry for entry in GOLDEN}
    for world, expected in EXPECTED_DELTAS.items():
        assert set(expected["deltas"]) == delta_kinds(cases[world]["case"]), world
        # A declaration that no longer differs from the recording would hide a regression there.
        assert expected["walk"] != cases[world]["walk"], world


def test_neutral_worlds_have_no_reason_to_differ() -> None:
    for entry in GOLDEN:
        assert bool(delta_kinds(entry["case"])) is not entry["case"]["n_neutral"], entry["case"]["world"]


@pytest.mark.parametrize("entry", GOLDEN, ids=lambda entry: f"world{entry['case']['world']}")
async def test_a_literal_walk_matches_the_recorded_one(entry: dict) -> None:
    world = entry["case"]["world"]
    expected = EXPECTED_DELTAS[world]["walk"] if world in EXPECTED_DELTAS else entry["walk"]
    logging.disable(logging.CRITICAL)
    try:
        assert await walk(entry["case"]) == expected
    finally:
        logging.disable(logging.NOTSET)
