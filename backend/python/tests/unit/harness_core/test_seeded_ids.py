from __future__ import annotations

from harness_core.testing import SeededIds


class TestSeededIds:
    def test_same_seed_same_sequence(self) -> None:
        a, b = SeededIds(7), SeededIds(7)
        assert [a.new_id("run"), a.new_id("run"), a.new_id("evt")] == [
            "run_000001",
            "run_000002",
            "evt_000001",
        ]
        assert [b.new_id("run"), b.new_id("run"), b.new_id("evt")] == [
            "run_000001",
            "run_000002",
            "evt_000001",
        ]
        assert [a.uuid4() for _ in range(3)] == [b.uuid4() for _ in range(3)]

    def test_different_seed_different_uuids(self) -> None:
        assert SeededIds(1).uuid4() != SeededIds(2).uuid4()
        assert SeededIds(1).uuid4().version == 4
