"""Every field a graph entity carries must be in the schema the checks validate against.

`assert_graph_entity_matches` runs both the expected and the actual entity through a
YAML schema that rejects any field it does not name. So a field added to one of these
models without being added to its YAML file fails every connector graph check at once,
on the nightly, hours after it merged - which is what `owner_device_id` and
`owner_device_name` did to GitLab, Jira and Linear.

This compares the two directly, so the gap is named on the pull request instead.
"""

from __future__ import annotations

from typing import get_args

import pytest
from app.models.entities import (
    AppMetadata,
    AppRole,
    AppUserGroup,
    CodeFileRecord,
    FileRecord,
    LinkRecord,
    MailRecord,
    PullRequestRecord,
    RecordGroup,
    TicketRecord,
    WebpageRecord,
)
from pydantic import BaseModel

from validation.graph_entity_validator import (
    _ENTITY_SCHEMA_LAYERS,
    merged_graph_entity_schema,
)

pytestmark = pytest.mark.unit

def _carried_fields(model: type[BaseModel]) -> set[str]:
    """The field names a real dump of `model` will carry.

    `exclude=True` fields - `Record.location`, built for LLM context and never
    stored - never reach the schema, so requiring a YAML entry for them would be
    wrong. Everything else does, including the required fields with no default
    that a `model_construct()` dump would silently leave out.
    """
    return {name for name, field in model.model_fields.items() if field.exclude is not True}


MODEL_FOR_ENTITY: dict[str, type[BaseModel]] = {
    "ticket_record": TicketRecord,
    "file_record": FileRecord,
    "pull_request_record": PullRequestRecord,
    "code_file_record": CodeFileRecord,
    "link_record": LinkRecord,
    "mail_record": MailRecord,
    "webpage_record": WebpageRecord,
    "record_group": RecordGroup,
    "app_user_group": AppUserGroup,
    "app_role": AppRole,
    "app_metadata": AppMetadata,
}


def test_the_comparison_counts_required_fields_and_skips_excluded_ones() -> None:
    """The guard is only as good as what it decides a model "carries".

    Reading a `model_construct()` dump instead drops every required field that
    has no default -- `is_file`, `record_name`, `connector_id` and the rest of
    the identifying half -- so the guard passed while blind to them. Pinned here
    rather than trusted, because nothing downstream would notice it regressing.
    """
    carried = _carried_fields(FileRecord)
    assert "is_file" in carried, "a required field with no default must count"
    assert "location" not in carried, "an exclude=True field never reaches a schema"


def test_every_entity_kind_has_a_model() -> None:
    """A new entity kind without a model here would go unchecked and look checked."""
    assert sorted(MODEL_FOR_ENTITY) == sorted(_ENTITY_SCHEMA_LAYERS)


@pytest.mark.parametrize("entity", sorted(MODEL_FOR_ENTITY))
def test_the_schema_names_every_field_the_model_carries(entity: str) -> None:
    model = MODEL_FOR_ENTITY[entity]
    declared = set(merged_graph_entity_schema(entity).fields)
    carried = _carried_fields(model)

    # Every layer, not just the last: a field on the base `Record` belongs in
    # record.yaml, and naming only the leaf sends the reader to the wrong file.
    layers = ", ".join(
        f"integration-tests/validation/schemas/{name}"
        for name in _ENTITY_SCHEMA_LAYERS[entity]
    )
    missing = sorted(carried - declared)
    assert not missing, (
        f"{model.__name__} carries {missing}, which {layers} "
        f"does not name; add each one before shipping it"
    )


def _nested_model(annotation: object) -> type[BaseModel] | None:
    """The model behind a field typed `Model` or `Optional[Model]`, else None."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    models = [
        arg for arg in get_args(annotation)
        if isinstance(arg, type) and issubclass(arg, BaseModel)
    ]
    return models[0] if len(models) == 1 else None


@pytest.mark.parametrize("entity", sorted(MODEL_FOR_ENTITY))
def test_the_schema_names_every_field_of_a_nested_model(entity: str) -> None:
    """A schema that lists a nested object's properties rejects any it does not list.

    `block_containers` is checked key by key, so `BlocksContainer.code_facts_version`
    failed every Jira and Linear graph check on the nightly while the top-level
    comparison above stayed green.
    """
    model = MODEL_FOR_ENTITY[entity]
    declared = merged_graph_entity_schema(entity).fields
    checked: list[str] = []
    gaps: list[str] = []
    for name, field in model.model_fields.items():
        nested = _nested_model(field.annotation)
        schema = declared.get(name)
        if field.exclude is True or nested is None or schema is None or not schema.properties:
            continue
        checked.append(name)
        missing = sorted(_carried_fields(nested) - set(schema.properties))
        if missing:
            gaps.append(f"{name} ({nested.__name__}) carries {missing}")

    if "block_containers" in model.model_fields:
        assert "block_containers" in checked, "block_containers must be compared, not skipped"
    assert not gaps, (
        f"{model.__name__}: {'; '.join(gaps)}, which integration-tests/validation/schemas/ "
        "does not name under that field's properties; add each one before shipping it"
    )
