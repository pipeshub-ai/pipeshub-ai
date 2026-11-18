"""The extra-writers seam of the kg registry and guard: a build may classify provider methods beyond ``WRITERS``; the default adds none."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.postgres.kg import guard, registry
from app.services.graph_db.postgres.kg.registry import (
    EXTRA_WRITERS,
    NO_PROJECTION,
    PROJECTED,
    WRITERS,
    UnclassifiedProviderWrite,
    register_writers,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PG_KG_GUARD", "strict")
    before = dict(EXTRA_WRITERS)
    yield
    EXTRA_WRITERS.clear()
    EXTRA_WRITERS.update(before)


def _check_as(name: str) -> None:
    token = guard.current_writer.set((name,))
    try:
        guard.check(None, "")      # type: ignore[arg-type]
    finally:
        guard.current_writer.reset(token)


def test_default_is_empty() -> None:
    assert EXTRA_WRITERS == {}
    assert registry.EXTRA_WRITERS is EXTRA_WRITERS


def test_unclassified_name_is_refused_by_default() -> None:
    with pytest.raises(UnclassifiedProviderWrite):
        _check_as("seam_only_writer")


def test_a_listed_writer_still_passes() -> None:
    _check_as(next(iter(WRITERS)))


def test_registered_name_passes_the_guard() -> None:
    register_writers({"seam_only_writer": (PROJECTED, "")})
    _check_as("seam_only_writer")


def test_registered_name_is_found_below_the_outermost_frame() -> None:
    register_writers({"seam_only_writer": (PROJECTED, "")})
    token = guard.current_writer.set(("not_listed", "seam_only_writer"))
    try:
        guard.check(None, "")      # type: ignore[arg-type]
    finally:
        guard.current_writer.reset(token)


def test_a_name_in_writers_is_refused_and_nothing_is_added() -> None:
    taken = next(iter(WRITERS))
    with pytest.raises(ValueError, match=taken):
        register_writers({"seam_only_writer": (PROJECTED, ""), taken: (PROJECTED, "")})
    assert EXTRA_WRITERS == {}


def test_writers_is_untouched_by_registration() -> None:
    snapshot = dict(WRITERS)
    register_writers({"seam_only_writer": (NO_PROJECTION, "kept out of the access data")})
    assert WRITERS == snapshot


@pytest.mark.parametrize("entry", [("projected", "why"), ("no_projection", ""), ("other", "")])
def test_a_malformed_classification_is_refused(entry: tuple[str, str]) -> None:
    with pytest.raises(ValueError):
        register_writers({"seam_only_writer": entry})
    assert EXTRA_WRITERS == {}


def test_a_name_already_registered_is_not_reclassified() -> None:
    register_writers({"seam_only_writer": (PROJECTED, "")})
    with pytest.raises(ValueError, match="seam_only_writer"):
        register_writers({"seam_only_writer": (NO_PROJECTION, "now not projected")})
    assert EXTRA_WRITERS == {"seam_only_writer": (PROJECTED, "")}


@pytest.mark.parametrize("mapping", [{"": (PROJECTED, "")}, {5: (PROJECTED, "")}, {"w": (NO_PROJECTION, None)}, {"w": (PROJECTED, 3)}, {"w": None}, {"w": (PROJECTED,)}])  # type: ignore[dict-item]
def test_empty_or_non_string_names_and_reasons_are_refused(mapping: dict) -> None:
    with pytest.raises(ValueError):
        register_writers(mapping)
    assert EXTRA_WRITERS == {}
