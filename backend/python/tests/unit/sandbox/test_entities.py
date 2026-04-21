"""Tests for sandbox-related entity updates in app.models.entities."""

import pytest


class TestArtifactType:
    def test_import(self):
        from app.models.entities import ArtifactType
        assert ArtifactType is not None

    def test_values(self):
        from app.models.entities import ArtifactType
        assert ArtifactType.CODE_OUTPUT == "CODE_OUTPUT"
        assert ArtifactType.CHART == "CHART"
        assert ArtifactType.DOCUMENT == "DOCUMENT"
        assert ArtifactType.IMAGE == "IMAGE"
        assert ArtifactType.SPREADSHEET == "SPREADSHEET"
        assert ArtifactType.PRESENTATION == "PRESENTATION"
        assert ArtifactType.DATA_FILE == "DATA_FILE"
        assert ArtifactType.OTHER == "OTHER"

    def test_member_count(self):
        from app.models.entities import ArtifactType
        assert len(ArtifactType) == 8

    def test_is_str_enum(self):
        from app.models.entities import ArtifactType
        assert isinstance(ArtifactType.CHART, str)


class TestLifecycleStatus:
    def test_values(self):
        from app.models.entities import LifecycleStatus
        assert LifecycleStatus.DRAFT == "DRAFT"
        assert LifecycleStatus.PUBLISHED == "PUBLISHED"
        assert LifecycleStatus.ARCHIVED == "ARCHIVED"
        assert LifecycleStatus.REJECTED == "REJECTED"
        assert LifecycleStatus.UNKNOWN == "UNKNOWN"


class TestRecordTypeArtifact:
    def test_artifact_in_record_type(self):
        from app.models.entities import RecordType
        assert hasattr(RecordType, "ARTIFACT")
        assert RecordType.ARTIFACT == "ARTIFACT"

    def test_others_still_present(self):
        from app.models.entities import RecordType
        assert RecordType.FILE == "FILE"
        assert RecordType.OTHERS == "OTHERS"


class TestArtifactRecordFields:
    """Test that ArtifactRecord has the expected new fields in its schema."""

    def test_has_new_fields(self):
        from app.models.entities import ArtifactRecord
        fields = ArtifactRecord.model_fields
        assert "artifact_type" in fields
        assert "source_tool" in fields
        assert "conversation_id" in fields
        assert "is_temporary" in fields
        assert "expires_at" in fields

    def test_has_base_fields(self):
        from app.models.entities import ArtifactRecord
        fields = ArtifactRecord.model_fields
        assert "description" in fields
        assert "lifecycle_status" in fields

    def test_artifact_type_default(self):
        from app.models.entities import ArtifactRecord, ArtifactType
        field = ArtifactRecord.model_fields["artifact_type"]
        assert field.default == ArtifactType.OTHER

    def test_is_temporary_default(self):
        from app.models.entities import ArtifactRecord
        field = ArtifactRecord.model_fields["is_temporary"]
        assert field.default is False

    def test_expires_at_default(self):
        from app.models.entities import ArtifactRecord
        field = ArtifactRecord.model_fields["expires_at"]
        assert field.default is None

    def test_source_tool_default(self):
        from app.models.entities import ArtifactRecord
        field = ArtifactRecord.model_fields["source_tool"]
        assert field.default is None

    def test_conversation_id_default(self):
        from app.models.entities import ArtifactRecord
        field = ArtifactRecord.model_fields["conversation_id"]
        assert field.default is None
