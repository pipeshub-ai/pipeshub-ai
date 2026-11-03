"""Display-policy unit tests — user-visible vs hidden artifacts."""

from app.models.entities import ArtifactType, ArtifactVisibility
from app.services.artifact_registry.gallery import ArtifactDisplayPolicy


class TestArtifactDisplayPolicy:
    def test_visible_explicit(self):
        assert ArtifactDisplayPolicy.is_user_visible(
            artifact_type=ArtifactType.IMAGE,
            is_temporary=False,
            visibility=ArtifactVisibility.VISIBLE,
        )

    def test_missing_visibility_is_visible(self):
        assert ArtifactDisplayPolicy.is_user_visible(
            artifact_type="CHART",
            is_temporary=False,
            visibility=None,
        )

    def test_staging_hidden(self):
        assert not ArtifactDisplayPolicy.is_user_visible(
            artifact_type=ArtifactType.DOCUMENT,
            visibility=ArtifactVisibility.STAGING,
        )

    def test_tool_result_hidden(self):
        assert not ArtifactDisplayPolicy.is_user_visible(
            artifact_type=ArtifactType.TOOL_RESULT,
            visibility=ArtifactVisibility.VISIBLE,
        )

    def test_temporary_hidden(self):
        assert not ArtifactDisplayPolicy.is_user_visible(
            artifact_type=ArtifactType.CODE,
            is_temporary=True,
            visibility=ArtifactVisibility.VISIBLE,
        )

    def test_all_fields_missing_is_visible(self):
        assert ArtifactDisplayPolicy.is_user_visible()

    def test_visible_plus_tool_result_hidden(self):
        assert not ArtifactDisplayPolicy.is_user_visible_doc(
            {"artifactType": "TOOL_RESULT", "visibility": "VISIBLE"}
        )

    def test_staging_and_temporary_hidden(self):
        assert not ArtifactDisplayPolicy.is_user_visible_doc(
            {"artifactType": "IMAGE", "visibility": "STAGING", "isTemporary": True}
        )

    def test_unrecognized_visibility_hidden(self):
        assert not ArtifactDisplayPolicy.is_user_visible(visibility="INTERNAL")
