"""A date written without a year gets the year nearest the record's creation, when the
record is written; an upload, or a date no nearby year has, stays unresolved."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import Mention, NamedEntity
from app.modules.named_entities.merge import anchor_year_less_dates, entity_identity
from app.modules.named_entities.normalizers.dates import (
    NormalizationContext,
    normalize_temporal,
)

K = EntityKind


def _ms(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1000)


def _ctx(reference: int | None, tz: str = "UTC") -> NormalizationContext:
    return NormalizationContext(reference_time_ms=reference, tz=tz, anchor_years=True)


@pytest.mark.parametrize(("text", "reference", "expected"), [
    ("September 10", _ms(2025, 10, 1), "2025-09-10"),
    ("10 Sep", _ms(2025, 10, 1), "2025-09-10"),
    # Nearest, across a new year either way.
    ("January 5", _ms(2025, 12, 20), "2026-01-05"),
    ("December 30", _ms(2026, 1, 3), "2025-12-30"),
])
def test_a_year_less_day_takes_the_year_nearest_the_record(text: str, reference: int, expected: str) -> None:
    value = normalize_temporal(K.DATE, text, _ctx(reference))
    assert (value.timex, value.ambiguous, value.anchor_source) == (expected, False, "record")
    assert value.start_ms is not None


def test_a_tie_goes_to_the_past() -> None:
    # July 2, 2024 (a leap year) is 183 days after Jan 1, 2024 and 183 days before Jan 1, 2025.
    value = normalize_temporal(K.DATE, "January 1", _ctx(_ms(2024, 7, 2, 12)))
    assert value.timex == "2024-01-01"


def test_without_anchoring_or_a_reference_it_stays_unresolved() -> None:
    for ctx in (NormalizationContext(reference_time_ms=_ms(2025, 10, 1)), _ctx(None)):
        value = normalize_temporal(K.DATE, "September 10", ctx)
        assert (value.timex, value.ambiguous, value.start_ms) == ("XXXX-09-10", True, None)


def test_a_day_no_nearby_year_has_stays_unresolved_rather_than_dropped() -> None:
    # Feb 29 takes the nearby leap year; a day no year has stays unresolved, never dropped.
    leap = normalize_temporal(K.DATE, "February 29", _ctx(_ms(2025, 3, 1)))
    assert leap.timex == "2024-02-29"
    impossible = normalize_temporal(K.DATE, "February 30", _ctx(_ms(2025, 3, 1)))
    assert impossible is not None and impossible.ambiguous


def test_the_reference_day_is_the_records_own_time_zone() -> None:
    # 2025-12-31 23:30 in New York is already Jan 1 in UTC; "January 2" is near either way,
    # but "December 31" must read as 2025 there.
    late = _ms(2026, 1, 1, 4)  # 2025-12-31 23:00 in New York
    assert normalize_temporal(K.DATE, "December 31", _ctx(late, "America/New_York")).timex == "2025-12-31"


@pytest.mark.parametrize(("text", "reference", "expected"), [
    ("9-11 September", _ms(2025, 10, 1), "2025-09-09/2025-09-11"),
    ("Dec 28 - Jan 3", _ms(2025, 12, 20), "2025-12-28/2026-01-03"),
    ("Sep - Oct", _ms(2025, 10, 1), "2025-09/2025-10"),
    # The nearest year must have both ends: 2025 has no Feb 29, so the range is 2024's.
    ("Feb 28 - Feb 29", _ms(2025, 3, 1), "2024-02-28/2024-02-29"),
])
def test_a_year_less_range_is_anchored_by_its_start(text: str, reference: int, expected: str) -> None:
    value = normalize_temporal(K.DATE_RANGE, text, _ctx(reference))
    assert (value.timex, value.anchor_source) == (expected, "record")


def _date(name: str, block: int, *, explicit_ctx: NormalizationContext | None = None) -> NamedEntity:
    display, key, value = entity_identity(K.DATE, name, explicit_ctx or NormalizationContext())
    return NamedEntity(
        kind=K.DATE, display_name=display, norm_key=key, value=value,
        mentions=[Mention(block_index=block, char_start=0, char_end=len(name), surface=name, extractor="value")],
    )


def test_writing_again_anchors_and_joins_the_explicit_date_of_the_same_day() -> None:
    explicit = _date("September 10, 2025", 0)
    year_less = _date("Sep 10", 1)
    assert year_less.value.ambiguous

    out = anchor_year_less_dates([explicit, year_less], _ctx(_ms(2025, 10, 1)))

    assert len(out) == 1
    assert [m.surface for m in out[0].mentions] == ["September 10, 2025", "Sep 10"]
    assert out[0].norm_key == explicit.norm_key


def test_a_date_no_year_fits_is_left_as_it_was() -> None:
    year_less = _date("Sep 10", 1)
    assert anchor_year_less_dates([year_less], _ctx(None)) == [year_less]


def test_the_write_anchors_to_the_records_creation_and_never_an_uploads() -> None:
    from types import SimpleNamespace

    from app.config.constants.arangodb import Connectors, OriginTypes
    from app.modules.named_entities import stage
    from app.modules.named_entities.domain.models import NamedEntityExtraction

    extraction = NamedEntityExtraction(tz="Asia/Kolkata")
    edited_later = SimpleNamespace(
        source_created_at=_ms(2023, 9, 1), source_updated_at=_ms(2026, 3, 1),
        connector_name=Connectors.CONFLUENCE, origin=OriginTypes.CONNECTOR,
    )
    ctx = stage._year_anchor(edited_later, extraction)
    assert (ctx.reference_time_ms, ctx.tz, ctx.anchor_years) == (_ms(2023, 9, 1), "Asia/Kolkata", True)

    # An upload's creation is when it was uploaded, whatever the content-dates switch says.
    upload = SimpleNamespace(
        source_created_at=_ms(2026, 3, 1), source_updated_at=None,
        connector_name=Connectors.KNOWLEDGE_BASE, origin=OriginTypes.UPLOAD,
    )
    assert stage._year_anchor(upload, extraction) is None

    # No source creation time: when the record was first stored, never its last update,
    # so an edit does not move an unchanged "Sep 10" to another year.
    no_source_created = SimpleNamespace(
        source_created_at=None, created_at=_ms(2025, 10, 1), source_updated_at=_ms(2026, 3, 1),
        connector_name=Connectors.CONFLUENCE, origin=OriginTypes.CONNECTOR,
    )
    assert stage._year_anchor(no_source_created, extraction).reference_time_ms == _ms(2025, 10, 1)
    unknown = SimpleNamespace(
        source_created_at=None, created_at=None, source_updated_at=_ms(2026, 3, 1),
        connector_name=Connectors.CONFLUENCE, origin=OriginTypes.CONNECTOR,
    )
    assert stage._year_anchor(unknown, extraction) is None


def test_a_merged_day_keeps_the_display_the_record_wrote_in_full() -> None:
    year_less, explicit = _date("Sep 10", 0), _date("September 10, 2025", 1)
    (merged,) = anchor_year_less_dates([year_less, explicit], _ctx(_ms(2025, 10, 1)))
    assert merged.display_name == explicit.display_name
