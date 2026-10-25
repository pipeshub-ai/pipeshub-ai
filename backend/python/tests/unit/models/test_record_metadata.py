"""Record properties: schema definitions, value validation, provenance ownership, searchable text."""

import dataclasses
import json

import pytest
from pydantic import ValidationError

from app.models import record_metadata as rm
from app.models.blocks import SemanticMetadata
from app.models.record_metadata import (
    MAX_LIST_ITEMS,
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
    searchable_lines,
    validate_values,
)
from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db import models as vector_models
from app.services.vector_db.const import const as vector_payload

SOURCE = "connector:jira"


def _prop(name: str, type_: PropertyType, **kwargs: object) -> PropertyDefinition:
    return PropertyDefinition(name=name, type=type_, display_label=name.title(), **kwargs)


SCHEMA = MetadataSchema(schema_id="org-1:conn-1:1", properties=(
    _prop("title", PropertyType.STRING, searchable=True),
    _prop("tags", PropertyType.STRING_LIST, searchable=True),
    _prop("points", PropertyType.INT),
    _prop("score", PropertyType.FLOAT),
    _prop("urgent", PropertyType.BOOL),
    _prop("due", PropertyType.DATETIME),
    _prop("state", PropertyType.ENUM, enum_values=("OPEN", "DONE")),
))


def _error(raw: dict[str, object]) -> MetadataValidationError:
    with pytest.raises(MetadataValidationError) as raised:
        validate_values(SCHEMA, raw, source=SOURCE)
    return raised.value


class TestValues:
    def test_every_type_is_stored_as_its_type(self) -> None:
        metadata = validate_values(SCHEMA, {
            "title": "Login fails", "tags": ["auth", "web"], "points": 3, "score": 2,
            "urgent": False, "due": 1_767_225_600_000, "state": "OPEN",
        }, source=SOURCE)

        values = {k: v.value for k, v in metadata.values.items()}
        assert values == {
            "title": "Login fails", "tags": ["auth", "web"], "points": 3, "score": 2.0,
            "urgent": False, "due": 1_767_225_600_000, "state": "OPEN",
        }
        assert isinstance(values["score"], float) and isinstance(values["urgent"], bool)
        assert metadata.schema_id == "org-1:conn-1:1"
        assert {v.provenance for v in metadata.values.values()} == {MetadataProvenance.PROVIDED}
        assert {v.source for v in metadata.values.values()} == {SOURCE}

    @pytest.mark.parametrize(("raw", "expected"), [
        ("2026-01-01T00:00:00Z", 1_767_225_600_000),
        ("2026-01-01T05:30:00.250+05:30", 1_767_225_600_250),
        (0, 0),
    ])
    def test_datetime_is_epoch_ms_from_ms_or_offset_iso(self, raw: object, expected: int) -> None:
        assert validate_values(SCHEMA, {"due": raw}, source=SOURCE).values["due"].value == expected

    @pytest.mark.parametrize("raw", ["2026-01-01T00:00:00", "tomorrow", 1.5, True])
    def test_datetime_without_an_instant_is_refused(self, raw: object) -> None:
        assert _error({"due": raw}).property == "due"

    @pytest.mark.parametrize(("name", "raw"), [
        ("title", 5), ("tags", "auth"), ("tags", ["a", 1]), ("points", 1.0), ("points", True),
        ("points", 2**63), ("score", "1"), ("score", float("nan")), ("score", float("inf")),
        ("due", 2**63),
        pytest.param("score", 10**400, id="score-int-past-float-max"),
        pytest.param("score", -(10**400), id="score-int-past-float-min"),
        ("urgent", 1), ("state", "CLOSED"), ("state", 1),
    ])
    def test_a_value_of_the_wrong_type_names_its_property(self, name: str, raw: object) -> None:
        assert _error({name: raw}).property == name

    def test_string_cap_is_one_kib_of_utf8(self) -> None:
        validate_values(SCHEMA, {"title": "x" * MAX_STRING_BYTES}, source=SOURCE)
        assert _error({"title": "x" * (MAX_STRING_BYTES + 1)}).property == "title"
        # Bytes, not characters: 342 three-byte characters are 1026 bytes.
        assert _error({"title": "€" * 342}).property == "title"
        assert _error({"tags": ["x" * (MAX_STRING_BYTES + 1)]}).property == "tags"

    def test_list_cap_is_64_items(self) -> None:
        assert MAX_LIST_ITEMS == 64
        validate_values(SCHEMA, {"tags": ["t"] * 64}, source=SOURCE)
        assert _error({"tags": ["t"] * 65}).property == "tags"

    def test_none_is_absent(self) -> None:
        assert validate_values(SCHEMA, {"title": None}, source=SOURCE).values == {}

    def test_unknown_name_is_refused_naming_it(self) -> None:
        error = _error({"title": "ok", "colour": "red"})
        assert error.property == "colour"
        assert "colour" in str(error)

    @pytest.mark.parametrize("name", ["orgId", "virtualRecordId", "blockId", "rev", "connectorIds", "recordGroupIds"])
    def test_reserved_name_is_refused_naming_it(self, name: str) -> None:
        error = _error({name: "x"})
        assert error.property == name
        assert "reserved" in str(error)

    def test_extracted_values_carry_their_source_and_confidence(self) -> None:
        metadata = validate_values(
            SCHEMA, {"state": "DONE"}, source="classify@4",
            provenance=MetadataProvenance.EXTRACTED, confidence=0.7,
        )
        assert metadata.values["state"] == MetadataValue(
            value="DONE", provenance=MetadataProvenance.EXTRACTED, source="classify@4", confidence=0.7,
        )


