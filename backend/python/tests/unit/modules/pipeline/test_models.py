"""Pipeline contracts: fingerprint digests (UNIT-FP-01..04), job identity, wire form."""

import pytest
from pydantic import ValidationError

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.models import (
    Priority,
    StageFingerprint,
    StageJob,
    StageState,
    StageStatePatch,
    stage_state_key,
)
from app.services.resource_governor.models import ParseTier


def _fingerprint(**overrides: object) -> StageFingerprint:
    fields: dict[str, object] = {
        "stage": "classify",
        "stage_version": 1,
        "input_digest": "text-digest",
        "config_digest": "config-digest",
    }
    fields.update(overrides)
    return StageFingerprint.model_validate(fields)


def _job(**overrides: object) -> StageJob:
    fields: dict[str, object] = {
        "stage": "classify",
        "stage_version": 2,
        "org_id": "org-1",
        "virtual_record_id": "vr-1",
        "rev": "abc123",
        "record_ids": ("rec-1", "rec-2"),
        "connector_id": "conn-1",
        "tier": ParseTier.LIGHT,
        "priority": Priority.BULK,
        "trigger": "embed",
        "text_digest": "t",
        "blocks_digest": "b",
        "text_chars": 10,
        "has_tables": False,
        "has_images": True,
    }
    fields.update(overrides)
    return StageJob.model_validate(fields)


class TestStageFingerprint:
    def test_equal_inputs_give_an_equal_digest(self) -> None:
        assert _fingerprint().digest() == _fingerprint().digest()

    @pytest.mark.parametrize(
        "change",
        [{"stage_version": 2}, {"input_digest": "other-text"}, {"config_digest": "other-config"}, {"stage": "embed"}],
    )
    def test_every_component_changes_the_digest(self, change: dict[str, object]) -> None:
        assert _fingerprint(**change).digest() != _fingerprint().digest()

    def test_digest_is_24_hex_characters(self) -> None:
        digest = _fingerprint().digest()
        assert len(digest) == 24
        int(digest, 16)


class TestStageJob:
    def test_identity_is_derived_from_revision_and_stage(self) -> None:
        job = _job()
        assert job.job_id == "vr-1:abc123:classify@2"
        assert job.state_key == stage_state_key("vr-1", "abc123", "classify") == "vr-1:abc123:classify"

    def test_wire_form_is_camel_case_and_round_trips(self) -> None:
        job = _job()
        payload = job.model_dump(mode="json", by_alias=True)
        assert payload["virtualRecordId"] == "vr-1"
        assert payload["recordIds"] == ["rec-1", "rec-2"]
        assert payload["jobId"] == job.job_id
        assert StageJob.model_validate(payload) == job

    def test_is_immutable(self) -> None:
        job = _job()
        with pytest.raises(ValidationError):
            job.attempt = 2  # type: ignore[misc]

    def test_needs_at_least_one_bound_record(self) -> None:
        with pytest.raises(ValidationError):
            _job(record_ids=())

    def test_rejects_a_zero_stage_version(self) -> None:
        with pytest.raises(ValidationError):
            _job(stage_version=0)


class TestStageState:
    def test_is_built_from_a_job_and_replays_it(self) -> None:
        job = _job(force=True)
        state = StageState.from_job(job, ProgressStatus.QUEUED, now_ms=10)
        assert state.key == job.state_key
        assert state.status is ProgressStatus.QUEUED
        assert state.to_job() == job

    def test_storage_form_round_trips(self) -> None:
        state = StageState.from_job(_job(), ProgressStatus.SKIPPED, now_ms=1, reason="not applicable")
        document = state.model_dump(mode="json", by_alias=True)
        assert document["status"] == "SKIPPED"
        assert document["virtualRecordId"] == "vr-1"
        assert document["tier"] == "light"
        assert StageState.model_validate(document) == state


class TestStageStatePatch:
    def test_writes_only_explicitly_set_fields(self) -> None:
        assert StageStatePatch(reason=None, attempt=2).fields() == {"reason": None, "attempt": 2}

    def test_uses_storage_field_names(self) -> None:
        assert StageStatePatch(finished_at_ms=5).fields() == {"finishedAtMs": 5}

    def test_dispatch_patch_carries_the_new_envelope_and_clears_reason(self) -> None:
        fields = StageStatePatch.dispatch(_job(force=True)).fields()
        assert fields["force"] is True
        assert fields["recordIds"] == ["rec-1", "rec-2"]
        assert fields["reason"] is None


class TestRecordView:
    def test_a_job_carries_its_view_and_can_be_built_from_one(self) -> None:
        job = _job()
        view = job.view
        assert (view.virtual_record_id, view.rev, view.has_images, view.text_chars) == ("vr-1", "abc123", True, 10)
        rebuilt = StageJob.for_view(view, stage="classify", stage_version=2, priority=Priority.BULK, trigger="embed")
        assert rebuilt == job
