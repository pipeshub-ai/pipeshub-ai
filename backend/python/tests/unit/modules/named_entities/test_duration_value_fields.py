"""A duration is written as a quantity of time in seconds, so the quantity filter
(and its (orgId, dimension, siValue) index) answers "latency above 200 ms"."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.graph_writer import _value_fields
from app.modules.named_entities.normalizers.dates import (
    NormalizationContext,
    normalize_temporal,
)


@pytest.mark.parametrize(("surface", "seconds"), [("200 ms", 0.2), ("4 hours", 14_400.0), ("3 days", 259_200.0)])
def test_a_duration_with_a_length_in_seconds_is_a_time_quantity(surface: str, seconds: float) -> None:
    fields = _value_fields(SimpleNamespace(value=normalize_temporal(EntityKind.DURATION, surface, NormalizationContext())))
    assert (fields["dimension"], fields["siValue"], fields["durationSeconds"]) == ("time", seconds, seconds)


def test_a_duration_with_no_fixed_length_is_no_quantity() -> None:
    fields = _value_fields(SimpleNamespace(value=normalize_temporal(EntityKind.DURATION, "3 months", NormalizationContext())))
    assert fields["durationSeconds"] is None and "siValue" not in fields and "dimension" not in fields


@pytest.mark.parametrize(("surface", "seconds"), [("1.1 hours", 3960.0), ("0.3 minutes", 18.0), ("250 ms", 0.25)])
def test_a_fractional_duration_is_exact_seconds(surface: str, seconds: float) -> None:
    """1.1 hours came out as 3960.0000000000005 s, so a filter ending at 3960 left it out."""
    fields = _value_fields(SimpleNamespace(value=normalize_temporal(EntityKind.DURATION, surface, NormalizationContext())))
    assert fields["siValue"] == fields["durationSeconds"] == seconds