class TestReservedNames:
    def test_every_vector_payload_field_is_reserved(self) -> None:
        fields = [v for k, v in vars(vector_payload).items() if k.endswith("_FIELD") and isinstance(v, str)]
        assert fields
        for field in fields:
            assert field.rsplit(".", 1)[-1] in RESERVED_PROPERTY_NAMES

    def test_derived_from_the_payload_constants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vector_payload, "NEW_FILTER_FIELD", "metadata.addedLaterKey", raising=False)
        assert "addedLaterKey" in rm._vector_payload_keys()

    def test_derived_from_the_point_models(self) -> None:
        @dataclasses.dataclass
        class Grown(vector_models.VectorChunkMetadata):
            addedLaterFlag: bool | None = None

        assert {f.name for f in dataclasses.fields(vector_models.VectorChunkMetadata)} <= RESERVED_PROPERTY_NAMES
        assert {f.name for f in dataclasses.fields(vector_models.VectorChunkPayload)} <= RESERVED_PROPERTY_NAMES
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(rm, "VectorChunkMetadata", Grown)
            assert "addedLaterFlag" in rm._vector_payload_keys()

    def test_the_summary_points_metadata_is_reserved(self) -> None:
        document = VectorStore._build_record_summary_document(
            "rec-1", "vr-1", "org-1", SemanticMetadata(summary="A summary."),
        )
        assert document is not None
        assert set(document.metadata) <= RESERVED_PROPERTY_NAMES

    def test_pipeline_names_are_reserved(self) -> None:
        assert {"orgId", "virtualRecordId", "blockId", "rev", "connectorIds", "recordGroupIds"} <= PIPELINE_FIELD_NAMES
        assert PIPELINE_FIELD_NAMES <= RESERVED_PROPERTY_NAMES


class TestDefinitions:
    @pytest.mark.parametrize("name", ["orgId", "rev", "connectorIds"])
    def test_a_schema_cannot_define_a_reserved_name(self, name: str) -> None:
        with pytest.raises(ValidationError, match=name):
            _prop(name, PropertyType.STRING)

    @pytest.mark.parametrize("name", ["Status", "1st", "", "has-dash", "a" * 65])
    def test_names_follow_the_pattern(self, name: str) -> None:
        with pytest.raises(ValidationError):
            _prop(name, PropertyType.STRING)

    def test_facetable_implies_filterable(self) -> None:
        assert _prop("kind", PropertyType.STRING, facetable=True).filterable is True
        with pytest.raises(ValidationError, match="facetable requires filterable"):
            _prop("kind", PropertyType.STRING, facetable=True, filterable=False)

    @pytest.mark.parametrize("type_", [PropertyType.INT, PropertyType.FLOAT, PropertyType.BOOL, PropertyType.DATETIME])
    def test_searchable_is_for_strings_only(self, type_: PropertyType) -> None:
        with pytest.raises(ValidationError, match="only string properties"):
            _prop("n", type_, searchable=True)
        assert _prop("n", PropertyType.STRING_LIST, searchable=True).searchable

    def test_enum_values(self) -> None:
        with pytest.raises(ValidationError, match="needs enum_values"):
            _prop("e", PropertyType.ENUM)
        with pytest.raises(ValidationError, match="repeat"):
            _prop("e", PropertyType.ENUM, enum_values=("A", "A"))
        with pytest.raises(ValidationError, match="enum properties only"):
            _prop("e", PropertyType.STRING, enum_values=("A",))

    def test_a_schema_has_unique_names_and_semantic_labels(self) -> None:
        with pytest.raises(ValidationError, match="names repeat"):
            MetadataSchema(schema_id="s", properties=(_prop("a", PropertyType.STRING),) * 2)
        with pytest.raises(ValidationError, match="semantic label"):
            MetadataSchema(schema_id="s", properties=(
                _prop("a", PropertyType.STRING, semantic_label="owner"),
                _prop("b", PropertyType.STRING, semantic_label="owner"),
            ))


