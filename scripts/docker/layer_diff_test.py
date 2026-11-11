#!/usr/bin/env python3
"""Fixture checks for scripts/docker/layer_diff.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import layer_diff  # noqa: E402


LABELS = {
    "org.pipeshub.base-hash": "abc",
    "org.pipeshub.npm-lock-hash": "def",
}


def image(layers: list[dict[str, object]], labels: dict[str, str] | None = None) -> dict[str, object]:
    return {"labels": dict(labels if labels is not None else LABELS), "layers": layers}


def test_new_large_layer_fails_when_hashes_match() -> None:
    previous = image([{"digest": "sha256:old", "size": 100}])
    current = image(
        [
            {"digest": "sha256:old", "size": 100},
            {"digest": "sha256:new", "size": 21 * 1024 * 1024},
        ]
    )
    failed, message = layer_diff.compare(previous, current)
    assert failed, message
    assert "20 MB" in message


def test_small_new_layer_passes() -> None:
    previous = image([{"digest": "sha256:old", "size": 100}])
    current = image(
        [
            {"digest": "sha256:old", "size": 100},
            {"digest": "sha256:new", "size": 1024},
        ]
    )
    failed, _message = layer_diff.compare(previous, current)
    assert not failed


def test_hash_change_skips() -> None:
    previous = image([{"digest": "sha256:old", "size": 100}])
    labels = dict(LABELS)
    labels["org.pipeshub.base-hash"] = "other"
    current = image([{"digest": "sha256:new", "size": 80 * 1024 * 1024}], labels)
    failed, message = layer_diff.compare(previous, current)
    assert not failed, message
    assert message.startswith("skip:")


def test_missing_label_skips() -> None:
    previous = image([{"digest": "sha256:old", "size": 100}], {})
    current = image([{"digest": "sha256:new", "size": 80 * 1024 * 1024}])
    failed, message = layer_diff.compare(previous, current)
    assert not failed
    assert "missing" in message


def main() -> None:
    test_new_large_layer_fails_when_hashes_match()
    test_small_new_layer_passes()
    test_hash_change_skips()
    test_missing_label_skips()
    print("layer_diff_test: ok")


if __name__ == "__main__":
    main()
