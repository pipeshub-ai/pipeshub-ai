"""Guards on the Acme Corp demo stories behind the vertical Build Packs.

The packs (sales, support, marketing, finance, HR) each ask golden questions kept
under `pack_questions`, apart from the chat landing's `questions`. These checks
catch the ways a pack's demo would quietly break: a question naming a record that
no longer exists, a "who can see this" lesson visible to the wrong persona or to
the installing admin, and a restricted fact that leaks into an open record.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import app.connectors.sources.demo.connector as demo_connector

FIXTURE = Path(demo_connector.__file__).resolve().parent / "fixture" / "acme-corp.yaml"


@pytest.fixture(scope="module")
def fx() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _group_of_record(fx: dict) -> dict[str, str]:
    """The connector's rule: a record's own group, else its container's; a thread takes its first message's."""
    containers = {c["id"]: c for c in fx["containers"]}
    groups = {r["id"]: r.get("group") or containers[r["container"]]["group"] for r in fx["records"]}
    for t in fx.get("threads", []):
        first = min((r for r in fx["records"] if r.get("thread") == t["id"]), key=lambda r: str(r["created"]))
        groups[t["id"]] = first.get("group") or containers[t["container"]]["group"]
    return groups


def _pack_questions(fx: dict) -> list[dict]:
    return [q for qs in fx.get("pack_questions", {}).values() for q in qs]


def test_pack_expectations_name_records_that_exist(fx: dict) -> None:
    known = {r["id"] for r in fx["records"]} | {t["id"] for t in fx.get("threads", [])}
    keys = ("must_cite", "must_cite_any_of", "must_cite_any_of_2", "stretch_cite_any_of", "must_not_cite", "restricted")
    missing = {q["id"]: sorted(x for k in keys for x in q.get(k, []) if x not in known) for q in _pack_questions(fx)}
    assert not {k: v for k, v in missing.items() if v}, missing
    ids = [q["id"] for q in _pack_questions(fx) + fx["questions"]]
    assert len(ids) == len(set(ids)), "question ids must be unique"


def test_each_pack_lesson_is_visible_to_exactly_its_reader(fx: dict) -> None:
    # Every pack has one "who can see this" question; the restricted record must be
    # readable by the persona that "cites" it, hidden from the other, and never
    # handed to the installing admin.
    people = {p["id"]: p for p in fx["people"]}
    groups = {g["id"]: g for g in fx["groups"]}
    group_of_record = _group_of_record(fx)
    lessons = [q for q in _pack_questions(fx) if q.get("restricted")]
    assert {q["id"] for q in lessons}, "the packs need their restricted questions"
    containers = {c["id"]: c for c in fx["containers"]}
    records = {r["id"]: r for r in fx["records"]}
    for q in lessons:
        (group,) = {group_of_record[x] for x in q["restricted"]}
        assert not groups[group].get("installer_joins"), f"{q['id']}: the installer must not see {group}"
        # A record also inherits its container's readers, so a record-level group
        # inside a wider folder restricts nothing: the folder must be the group's own.
        for x in q["restricted"]:
            assert containers[records[x]["container"]]["group"] == group, f"{q['id']}: {x} sits in a wider folder"
        for persona, expect in q["personas"].items():
            member = group in people[persona].get("groups", [])
            assert member == (expect == "cites"), f"{q['id']}: {persona} ({expect}) membership of {group}"


def test_pack_restricted_facts_come_only_from_restricted_records(fx: dict) -> None:
    group_of_record = _group_of_record(fx)
    for q in (q for q in _pack_questions(fx) if q.get("restricted")):
        (group,) = {group_of_record[x] for x in q["restricted"]}
        facts = q.get("restricted_facts", [])
        assert facts, f"{q['id']} needs facts that catch a leak in the answer text"
        inside = " ".join(r["body"] for r in fx["records"] if r["id"] in q["restricted"]).lower()
        outside = " ".join(r["body"] for r in fx["records"] if group_of_record[r["id"]] != group).lower()
        assert [f for f in facts if f.lower() not in inside] == [], q["id"]
        assert [f for f in facts if f.lower() in outside] == [], q["id"]


def test_every_team_reader_group_is_open_to_the_installer(fx: dict) -> None:
    # Team folders carry the shared content the admin who adds the demo should see;
    # only the folders behind a "who can see this" lesson stay closed.
    groups = {g["id"]: g for g in fx["groups"]}
    lesson_groups = {"pricing-committee"} | {
        _group_of_record(fx)[x] for q in _pack_questions(fx) for x in q.get("restricted", [])
    }
    closed = sorted(c["id"] for c in fx["containers"] if c["group"] not in lesson_groups and not groups[c["group"]].get("installer_joins"))
    assert closed == [], closed


def test_no_open_folder_holds_a_restricted_record(fx: dict) -> None:
    # The leak this guards: a restricted record placed in a team folder is readable
    # by everyone who can read the folder.
    containers = {c["id"]: c for c in fx["containers"]}
    leaks = sorted(r["id"] for r in fx["records"] if r.get("group") and r["group"] != containers[r["container"]]["group"])
    assert leaks == [], leaks
