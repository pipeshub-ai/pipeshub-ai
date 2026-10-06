"""Which reader a repository file gets, and which files are skipped."""
from __future__ import annotations

import pytest

from app.modules.parsers.code_parser import engine
from app.modules.parsers.code_parser.routing import (
    CodeFilePlan,
    CodeFileRoute,
    SkipCause,
    plan_code_file,
)
from app.utils.user_errors import BINARY_FILE_SKIPPED, GENERATED_FILE_SKIPPED

LIMIT = 4096


def plan_repository(
    name: str, path: str | None, extension: str | None, content: bytes
) -> CodeFilePlan:
    """A file synced from a code repository."""
    return plan_code_file(name, path, extension, content, repository_file=True)


def plan_upload(name: str, content: bytes) -> CodeFilePlan:
    """A source file someone uploaded, which reaches the same code path."""
    return plan_code_file(name, None, None, content, repository_file=False)


@pytest.fixture(autouse=True)
def small_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "MAX_FILE_SIZE_BYTES", LIMIT)


@pytest.mark.parametrize(
    ("name", "language"),
    [("main.py", "python"), ("app.tsx", "tsx"), ("Service.scala", "scala"), ("init.lua", "lua")],
)
def test_source_with_a_grammar_goes_to_the_code_parser(name: str, language: str) -> None:
    plan = plan_repository(name, f"src/{name}", None, b"x = 1\n")
    assert (plan.route, plan.parser) == (CodeFileRoute.CODE, language)


def test_the_file_name_decides_the_grammar_before_the_declared_extension() -> None:
    result = plan_repository("main.py", "src/main.py", "json", b"x = 1\n")
    assert (result.route, result.parser, result.extension) == (CodeFileRoute.CODE, "python", "py")


def test_a_declared_extension_picks_the_grammar_when_the_name_has_none() -> None:
    plan = plan_repository("build-script", None, "py", b"print(1)\n")
    assert (plan.route, plan.parser) == (CodeFileRoute.CODE, "python")


@pytest.mark.parametrize(
    ("name", "route", "parser"),
    [
        ("export.csv", CodeFileRoute.DELIMITED, "csv"),
        ("export.CSV", CodeFileRoute.DELIMITED, "csv"),
        ("export.tsv", CodeFileRoute.DELIMITED, "tsv"),
        ("fixtures.json", CodeFileRoute.STRUCTURED, "json"),
        ("values.yaml", CodeFileRoute.STRUCTURED, "yaml"),
        ("values.yml", CodeFileRoute.STRUCTURED, "yaml"),
    ],
)
def test_data_files_go_to_the_parser_for_their_format(
    name: str, route: CodeFileRoute, parser: str
) -> None:
    plan = plan_repository(name, f"data/{name}", None, b"a,b\n1,2\n")
    assert (plan.route, plan.parser) == (route, parser)


def test_a_data_file_larger_than_the_code_limit_still_goes_to_its_own_parser() -> None:
    # The CSV and JSON parsers apply the limits an upload of that type gets.
    plan = plan_repository("export.csv", None, None, b"a,b\n" * LIMIT)
    assert plan.route is CodeFileRoute.DELIMITED


@pytest.mark.parametrize(
    "name",
    [
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
        "app.min.js", "site.min.css", "bundle.js.map", "Button.test.tsx.snap",
    ],
)
def test_generated_files_are_skipped_whatever_their_type(name: str) -> None:
    plan = plan_repository(name, f"web/{name}", None, b"{}")
    assert plan.route is CodeFileRoute.SKIP
    assert plan.skip_cause is SkipCause.GENERATED
    assert plan.skip_reason == GENERATED_FILE_SKIPPED


def test_directory_names_alone_do_not_skip_a_file() -> None:
    # The connector that syncs bin/ and vendor/ decided to; only names are judged here.
    plan = plan_repository("deploy.sh", "bin/deploy.sh", "sh", b"echo hi\n")
    assert plan.route is CodeFileRoute.TEXT


