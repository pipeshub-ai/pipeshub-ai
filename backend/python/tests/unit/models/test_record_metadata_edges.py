"""Edge cases of record property validation, schema rules and provenance merging.

Property-based where a rule is a boundary over a domain (byte caps, integer
ranges, merge invariants), table-driven elsewhere.
"""

import json
import math
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.models.record_metadata import (
    MAX_LIST_ITEMS,
    MAX_PROPERTIES,
    MAX_STRING_BYTES,
    PIPELINE_FIELD_NAMES,
    RESERVED_PROPERTY_NAMES,
    MetadataProvenance,
    MetadataSchema,
    MetadataValidationError,
    MetadataValue,
    PropertyDefinition,
    PropertyType,
    RecordMetadata,
    coerce_value,
    searchable_lines,
    validate_values,
)
from app.services.vector_db import models as vector_models
from app.services.vector_db.const import const as vector_const

INT_MIN, INT_MAX = -(2**63), 2**63 - 1
SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _p(name: str, type_: PropertyType, **kw: object) -> PropertyDefinition:
    if type_ is PropertyType.ENUM and "enum_values" not in kw:
        kw["enum_values"] = ("OPEN", "CLOSED")
    return PropertyDefinition(name=name, type=type_, display_label=name.title(), **kw)


SCHEMA = MetadataSchema(schema_id="org:conn:1", properties=(
    _p("s", PropertyType.STRING, searchable=True),
    _p("l", PropertyType.STRING_LIST, searchable=True),
    _p("i", PropertyType.INT),
    _p("f", PropertyType.FLOAT),
    _p("b", PropertyType.BOOL),
    _p("d", PropertyType.DATETIME),
    _p("e", PropertyType.ENUM),
))
PROPS = {p.name: p for p in SCHEMA.properties}


def _refused(raw: dict[str, object]) -> MetadataValidationError:
    with pytest.raises(MetadataValidationError) as info:
        validate_values(SCHEMA, raw, source="connector:jira")
    return info.value


def _stored(name: str, value: object) -> object:
    return validate_values(SCHEMA, {name: value}, source="connector:jira").values[name].value


# --- strings: the cap is UTF-8 bytes, not characters -----------------------------------

