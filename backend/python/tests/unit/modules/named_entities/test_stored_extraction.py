import copy
import json
from pathlib import Path

import pytest

from app.modules.named_entities.domain.models import (
    NamedEntityExtraction,
    read_stored_extraction,
    validate_record_entity_status,
)
from app.modules.named_entities.domain.values import MoneyValue

FIXTURE = Path(__file__).parent / "fixtures" / "stored_extraction_v1.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_v1_fixture_round_trips():
    extraction = read_stored_extraction(_payload())
    assert isinstance(extraction, NamedEntityExtraction)
    assert [entity.kind.value for entity in extraction.entities] == ["organization", "currency"]
    money = extraction.entities[1].value
    assert isinstance(money, MoneyValue) and money.currency == "USD"
    assert extraction.entities[0].mentions[0].surface == "Acme Inc."


def test_fields_added_by_a_newer_build_are_ignored_at_every_level():
    payload = _payload()
    payload["future_top"] = 1
    payload["stats"]["future_stat"] = 2
    payload["entities"][0]["future_entity"] = 3
    payload["entities"][0]["mentions"][0]["future_mention"] = 4
    payload["entities"][1]["value"]["future_value"] = 5
    extraction = read_stored_extraction(payload)
    assert extraction is not None
    assert len(extraction.entities) == 2


def test_unknown_value_type_is_kept_out_not_guessed():
    payload = copy.deepcopy(_payload())
    payload["entities"][1]["value"]["value_type"] = "from_the_future"
    extraction = read_stored_extraction(payload)
    assert [entity.kind.value for entity in extraction.entities] == ["organization"]
    assert extraction.status == "PARTIAL"


def test_an_entity_of_an_unknown_kind_is_dropped_alone():
    payload = _payload()
    payload["entities"][0]["kind"] = "vehicle"
    extraction = read_stored_extraction(payload)
    assert [entity.kind.value for entity in extraction.entities] == ["currency"]


def test_unknown_status_reads_as_partial_and_unknown_reason_as_deterministic():
    payload = _payload()
    payload["status"] = "CANCELLED"
    payload["termination_reason"] = "window_cap"
    extraction = read_stored_extraction(payload)
    assert (extraction.status, extraction.termination_reason) == ("PARTIAL", "deterministic")
    assert len(extraction.entities) == 2


def test_unreadable_stats_fall_back_to_empty():
    payload = _payload()
    payload["stats"] = {"units": "many"}
    extraction = read_stored_extraction(payload)
    assert extraction.stats.units == 0
    assert len(extraction.entities) == 2


@pytest.mark.parametrize("payload", [None, [], "x", 3, {"entities": "nope"}])
def test_invalid_shapes_return_none(payload):
    assert read_stored_extraction(payload) is None


def test_strict_model_still_rejects_unknown_fields():
    payload = _payload()
    payload["future_top"] = 1
    with pytest.raises(ValueError):
        NamedEntityExtraction.model_validate(payload)


@pytest.mark.parametrize("status", ["NOT_STARTED", "IN_PROGRESS", "COMPLETED", "PARTIAL", "FAILED", "SKIPPED"])
def test_known_statuses_pass(status):
    assert validate_record_entity_status(status) == status


@pytest.mark.parametrize("status", ["", "completed", "DONE", None])
def test_unknown_statuses_are_rejected(status):
    with pytest.raises(ValueError):
        validate_record_entity_status(status)
