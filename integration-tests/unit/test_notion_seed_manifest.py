"""The pinned Notion fixture manifest loads into the suite's seed (no live services).

The manifest is written by the simulator's fixture builder. If ``NotionSeed`` gains a field the manifest
lacks, this fails here, before the stack boots, instead of on the connector shard.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from connectors.notion.notion_seed import NotionSeed, load_seed_manifest

pytestmark = pytest.mark.unit

MANIFEST = Path(__file__).resolve().parents[1] / "connectors" / "notion" / "fixtures" / "notion_sim_seed.json"


def test_manifest_has_every_seed_field_with_sets_as_sets() -> None:
    seed = load_seed_manifest(str(MANIFEST))
    for f in dataclasses.fields(NotionSeed):
        value = getattr(seed, f.name)
        if "set[" in str(f.type):
            assert isinstance(value, set), f.name
    assert seed.expected_page_ids and seed.expected_data_source_ids
    assert seed.fixture_page_id in seed.expected_page_ids
    assert seed.archived_page_id not in seed.expected_page_ids


def test_a_manifest_missing_a_field_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "seed.json"
    broken.write_text('{"run_id": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="has no"):
        load_seed_manifest(str(broken))
