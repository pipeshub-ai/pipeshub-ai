"""The demo fixture loader: `acme-corp.yaml` plus every `acme-corp.d/*.yaml`.

The connector, the knowledge-base harness and the KG evaluation all read the
fixture through this loader, so a bad extension file fails here, at load time,
instead of as a half-synced demo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from app.connectors.sources.demo.fixture_loader import (
    FIXTURE_PATH,
    FixtureError,
    load_fixture,
)

if TYPE_CHECKING:
    from pathlib import Path

MAIN = {
    "company": "Acme Corp",
    "groups": [{"id": "eng", "name": "Engineering"}],
    "people": [{"id": "alice", "name": "Alice", "email": "alice@acme-demo.example", "groups": ["eng"]}],
    "containers": [{"id": "repo", "system": "GITHUB", "kind": "repository", "name": "acme/repo", "group": "eng"}],
    "threads": [],
    "records": [
        {"id": "pr-1", "container": "repo", "type": "PULL_REQUEST", "title": "PR 1",
         "author": "alice", "created": "2026-04-01T00:00:00Z", "body": "text"},
    ],
    "questions": [{"id": "q1", "ask": "?"}],
    "pack_questions": {"sales": [{"id": "s1", "ask": "?"}]},
}


def _write(tmp_path: Path, main: dict, extras: dict[str, dict]) -> Path:
    path = tmp_path / "acme-corp.yaml"
    path.write_text(yaml.safe_dump(main), encoding="utf-8")
    if extras:
        ext = tmp_path / "acme-corp.d"
        ext.mkdir()
        for name, content in extras.items():
            (ext / name).write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def _globex_extra() -> dict:
    return {
        "organizations": [{"id": "globex", "name": "Globex Corporation", "domain": "globex.example", "crm": True}],
        "people": [{"id": "gina", "name": "Gina", "email": "gina@globex.example", "kind": "external",
                    "company": "globex"}],
        "containers": [{"id": "sf-globex", "system": "SALESFORCE", "kind": "account", "name": "Globex",
                        "group": "eng", "organization": "globex"}],
        "records": [{"id": "case-1", "container": "sf-globex", "type": "CASE", "title": "Case 1",
                     "author": "alice", "owner": "alice", "reporter": "gina",
                     "created": "2026-04-02T00:00:00Z", "body": "text"}],
    }


def test_without_an_extension_directory_the_main_file_loads_unchanged(tmp_path: Path) -> None:
    fx = load_fixture(_write(tmp_path, MAIN, {}))
    assert fx["records"] == MAIN["records"]
    assert fx["questions"] == MAIN["questions"]
    assert fx["pack_questions"] == MAIN["pack_questions"]
    assert fx["organizations"] == []


def test_extension_lists_are_appended_in_file_name_order(tmp_path: Path) -> None:
    second = {"records": [{"id": "pr-3", "container": "repo", "type": "PULL_REQUEST", "title": "PR 3",
                           "author": "alice", "created": "2026-04-03T00:00:00Z", "body": "t"}]}
    first = {"records": [{"id": "pr-2", "container": "repo", "type": "PULL_REQUEST", "title": "PR 2",
                          "author": "alice", "created": "2026-04-02T00:00:00Z", "body": "t"}]}
    fx = load_fixture(_write(tmp_path, MAIN, {"20-b.yaml": second, "10-a.yaml": first}))
    assert [r["id"] for r in fx["records"]] == ["pr-1", "pr-2", "pr-3"]


def test_extensions_add_people_organizations_and_containers(tmp_path: Path) -> None:
    fx = load_fixture(_write(tmp_path, MAIN, {"00.yaml": _globex_extra()}))
    assert [o["id"] for o in fx["organizations"]] == ["globex"]
    assert [p["id"] for p in fx["people"]] == ["alice", "gina"]
    assert "sf-globex" in {c["id"] for c in fx["containers"]}


def test_only_the_shared_lists_merge_and_questions_stay_in_the_main_file(tmp_path: Path) -> None:
    with pytest.raises(FixtureError, match="questions"):
        load_fixture(_write(tmp_path, MAIN, {"00.yaml": {"questions": [{"id": "q9", "ask": "?"}]}}))


@pytest.mark.parametrize("extra, message", [
    ({"records": [{**MAIN["records"][0]}]}, "duplicate records id 'pr-1'"),
    ({"people": [{**MAIN["people"][0]}]}, "duplicate people id 'alice'"),
    ({"threads": [{"id": "pr-1", "container": "repo", "title": "t"}]}, "duplicate"),
])
def test_a_duplicate_id_fails_loading(tmp_path: Path, extra: dict, message: str) -> None:
    with pytest.raises(FixtureError, match=message):
        load_fixture(_write(tmp_path, MAIN, {"00.yaml": extra}))


def _record(**fields: object) -> dict:
    return {"id": "r-x", "container": "repo", "type": "PULL_REQUEST", "title": "x", "author": "alice",
            "created": "2026-04-01T00:00:00Z", "body": "t", **fields}


@pytest.mark.parametrize("extra, message", [
    ({"records": [_record(author="nobody")]}, "r-x.*author.*nobody"),
    ({"records": [_record(reviewers=["alice", "ghost"])]}, "r-x.*reviewers.*ghost"),
    ({"records": [_record(container="nowhere")]}, "r-x.*container.*nowhere"),
    ({"records": [_record(group="nogroup")]}, "r-x.*group.*nogroup"),
    ({"records": [_record(mentions_orgs=["initech"])]}, "r-x.*mentions_orgs.*initech"),
    ({"people": [{"id": "x", "name": "X", "email": "x@acme-demo.example", "groups": ["nope"]}]}, "x.*groups.*nope"),
    ({"people": [{"id": "x", "name": "X", "email": "x@initech.example", "kind": "external", "company": "initech"}]},
     "x.*company.*initech"),
    ({"containers": [{"id": "c", "system": "GMAIL", "kind": "mailbox", "name": "m", "group": "eng",
                      "owner": "ghost"}]}, "c.*owner.*ghost"),
    ({"containers": [{"id": "c", "system": "SALESFORCE", "kind": "account", "name": "m", "group": "eng",
                      "organization": "initech"}]}, "c.*organization.*initech"),
    ({"threads": [{"id": "t", "container": "nowhere", "title": "t"}]}, "t.*container.*nowhere"),
])
def test_a_dangling_reference_fails_loading(tmp_path: Path, extra: dict, message: str) -> None:
    with pytest.raises(FixtureError, match=message):
        load_fixture(_write(tmp_path, MAIN, {"00.yaml": extra}))


@pytest.mark.parametrize("record, message", [
    # The real Jira connector names no reviewers; the demo must not either.
    (_record(container="jira", type="TICKET", reviewers=["alice"]), "reviewers"),
    (_record(type="MAIL"), "GITHUB.*MAIL"),
])
def test_a_field_or_type_the_real_connector_does_not_emit_fails_loading(
    tmp_path: Path, record: dict, message: str,
) -> None:
    extra = {
        "containers": [{"id": "jira", "system": "JIRA", "kind": "project", "name": "J", "group": "eng"}],
        "records": [record],
    }
    with pytest.raises(FixtureError, match=message):
        load_fixture(_write(tmp_path, MAIN, {"00.yaml": extra}))


def test_a_salesforce_account_belongs_to_a_crm_organization(tmp_path: Path) -> None:
    extra = _globex_extra()
    extra["organizations"][0]["crm"] = False
    with pytest.raises(FixtureError, match="sf-globex.*crm"):
        load_fixture(_write(tmp_path, MAIN, {"00.yaml": extra}))


def test_an_external_person_needs_an_address_on_their_companys_domain(tmp_path: Path) -> None:
    extra = _globex_extra()
    extra["people"][0]["email"] = "gina@acme-demo.example"
    with pytest.raises(FixtureError, match="gina.*globex.example"):
        load_fixture(_write(tmp_path, MAIN, {"00.yaml": extra}))


def test_the_bundled_fixture_loads_and_keeps_every_main_file_record() -> None:
    main = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    fx = load_fixture()
    assert fx["records"][: len(main["records"])] == main["records"]
    assert fx["questions"] == main["questions"]
    assert fx["pack_questions"] == main["pack_questions"]