@pytest.mark.parametrize("name", ["events.ndjson", "events.jsonl"])
def test_line_delimited_json_is_reported_as_unsupported(name: str) -> None:
    plan = plan_repository(name, None, None, b'{"a": 1}\n{"a": 2}\n')
    assert plan.route is CodeFileRoute.SKIP
    assert plan.skip_cause is SkipCause.NO_PARSER
    assert name.rsplit(".", 1)[-1] in plan.skip_reason


@pytest.mark.parametrize("name", ["deploy.sh", "schema.sql", "styles.css", "main.tf", "NOTES.txt"])
def test_other_text_falls_back_to_text_parsing(name: str) -> None:
    plan = plan_repository(name, None, None, b"plain text\n")
    assert plan.route is CodeFileRoute.TEXT
    assert plan.parser is None


def test_text_at_the_limit_is_read_and_one_byte_more_is_not() -> None:
    assert plan_repository("dump.sql", None, None, b"x" * LIMIT).route is CodeFileRoute.TEXT

    plan = plan_repository("dump.sql", None, None, b"x" * (LIMIT + 1))
    assert plan.route is CodeFileRoute.SKIP
    assert plan.skip_cause is SkipCause.TOO_LARGE
    assert "CODE_FILE_MAX_SIZE_MB" in plan.skip_reason


def test_source_over_the_limit_is_skipped_before_it_is_parsed() -> None:
    plan = plan_repository("big.py", None, None, b"x = 1\n" * LIMIT)
    assert plan.route is CodeFileRoute.SKIP
    assert plan.skip_cause is SkipCause.TOO_LARGE


def test_binary_content_under_a_text_name_is_skipped() -> None:
    plan = plan_repository("blob.sql", None, None, b"\x7fELF\x00\x01\x02" + b"\x00" * 64)
    assert plan.route is CodeFileRoute.SKIP
    assert plan.skip_cause is SkipCause.BINARY
    assert plan.skip_reason == BINARY_FILE_SKIPPED


def test_utf16_text_is_not_mistaken_for_binary() -> None:
    plan = plan_repository("setup.ps1", None, None, "Write-Host 'hi'\n".encode("utf-16"))
    assert plan.route is CodeFileRoute.TEXT


def test_an_unknown_declared_extension_is_ignored() -> None:
    plan = plan_repository("LICENSE", None, "unknown", b"MIT\n")
    assert plan.route is CodeFileRoute.TEXT


# -- uploads keep what they had: nothing is filtered, only source is size-limited --


@pytest.mark.parametrize("name", ["app.min.js", "vendor.min.js"])
def test_an_uploaded_minified_script_is_still_parsed_as_source(name: str) -> None:
    result = plan_upload(name, b"function a(){return 1}")
    assert (result.route, result.parser) == (CodeFileRoute.CODE, "javascript")


def test_an_uploaded_text_file_has_no_size_limit() -> None:
    assert plan_upload("deploy.sh", b"echo hi\n" * LIMIT).route is CodeFileRoute.TEXT


def test_an_uploaded_file_is_not_checked_for_binary_content_or_data_dumps() -> None:
    assert plan_upload("odd.sh", b"\x00\x01\x02").route is CodeFileRoute.TEXT
    assert plan_upload("events.ndjson", b"{}\n").route is CodeFileRoute.TEXT


def test_uploaded_source_over_the_limit_is_still_refused_as_before() -> None:
    result = plan_upload("big.py", b"x = 1\n" * LIMIT)
    assert result.route is CodeFileRoute.SKIP
    assert result.skip_cause is SkipCause.TOO_LARGE
    assert result.skip_reason.endswith("and then upload it again.")


def test_the_reason_for_a_repository_file_names_a_step_that_exists() -> None:
    # A "File Type Not Supported" record has no Reindex action of its own.
    result = plan_repository("dump.sql", None, None, b"x" * (LIMIT + 1))
    assert result.skip_reason.endswith("and then choose Index all on the repository.")