@pytest.mark.parametrize("char", ["a", "é", "€", "😀"])
def test_string_cap_is_bytes_for_each_utf8_width(char: str) -> None:
    width = len(char.encode())
    fits = char * (MAX_STRING_BYTES // width)
    assert _stored("s", fits) == fits
    over = fits + "a" * (MAX_STRING_BYTES - len(fits.encode()) + 1)
    assert len(over.encode()) == MAX_STRING_BYTES + 1
    assert _refused({"s": over}).property == "s"


@SETTINGS
@given(st.text(min_size=0, max_size=1200))
def test_string_accepted_iff_at_most_1024_utf8_bytes(text: str) -> None:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:  # lone surrogates: covered separately
        return
    if size <= MAX_STRING_BYTES:
        assert _stored("s", text) == text
    else:
        assert _refused({"s": text}).property == "s"


@SETTINGS
@given(st.lists(st.text(max_size=8), min_size=0, max_size=80))
def test_list_accepted_iff_at_most_64_items(items: list[str]) -> None:
    if len(items) <= MAX_LIST_ITEMS:
        assert _stored("l", items) == items
    else:
        assert _refused({"l": items}).property == "l"


def test_list_item_cap_and_types() -> None:
    assert _stored("l", ("a", "b")) == ["a", "b"]
    assert _refused({"l": "abc"}).property == "l"
    assert _refused({"l": ["a", 1]}).property == "l"
    assert _refused({"l": ["a", None]}).property == "l"
    assert _refused({"l": ["x" * (MAX_STRING_BYTES + 1)]}).property == "l"
    assert _refused({"l": {"a"}}).property == "l"


def test_a_lone_surrogate_is_refused_or_survives_a_round_trip() -> None:
    """A string that cannot be UTF-8 must not produce a properties.json the model cannot read back."""
    from app.modules.artifacts.writer import canonical_json

    try:
        metadata = validate_values(SCHEMA, {"s": "broken \ud800 text"}, source="connector:jira")
    except MetadataValidationError:
        return
    data = canonical_json(metadata.to_json_dict())
    assert RecordMetadata.model_validate_json(data) == metadata


# --- integers: int64, and bool is not an int ------------------------------------------

@SETTINGS
@given(st.integers(min_value=-(2**70), max_value=2**70))
def test_int_accepted_iff_int64(value: int) -> None:
    if INT_MIN <= value <= INT_MAX:
        assert _stored("i", value) == value
    else:
        assert _refused({"i": value}).property == "i"


@pytest.mark.parametrize("value", [INT_MIN, INT_MAX, 0, -1])
def test_int64_bounds_are_inclusive(value: int) -> None:
    assert _stored("i", value) == value
    assert _stored("d", value) == value


@pytest.mark.parametrize("value", [INT_MIN - 1, INT_MAX + 1])
def test_one_past_int64_is_refused(value: int) -> None:
    assert _refused({"i": value}).property == "i"
    assert _refused({"d": value}).property == "d"


@pytest.mark.parametrize("name", ["i", "f", "d"])
@pytest.mark.parametrize("value", [True, False])
def test_bool_is_not_a_number(name: str, value: bool) -> None:
    assert _refused({name: value}).property == name


@pytest.mark.parametrize("value", [0, 1, "true", "false", None])
def test_bool_takes_only_bool(value: object) -> None:
    if value is None:
        assert validate_values(SCHEMA, {"b": None}, source="x").values == {}
        return
    assert _refused({"b": value}).property == "b"


@pytest.mark.parametrize("value", [1.0, "1", 1.5])
def test_int_refuses_non_int(value: object) -> None:
    assert _refused({"i": value}).property == "i"


# --- floats -------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, float("nan")])
def test_non_finite_floats_are_refused(value: float) -> None:
    assert _refused({"f": value}).property == "f"


@pytest.mark.parametrize("value", [2**1024, 10**400, -(2**2000)], ids=["2^1024", "10^400", "-2^2000"])
def test_an_int_too_large_for_a_float_is_a_validation_error(value: int) -> None:
    """Any refusal is a MetadataValidationError naming the property, never another exception."""
    assert _refused({"f": value}).property == "f"


@SETTINGS
@given(st.floats(allow_nan=True, allow_infinity=True))
def test_float_accepted_iff_finite(value: float) -> None:
    if math.isfinite(value):
        stored = _stored("f", value)
        assert stored == value and isinstance(stored, float)
        assert json.loads(json.dumps(stored)) == value
    else:
        assert _refused({"f": value}).property == "f"


def test_int_widens_to_float() -> None:
    stored = _stored("f", 3)
    assert stored == 3.0 and isinstance(stored, float)


# --- enums are case-sensitive ---------------------------------------------------------------

@pytest.mark.parametrize("value", ["open", "Open", "OPEN ", " OPEN", "OPEN\n", "ＯＰＥＮ"])
def test_enum_is_exact(value: str) -> None:
    assert _refused({"e": value}).property == "e"


def test_enum_member_value_passes() -> None:
    assert _stored("e", "OPEN") == "OPEN"


# --- datetimes ------------------------------------------------------------------------------

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-01-02T03:04:05Z", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
    ("2026-01-02T03:04:05+00:00", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
    ("2026-01-02T05:04:05+02:00", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
    ("2026-01-01T22:04:05-05:00", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
    ("2026-01-02T08:34:05+05:30", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
    ("2024-02-29T00:00:00Z", datetime(2024, 2, 29, tzinfo=UTC)),
    ("2026-12-31T23:59:59.999Z", datetime(2026, 12, 31, 23, 59, 59, 999000, tzinfo=UTC)),
    ("1969-12-31T23:59:59Z", datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC)),
])
def test_iso_with_offset_is_utc_epoch_ms(raw: str, expected: datetime) -> None:
    assert _stored("d", raw) == (expected - EPOCH) // timedelta(milliseconds=1)


@pytest.mark.parametrize(("raw", "expected_ms"), [
    # Year 1 at +14:00 is an instant before 0001-01-01T00:00Z; still representable.
    ("0001-01-01T00:00:00+14:00", -62135596800000 - 14 * 3_600_000),
    ("9999-12-31T23:59:59-14:00", 253402300799000 + 14 * 3_600_000),
])
def test_calendar_extremes_with_offsets(raw: str, expected_ms: int) -> None:
    assert _stored("d", raw) == expected_ms


def test_sub_millisecond_floors() -> None:
    assert _stored("d", "1970-01-01T00:00:00.0009Z") == 0
    assert _stored("d", "1969-12-31T23:59:59.9995Z") == -1


@pytest.mark.parametrize("raw", [
    "2026-01-02T03:04:05",      # no offset
    "2026-01-02",               # a date has no instant
    "2026-02-29T00:00:00Z",     # not a leap year
    "2026-01-01T23:59:60Z",     # leap second
    "2026-13-01T00:00:00Z",
    "2026-01-01T00:00:00+25:00",
    "yesterday",
    "",
    " 2026-01-02T03:04:05Z",
    1.5,
    1767225600000.0,
    [1],
])
def test_datetimes_without_a_known_instant_are_refused(raw: object) -> None:
    assert _refused({"d": raw}).property == "d"


@SETTINGS
@given(st.datetimes(timezones=st.timezones()))
def test_any_aware_datetime_round_trips_to_its_instant(moment: datetime) -> None:
    raw = moment.isoformat()
    ms = _stored("d", raw)
    assert ms == (moment - EPOCH) // timedelta(milliseconds=1)
    assert INT_MIN <= ms <= INT_MAX


# --- none and names ------------------------------------------------------------------------

def test_none_is_absent_for_every_type() -> None:
    assert validate_values(SCHEMA, dict.fromkeys(PROPS), source="x").values == {}


def test_empty_values_are_kept_as_given() -> None:
    held = validate_values(SCHEMA, {"s": "", "l": []}, source="x").values
    assert held["s"].value == "" and held["l"].value == []


@pytest.mark.parametrize("name", sorted(RESERVED_PROPERTY_NAMES))
def test_every_reserved_name_is_refused_even_with_a_none_value(name: str) -> None:
    assert _refused({name: None}).property == name
    with pytest.raises(ValidationError, match="reserved"):
        _p(name, PropertyType.STRING)


def test_every_vector_const_field_is_reserved() -> None:
    for attr, value in vars(vector_const).items():
        if attr.endswith("_FIELD") and isinstance(value, str):
            assert value.rsplit(".", 1)[-1] in RESERVED_PROPERTY_NAMES, attr
    for field, _ in vector_const.PAYLOAD_KEYWORD_INDEXES:
        assert field.rsplit(".", 1)[-1] in RESERVED_PROPERTY_NAMES, field


def test_every_known_vector_point_metadata_field_is_reserved() -> None:
    """The point metadata the vector store writes (VectorChunkMetadata, the summary and table points)
    sits beside future properties; a property of the same name would shadow it."""
    known = set(vector_models.VectorChunkMetadata.__dataclass_fields__)
    known |= {"isRecordSummary", "blockGroupIndex"}
    assert known - RESERVED_PROPERTY_NAMES == set()


@pytest.mark.parametrize("variant", ["orgID", "orgid", "virtualRecordID", "blockid", "connectorids", "pageContent"])
def test_case_variants_of_reserved_names(variant: str) -> None:
    """Informational: case variants are distinct keys in every payload store, so they are allowed."""
    prop = _p(variant, PropertyType.STRING)
    assert prop.name == variant


@pytest.mark.parametrize("name", ["", "A", "1a", "_a", "a-b", "a.b", "a b", "a" * 65, "é", "orgId\n", "a/b"])
def test_names_outside_the_pattern_are_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        _p(name, PropertyType.STRING)


def test_unknown_name_names_itself_and_wins_in_sorted_order() -> None:
    error = _refused({"zzz": 1, "aaa": 1, "i": "bad"})
    assert error.property == "aaa"
    assert "aaa" in str(error)


def test_a_reserved_name_is_reported_before_an_unknown_one_after_it() -> None:
    assert _refused({"zzz": 1, "orgId": "x"}).property == "orgId"


# --- schema definition rules ---------------------------------------------------------------

def test_facetable_implies_filterable_only_when_unset() -> None:
    assert _p("a", PropertyType.STRING, facetable=True).filterable is True
    with pytest.raises(ValidationError, match="facetable requires filterable"):
        _p("a", PropertyType.STRING, facetable=True, filterable=False)


@pytest.mark.parametrize("type_", [PropertyType.INT, PropertyType.FLOAT, PropertyType.BOOL,
                                   PropertyType.DATETIME, PropertyType.ENUM])
def test_only_strings_are_searchable(type_: PropertyType) -> None:
    with pytest.raises(ValidationError, match="searchable"):
        _p("a", type_, searchable=True)


@pytest.mark.parametrize(("values", "match"), [
    ((), "needs enum_values"),
    (("A", "A"), "repeat"),
    (("A", ""), "empty or too long"),
    (("A", "€" * 342), "empty or too long"),
])
def test_enum_values_rules(values: tuple[str, ...], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        _p("a", PropertyType.ENUM, enum_values=values)


def test_enum_value_at_the_byte_cap_passes() -> None:
    assert _p("a", PropertyType.ENUM, enum_values=("€" * 341 + "a",)).enum_values


def test_enum_values_on_a_non_enum_are_refused() -> None:
    with pytest.raises(ValidationError, match="enum properties only"):
        _p("a", PropertyType.STRING, enum_values=("A",))


def test_display_label_bounds() -> None:
    with pytest.raises(ValidationError):
        PropertyDefinition(name="a", type=PropertyType.STRING, display_label="")
    with pytest.raises(ValidationError):
        PropertyDefinition(name="a", type=PropertyType.STRING, display_label="x" * 257)


def test_property_count_cap() -> None:
    props = tuple(_p(f"p{i}", PropertyType.STRING) for i in range(MAX_PROPERTIES))
    assert len(MetadataSchema(schema_id="s", properties=props).properties) == MAX_PROPERTIES
    with pytest.raises(ValidationError):
        MetadataSchema(schema_id="s", properties=(*props, _p("extra", PropertyType.STRING)))


def test_unknown_semantic_label_and_type_are_refused() -> None:
    with pytest.raises(ValidationError):
        _p("a", PropertyType.STRING, semantic_label="summary")
    with pytest.raises(ValidationError):
        PropertyDefinition(name="a", type="string", display_label="A")  # enum case


def test_schema_errors_name_the_property() -> None:
    with pytest.raises(ValidationError) as info:
        _p("assignee", PropertyType.INT, searchable=True)
    assert "assignee" in str(info.value)


def test_definitions_round_trip_through_json() -> None:
    for prop in SCHEMA.properties:
        assert PropertyDefinition.model_validate(json.loads(json.dumps(prop.model_dump(mode="json")))) == prop


# --- values: the stored model ------------------------------------------------------------

@pytest.mark.parametrize("confidence", [-0.01, 1.01, math.nan])
def test_confidence_bounds(confidence: float) -> None:
    with pytest.raises(ValidationError):
        MetadataValue(value="x", provenance=MetadataProvenance.EXTRACTED, source="s", confidence=confidence)


def test_values_keep_their_json_type_on_reading_back() -> None:
    metadata = validate_values(
        SCHEMA, {"s": "1", "i": 1, "f": 1.0, "b": True, "l": ["1"], "d": 0, "e": "OPEN"}, source="x",
    )
    back = RecordMetadata.model_validate_json(json.dumps(metadata.to_json_dict()))
    assert {k: (type(v.value), v.value) for k, v in back.values.items()} == \
        {k: (type(v.value), v.value) for k, v in metadata.values.items()}


def test_coerce_value_on_an_unknown_type_is_a_validation_error() -> None:
    prop = PropertyDefinition.model_construct(name="x", type="WEIRD", display_label="X", enum_values=())
    with pytest.raises(MetadataValidationError):
        coerce_value(prop, "v")


# --- merging -------------------------------------------------------------------------------

def _v(value: object, source: str, provenance: MetadataProvenance) -> MetadataValue:
    return MetadataValue(value=value, provenance=provenance, source=source)


P, E = MetadataProvenance.PROVIDED, MetadataProvenance.EXTRACTED
_keys = st.sampled_from(["a", "b", "c", "d", "e"])
_sources = st.sampled_from(["connector:jira", "classify@4", "entities@2"])
_value_maps = st.dictionaries(_keys, st.tuples(_sources, st.sampled_from([P, E]), st.integers(0, 9)), max_size=5)


@SETTINGS
@given(_value_maps, st.dictionaries(_keys, st.tuples(st.sampled_from([P, E]), st.integers(0, 9)), max_size=5),
       _sources)
def test_merge_invariants(current_raw: dict, incoming_raw: dict, source: str) -> None:
    current = RecordMetadata(schema_id="s", values={k: _v(n, s, p) for k, (s, p, n) in current_raw.items()})
    incoming = RecordMetadata(schema_id="s", values={k: _v(n, source, p) for k, (p, n) in incoming_raw.items()})

    merged = current.merged_with(incoming, source=source)

    for key, held in current.values.items():
        if held.source != source:
            out = merged.values.get(key)
            assert out is not None
            # Another source's value survives, unless a provided value replaced an extracted one.
            if out != held:
                assert held.provenance is E and out.provenance is P and out.source == source
        elif key not in incoming.values:
            assert key not in merged.values  # what this source no longer says is dropped
    for key, new in incoming.values.items():
        out = merged.values[key]
        if out != new:
            assert out.source != source  # only another source's value can win
    # Never an extracted value over a provided one from someone else.
    for key, out in merged.values.items():
        before = current.values.get(key)
        if before is not None and before.provenance is P and before.source != source:
            assert out == before
    # Merging the same output twice is the same as once.
    assert merged.merged_with(incoming, source=source) == merged


def test_merge_refuses_foreign_values_and_schema_mismatch() -> None:
    current = RecordMetadata(schema_id="s:1", values={})
    with pytest.raises(ValueError, match="another source"):
        current.merged_with(RecordMetadata(schema_id="s:1", values={"a": _v(1, "x", P)}), source="y")
    with pytest.raises(ValueError, match="cannot merge"):
        current.merged_with(RecordMetadata(schema_id="s:2", values={}), source="y")
    merged = RecordMetadata(values={}).merged_with(RecordMetadata(schema_id="s:2", values={}), source="y")
    assert merged.schema_id == "s:2"


def test_merge_does_not_mutate_its_inputs() -> None:
    current = RecordMetadata(schema_id="s", values={"a": _v(1, "x", E)})
    incoming = RecordMetadata(schema_id="s", values={"a": _v(2, "y", P)})
    snapshot = (current.model_dump(), incoming.model_dump())
    current.merged_with(incoming, source="y")
    assert (current.model_dump(), incoming.model_dump()) == snapshot


# --- searchable lines -----------------------------------------------------------------------

def test_searchable_lines_skip_blank_and_non_searchable() -> None:
    metadata = validate_values(SCHEMA, {"s": "   ", "l": ["", "x", ""], "i": 3}, source="x")
    assert searchable_lines(SCHEMA, metadata) == ["L: x"]


def test_pipeline_names_include_the_payload_body_keys() -> None:
    assert {"page_content", "metadata"} <= PIPELINE_FIELD_NAMES
