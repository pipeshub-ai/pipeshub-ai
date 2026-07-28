from __future__ import annotations

import os

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import SkillFilter, SkillStatus
from app.agent_loop_lib.modules.providers.skills.filesystem_store import FilesystemSkillStore
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError


def _skill_md(name: str, description: str = "does things", body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def _write_raw_skill(root: str, name: str, content: str, *subdirs: str) -> str:
    skill_dir = os.path.join(root, *subdirs, name)
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(content)
    return skill_dir


class TestConstructionAndRefresh:
    async def test_creates_primary_dir_if_missing(self, tmp_path) -> None:
        primary = os.path.join(tmp_path, "skills")
        assert not os.path.isdir(primary)

        FilesystemSkillStore(primary)

        assert os.path.isdir(primary)

    async def test_starts_with_no_skills(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        assert await store.list_skills() == []

    async def test_primary_root_shadows_extra_root_on_name_collision(self, tmp_path) -> None:
        primary = tmp_path / "primary"
        extra = tmp_path / "extra"
        _write_raw_skill(str(extra), "shared", _skill_md("shared", description="from extra"))
        _write_raw_skill(str(primary), "shared", _skill_md("shared", description="from primary"))

        store = FilesystemSkillStore(str(primary), extra_skills_dirs=[str(extra)])
        skill = await store.get_skill("shared")

        assert skill is not None
        assert skill.metadata.description == "from primary"

    async def test_extra_root_skill_is_visible_when_not_shadowed(self, tmp_path) -> None:
        primary = tmp_path / "primary"
        extra = tmp_path / "extra"
        _write_raw_skill(str(extra), "extra-only", _skill_md("extra-only"))

        store = FilesystemSkillStore(str(primary), extra_skills_dirs=[str(extra)])
        names = [m.name for m in await store.list_skills()]

        assert names == ["extra-only"]

    async def test_missing_extra_root_is_silently_skipped(self, tmp_path) -> None:
        primary = tmp_path / "primary"
        missing_extra = tmp_path / "does-not-exist"

        store = FilesystemSkillStore(str(primary), extra_skills_dirs=[str(missing_extra)])

        assert await store.list_skills() == []

    async def test_category_and_subcategory_inferred_from_directory(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        _write_raw_skill(str(primary), "deploy", _skill_md("deploy"), "devops", "kubernetes")

        store = FilesystemSkillStore(str(primary))
        skill = await store.get_skill("deploy")

        assert skill is not None
        assert skill.metadata.category == "devops"
        assert skill.metadata.subcategory == "kubernetes"

    async def test_underscore_and_dot_prefixed_dirs_are_never_scanned(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        _write_raw_skill(str(primary), "real-skill", _skill_md("real-skill"))
        _write_raw_skill(str(primary), "hidden", _skill_md("hidden"), "_meta")
        _write_raw_skill(str(primary), "vcs", _skill_md("vcs"), ".git")

        store = FilesystemSkillStore(str(primary))
        names = {m.name for m in await store.list_skills()}

        assert names == {"real-skill"}

    async def test_refresh_picks_up_out_of_process_changes(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))
        assert await store.list_skills() == []

        _write_raw_skill(str(primary), "added-later", _skill_md("added-later"))
        assert await store.list_skills() == []  # cache not yet refreshed

        store.refresh()
        names = [m.name for m in await store.list_skills()]
        assert names == ["added-later"]

    async def test_corrupt_skill_file_is_skipped_not_raised(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        _write_raw_skill(str(primary), "broken", "not even frontmatter")
        _write_raw_skill(str(primary), "good", _skill_md("good"))

        store = FilesystemSkillStore(str(primary))

        assert await store.get_skill("broken") is None
        names = [m.name for m in await store.list_skills()]
        assert names == ["good"]


class TestCreateSkill:
    async def test_create_writes_skill_md_and_returns_metadata(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))

        metadata = await store.create_skill("my-skill", _skill_md("my-skill", description="a new skill"))

        assert metadata.name == "my-skill"
        assert metadata.description == "a new skill"
        skill_md_path = primary / "my-skill" / "SKILL.md"
        assert skill_md_path.is_file()
        assert "a new skill" in skill_md_path.read_text(encoding="utf-8")

    async def test_created_skill_is_immediately_readable(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("my-skill", _skill_md("my-skill"))

        assert await store.exists("my-skill") is True
        skill = await store.get_skill("my-skill")
        assert skill is not None
        assert skill.name == "my-skill"

    async def test_create_duplicate_name_raises_registry_error(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("dup", _skill_md("dup"))

        try:
            await store.create_skill("dup", _skill_md("dup"))
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")

    async def test_create_with_invalid_frontmatter_name_raises_format_error(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))

        try:
            await store.create_skill("Bad_Name", _skill_md("Bad_Name"))
        except SkillFormatError:
            pass
        else:
            raise AssertionError("expected SkillFormatError")

    async def test_create_name_mismatch_with_frontmatter_raises_format_error(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))

        try:
            await store.create_skill("expected-name", _skill_md("different-name"))
        except SkillFormatError:
            pass
        else:
            raise AssertionError("expected SkillFormatError")

    async def test_create_under_category_and_subcategory_places_correct_directory(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))

        metadata = await store.create_skill(
            "deploy", _skill_md("deploy"), category="devops", subcategory="kubernetes",
        )

        assert (primary / "devops" / "kubernetes" / "deploy" / "SKILL.md").is_file()
        assert metadata.category == "devops"
        assert metadata.subcategory == "kubernetes"

    async def test_create_category_param_does_not_override_explicit_frontmatter_category(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        content = "---\nname: has-own-category\ndescription: d\nmetadata:\n  agent-loop:\n    category: from-frontmatter\n---\n\nBody.\n"

        metadata = await store.create_skill("has-own-category", content, category="dir-category")

        assert metadata.category == "from-frontmatter"

    async def test_created_skill_appears_in_subsequent_list_skills(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("alpha", _skill_md("alpha"))
        await store.create_skill("beta", _skill_md("beta"))

        names = [m.name for m in await store.list_skills()]
        assert names == ["alpha", "beta"]


class TestUpdateSkill:
    async def test_update_overwrites_content_in_place(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s", description="v1"))

        metadata = await store.update_skill("s", _skill_md("s", description="v2"))

        assert metadata.description == "v2"
        skill = await store.get_skill("s")
        assert skill.metadata.description == "v2"

    async def test_update_unknown_skill_raises_registry_error(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))

        try:
            await store.update_skill("nonexistent", _skill_md("nonexistent"))
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")

    async def test_update_skill_in_extra_root_raises_format_error(self, tmp_path) -> None:
        primary = tmp_path / "primary"
        extra = tmp_path / "extra"
        _write_raw_skill(str(extra), "readonly", _skill_md("readonly"))
        store = FilesystemSkillStore(str(primary), extra_skills_dirs=[str(extra)])

        try:
            await store.update_skill("readonly", _skill_md("readonly", description="changed"))
        except SkillFormatError:
            pass
        else:
            raise AssertionError("expected SkillFormatError")

    async def test_update_invalid_content_raises_and_does_not_corrupt_disk(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))
        await store.create_skill("s", _skill_md("s", description="original"))

        try:
            await store.update_skill("s", "garbage, no frontmatter")
        except SkillFormatError:
            pass
        else:
            raise AssertionError("expected SkillFormatError")

        skill = await store.get_skill("s")
        assert skill.metadata.description == "original"


class TestPatchSkill:
    async def test_patch_replaces_matching_substring_once(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s", body="Step one.\nStep two."))

        ok = await store.patch_skill("s", "Step one.", "Step 1.")

        assert ok is True
        skill = await store.get_skill("s")
        assert skill.body == "Step 1.\nStep two."

    async def test_patch_unknown_skill_returns_false(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        assert await store.patch_skill("nope", "a", "b") is False

    async def test_patch_string_not_found_returns_false(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s", body="Only one line."))

        assert await store.patch_skill("s", "nonexistent text", "replacement") is False

    async def test_patch_ambiguous_match_returns_false(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s", body="dup dup"))

        assert await store.patch_skill("s", "dup", "single") is False

    async def test_patch_on_readonly_skill_returns_false(self, tmp_path) -> None:
        extra = tmp_path / "extra"
        _write_raw_skill(str(extra), "readonly", _skill_md("readonly", body="content here"))
        store = FilesystemSkillStore(str(tmp_path / "primary"), extra_skills_dirs=[str(extra)])

        assert await store.patch_skill("readonly", "content", "changed") is False


class TestDeleteSkill:
    async def test_delete_removes_directory_and_location(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))
        await store.create_skill("s", _skill_md("s"))

        ok = await store.delete_skill("s")

        assert ok is True
        assert not (primary / "s").exists()
        assert await store.exists("s") is False
        assert await store.get_skill("s") is None

    async def test_delete_unknown_skill_returns_false(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        assert await store.delete_skill("nope") is False

    async def test_delete_readonly_skill_returns_false_and_keeps_it(self, tmp_path) -> None:
        extra = tmp_path / "extra"
        _write_raw_skill(str(extra), "readonly", _skill_md("readonly"))
        store = FilesystemSkillStore(str(tmp_path / "primary"), extra_skills_dirs=[str(extra)])

        ok = await store.delete_skill("readonly")

        assert ok is False
        assert await store.exists("readonly") is True


class TestResources:
    async def test_write_then_read_resource(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s"))

        ok = await store.write_resource("s", "scripts/deploy.sh", "#!/bin/sh\necho hi")

        assert ok is True
        content = await store.get_resource("s", "scripts/deploy.sh")
        assert content == "#!/bin/sh\necho hi"

    async def test_write_resource_creates_nested_directories(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))
        await store.create_skill("s", _skill_md("s"))

        await store.write_resource("s", "references/deep/nested/file.md", "content")

        assert (primary / "s" / "references" / "deep" / "nested" / "file.md").is_file()

    async def test_write_resource_unknown_skill_returns_false(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        assert await store.write_resource("nope", "a.txt", "x") is False

    async def test_write_resource_readonly_skill_returns_false(self, tmp_path) -> None:
        extra = tmp_path / "extra"
        _write_raw_skill(str(extra), "readonly", _skill_md("readonly"))
        store = FilesystemSkillStore(str(tmp_path / "primary"), extra_skills_dirs=[str(extra)])

        assert await store.write_resource("readonly", "a.txt", "x") is False

    async def test_write_resource_path_traversal_is_rejected(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s"))

        ok = await store.write_resource("s", "../../../etc/passwd", "malicious")

        assert ok is False
        assert not (tmp_path / "etc" / "passwd").exists()

    async def test_get_resource_path_traversal_returns_none(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))
        await store.create_skill("s", _skill_md("s"))
        outside_secret = tmp_path / "secret.txt"
        outside_secret.write_text("top secret", encoding="utf-8")

        content = await store.get_resource("s", "../secret.txt")

        assert content is None

    async def test_get_resource_unknown_skill_returns_none(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        assert await store.get_resource("nope", "a.txt") is None

    async def test_get_resource_missing_file_returns_none(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s"))

        assert await store.get_resource("s", "scripts/missing.sh") is None

    async def test_remove_resource_deletes_file(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))
        await store.create_skill("s", _skill_md("s"))
        await store.write_resource("s", "scripts/tmp.sh", "x")

        ok = await store.remove_resource("s", "scripts/tmp.sh")

        assert ok is True
        assert not (primary / "s" / "scripts" / "tmp.sh").exists()

    async def test_remove_resource_missing_file_returns_false(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("s", _skill_md("s"))

        assert await store.remove_resource("s", "scripts/missing.sh") is False

    async def test_remove_resource_readonly_skill_returns_false(self, tmp_path) -> None:
        extra = tmp_path / "extra"
        skill_dir = _write_raw_skill(str(extra), "readonly", _skill_md("readonly"))
        os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
        with open(os.path.join(skill_dir, "scripts", "s.sh"), "w", encoding="utf-8") as f:
            f.write("x")
        store = FilesystemSkillStore(str(tmp_path / "primary"), extra_skills_dirs=[str(extra)])

        assert await store.remove_resource("readonly", "scripts/s.sh") is False


class TestDeprecateSkill:
    async def test_deprecate_sets_status_and_reason(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("old", _skill_md("old"))

        ok = await store.deprecate_skill("old", "superseded", replaced_by="new")

        assert ok is True
        skill = await store.get_skill("old")
        assert skill.metadata.status == SkillStatus.DEPRECATED
        assert skill.metadata.deprecated_reason == "superseded"
        assert skill.metadata.replaced_by == "new"

    async def test_deprecate_unknown_skill_returns_false(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        assert await store.deprecate_skill("nope", "reason") is False

    async def test_deprecate_readonly_skill_returns_false(self, tmp_path) -> None:
        extra = tmp_path / "extra"
        _write_raw_skill(str(extra), "readonly", _skill_md("readonly"))
        store = FilesystemSkillStore(str(tmp_path / "primary"), extra_skills_dirs=[str(extra)])

        assert await store.deprecate_skill("readonly", "reason") is False

    async def test_deprecate_skill_corrupted_on_disk_after_indexing_returns_false(self, tmp_path) -> None:
        primary = tmp_path / "skills"
        store = FilesystemSkillStore(str(primary))
        await store.create_skill("s", _skill_md("s"))
        # Corrupt the file directly on disk without calling refresh(), so the
        # in-memory location cache still points at it but `_load` now fails.
        with open(primary / "s" / "SKILL.md", "w", encoding="utf-8") as f:
            f.write("no frontmatter here")

        assert await store.deprecate_skill("s", "reason") is False


class TestListSkillsFilter:
    async def test_filter_by_query_matches_name_description_and_tags(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        content = "---\nname: kube-deploy\ndescription: deploy to kubernetes\nmetadata:\n  agent-loop:\n    tags: [k8s, deploy]\n---\n\nBody.\n"
        await store.create_skill("kube-deploy", content)
        await store.create_skill("unrelated", _skill_md("unrelated", description="something else"))

        results = await store.list_skills(SkillFilter(query="kubernetes"))

        assert [m.name for m in results] == ["kube-deploy"]

    async def test_filter_by_category(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("a", _skill_md("a"), category="devops")
        await store.create_skill("b", _skill_md("b"), category="writing")

        results = await store.list_skills(SkillFilter(category="devops"))

        assert [m.name for m in results] == ["a"]

    async def test_filter_by_status_excludes_non_matching(self, tmp_path) -> None:
        store = FilesystemSkillStore(str(tmp_path / "skills"))
        await store.create_skill("active-one", _skill_md("active-one"))
        await store.create_skill("dep-one", _skill_md("dep-one"))
        await store.deprecate_skill("dep-one", "old")

        results = await store.list_skills(SkillFilter(status=SkillStatus.DEPRECATED))

        assert [m.name for m in results] == ["dep-one"]