def _value(value: object, source: str, provenance: MetadataProvenance) -> MetadataValue:
    return MetadataValue(value=value, provenance=provenance, source=source)


PROVIDED, EXTRACTED = MetadataProvenance.PROVIDED, MetadataProvenance.EXTRACTED


class TestOwnership:
    def test_extracted_cannot_overwrite_provided(self) -> None:
        current = RecordMetadata(schema_id="s", values={"state": _value("OPEN", SOURCE, PROVIDED)})
        incoming = RecordMetadata(schema_id="s", values={"state": _value("DONE", "classify@4", EXTRACTED)})

        assert current.merged_with(incoming, source="classify@4").values["state"].value == "OPEN"

    def test_provided_replaces_extracted(self) -> None:
        current = RecordMetadata(schema_id="s", values={"state": _value("DONE", "classify@4", EXTRACTED)})
        incoming = RecordMetadata(schema_id="s", values={"state": _value("OPEN", SOURCE, PROVIDED)})

        merged = current.merged_with(incoming, source=SOURCE)
        assert merged.values["state"] == _value("OPEN", SOURCE, PROVIDED)

    def test_a_stage_keeps_another_stages_keys_and_replaces_its_own(self) -> None:
        current = RecordMetadata(schema_id="s", values={
            "state": _value("DONE", "classify@4", EXTRACTED),
            "title": _value("Old", "summarise@1", EXTRACTED),
            "tags": _value(["stale"], "summarise@1", EXTRACTED),
        })
        incoming = RecordMetadata(schema_id="s", values={
            "state": _value("OPEN", "summarise@1", EXTRACTED),
            "title": _value("New", "summarise@1", EXTRACTED),
        })

        merged = current.merged_with(incoming, source="summarise@1")
        assert {k: v.value for k, v in merged.values.items()} == {"state": "DONE", "title": "New"}

    def test_a_stage_cannot_write_in_another_sources_name(self) -> None:
        incoming = RecordMetadata(values={"state": _value("OPEN", SOURCE, PROVIDED)})
        with pytest.raises(ValueError, match="another source"):
            RecordMetadata().merged_with(incoming, source="classify@4")

    def test_schemas_must_agree(self) -> None:
        with pytest.raises(ValueError, match="schema"):
            RecordMetadata(schema_id="a").merged_with(RecordMetadata(schema_id="b"), source=SOURCE)
        assert RecordMetadata().merged_with(RecordMetadata(schema_id="b"), source=SOURCE).schema_id == "b"


class TestSearchableLines:
    def test_label_and_value_for_searchable_strings_in_schema_order(self) -> None:
        metadata = validate_values(
            SCHEMA, {"tags": ["auth", "web"], "points": 3, "title": "Login fails", "state": "OPEN"}, source=SOURCE,
        )
        assert searchable_lines(SCHEMA, metadata) == ["Title: Login fails", "Tags: auth, web"]

    def test_empty_values_give_no_line(self) -> None:
        metadata = validate_values(SCHEMA, {"title": " ", "tags": []}, source=SOURCE)
        assert searchable_lines(SCHEMA, metadata) == []


def test_json_round_trip() -> None:
    metadata = validate_values(SCHEMA, {
        "title": "Login fails", "tags": ["auth"], "points": 3, "score": 1.0, "urgent": True,
        "due": 1_767_225_600_000, "state": "DONE",
    }, source=SOURCE)
    as_json = metadata.to_json_dict()

    assert set(as_json) == {"schemaId", "values"}
    assert "confidence" not in as_json["values"]["title"]
    back = RecordMetadata.model_validate_json(json.dumps(as_json))
    assert back == metadata
    assert isinstance(back.values["urgent"].value, bool) and isinstance(back.values["score"].value, float)
