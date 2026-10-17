"""A field added to a record must be added to the published spec too.

The response-validation suite checks real responses against
`pipeshub-openapi.yaml`, and the record schemas there set
`additionalProperties: false`. So a field that reaches the API without being
added to the spec fails those tests — but only on the nightly, hours after it
merged, and with an error that names the field rather than the change that
introduced it. `rootRecordGroupId` did exactly that.

This compares the record the model serialises against the record the spec
describes, so the mismatch fails on the pull request that causes it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.models.entities import Record
from tests.unit.models.test_entities import _record_kwargs

_REPO = Path(__file__).resolve().parents[5]
_SPEC = _REPO / "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"

# The stored record keys its document `_key`; the API returns the same value as
# `id`. That is the one rename between the two shapes.
_STORED_ONLY = {"_key"}


def _spec_record_properties() -> set[str]:
    spec = yaml.safe_load(_SPEC.read_text())
    record = spec["components"]["schemas"]["GetRecordByIdResponseSchema"]["properties"]["record"]
    assert record.get("additionalProperties") is False, (
        "this test only matters while the schema rejects unknown fields"
    )
    return set(record["properties"])


def _model_record_keys() -> set[str]:
    """The keys `Record.to_arango_base_record` actually writes.

    The method is called rather than read: some keys are set on the dict after
    it is built (`base["queuedAtTimestamp"] = ...`), so anything that scrapes
    the literal would miss them and let exactly the kind of field this test
    exists to catch through.

    The optional fields are populated so those branches are taken; a key that
    only appears for some records still has to be in the schema.
    """
    record = Record(**_record_kwargs(queued_at=1, root_record_group_id="rg-root"))
    return set(record.to_arango_base_record())


def test_the_spec_knows_every_field_a_record_carries() -> None:
    missing = sorted(_model_record_keys() - _STORED_ONLY - _spec_record_properties())
    assert missing == [], (
        "these are on the record but not in GetRecordByIdResponseSchema, so the "
        "response-validation tests will reject them: " + ", ".join(missing)
    )


def test_the_rename_this_test_allows_is_still_real() -> None:
    """`_STORED_ONLY` is an exception list, and an exception list that stops
    being true quietly turns into a hole."""
    spec = _spec_record_properties()
    assert "id" in spec, "the API is expected to return `id`"
    assert "_key" not in spec, "if the API returns `_key`, the rename is gone"
    assert _STORED_ONLY <= _model_record_keys(), (
        "the model no longer writes " + ", ".join(sorted(_STORED_ONLY - _model_record_keys()))
    )
