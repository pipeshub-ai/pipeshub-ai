"""`SkillValidator.validate_resource_path`/`validate_resource_budget` — the
single source of truth every bundled-resource write path (graph store,
filesystem store, the package importer, the REST `resource` route, and
`skill_manage`) calls before persisting a resource, so a traversal/absolute
path can never reach `_upload_staged_files` at sandbox-upload time."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.modules.providers.skills.validator import (
    MAX_RESOURCE_FILE_BYTES,
    MAX_RESOURCE_PATH_LENGTH,
    SkillFormatError,
    SkillValidator,
)


@pytest.fixture
def validator() -> SkillValidator:
    return SkillValidator()


class TestValidateResourcePath:
    @pytest.mark.parametrize(
        "path",
        [
            "scripts/unpack.py",
            "scripts/lib/util.py",
            "forms.md",
            "ooxml/schemas/a.xsd",
            "a" * 10,
        ],
    )
    def test_accepts_normal_relative_paths(self, validator: SkillValidator, path: str) -> None:
        validator.validate_resource_path(path)  # must not raise

    @pytest.mark.parametrize(
        "path",
        [
            "../a",
            "../../etc/passwd",
            "scripts/../../etc/passwd",
            "..",
        ],
    )
    def test_rejects_traversal_segments(self, validator: SkillValidator, path: str) -> None:
        with pytest.raises(SkillFormatError, match="traversal"):
            validator.validate_resource_path(path)

    def test_rejects_absolute_paths(self, validator: SkillValidator) -> None:
        with pytest.raises(SkillFormatError, match="relative"):
            validator.validate_resource_path("/etc/passwd")

    def test_rejects_backslashes(self, validator: SkillValidator) -> None:
        with pytest.raises(SkillFormatError, match="forward slashes"):
            validator.validate_resource_path("a\\b")

    def test_rejects_skill_md(self, validator: SkillValidator) -> None:
        with pytest.raises(SkillFormatError, match="reserved"):
            validator.validate_resource_path("SKILL.md")

    def test_rejects_empty_string(self, validator: SkillValidator) -> None:
        with pytest.raises(SkillFormatError, match="non-empty"):
            validator.validate_resource_path("")

    def test_rejects_non_string(self, validator: SkillValidator) -> None:
        with pytest.raises(SkillFormatError, match="non-empty"):
            validator.validate_resource_path(None)

    def test_rejects_over_length_path(self, validator: SkillValidator) -> None:
        with pytest.raises(SkillFormatError, match="character limit"):
            validator.validate_resource_path("a" * (MAX_RESOURCE_PATH_LENGTH + 1))

    def test_rejects_control_characters(self, validator: SkillValidator) -> None:
        with pytest.raises(SkillFormatError, match="control characters"):
            validator.validate_resource_path("scripts/a\x00b.py")


class TestValidateResourceBudget:
    def test_accepts_resources_within_limits(self, validator: SkillValidator) -> None:
        validator.validate_resource_budget({"scripts/a.py": "print(1)"})  # must not raise

    def test_accepts_empty_resources(self, validator: SkillValidator) -> None:
        validator.validate_resource_budget({})  # must not raise

    def test_rejects_a_single_file_over_the_per_file_cap(self, validator: SkillValidator) -> None:
        oversized = "x" * (MAX_RESOURCE_FILE_BYTES + 1)
        with pytest.raises(SkillFormatError, match="per-file limit"):
            validator.validate_resource_budget({"assets/big.bin": oversized})

    def test_rejects_totals_over_the_per_skill_cap_even_if_no_single_file_is_over(
        self, validator: SkillValidator
    ) -> None:
        chunk = "x" * (MAX_RESOURCE_FILE_BYTES // 2)
        resources = {f"assets/chunk-{i}.bin": chunk for i in range(5)}
        with pytest.raises(SkillFormatError, match="per-skill limit"):
            validator.validate_resource_budget(resources)
