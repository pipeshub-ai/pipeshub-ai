"""Guards on the Acme Corp demo fixture that are cheap enough for every pull request.

The demo's acceptance test asks the golden questions against a running
instance, so it only runs nightly. These catch the ways the demo has already
gone wrong, or nearly did, without an instance:

- links that point at real third-party accounts instead of a reserved domain;
- suggested questions on the chat landing that drift from the questions the
  acceptance test actually scores;
- expectations that name records the fixture no longer has;
- the permissions lesson (only the pricing committee sees pricing) quietly
  disappearing.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

import pytest
import yaml

import app.connectors.sources.demo.connector as demo_connector
from app.connectors.sources.demo.harness.kb_harness import score

FIXTURE = Path(demo_connector.__file__).resolve().parent / "fixture" / "acme-corp.yaml"
RESERVED_DOMAIN = "acme-demo.example"
CHAT_LOCALES = ("en-US", "en-IN")


def _on_reserved_domain(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host == RESERVED_DOMAIN or host.endswith("." + RESERVED_DOMAIN)


def _group_of_record(fx: dict) -> dict[str, str]:
    """The connector's rule: a record's own group, else its container's; a thread takes its first message's."""
    containers = {c["id"]: c for c in fx["containers"]}
    groups = {r["id"]: r.get("group") or containers[r["container"]]["group"] for r in fx["records"]}
    for t in fx.get("threads", []):
        first = min((r for r in fx["records"] if r.get("thread") == t["id"]), key=lambda r: str(r["created"]))
        groups[t["id"]] = first.get("group") or containers[t["container"]]["group"]
    return groups


def _repo_root() -> Path | None:
    for parent in FIXTURE.parents:
        if (parent / "frontend" / "lib" / "i18n" / "locales").is_dir():
            return parent
    return None


@pytest.fixture(scope="module")
def fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fx(fixture_text: str) -> dict:
    return yaml.safe_load(fixture_text)


def test_every_link_stays_on_the_reserved_demo_domain(fixture_text: str) -> None:
    # Demo records show "Open in GitHub/Jira/Slack" links. A real host there
    # (github.com/acme-demo, acme-demo.slack.com, ...) sends a first-time user
    # to an account somebody else can register and fill.
    urls = re.findall(r"https?://[^\s\"')\]]+", fixture_text)
    assert urls, "the fixture should carry source links"
    outside = sorted({u for u in urls if not _on_reserved_domain(u)})
    assert not outside, f"links outside {RESERVED_DOMAIN}: {outside}"


@pytest.mark.parametrize("locale", CHAT_LOCALES)
def test_chat_suggestions_are_the_questions_the_acceptance_test_scores(fx: dict, locale: str) -> None:
    root = _repo_root()
    if root is None:
        pytest.skip("frontend sources are not in this checkout")
    messages = json.loads((root / "frontend" / "lib" / "i18n" / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    chips = [item["text"] for item in messages["chat"]["demoSuggestions"].values()]
    assert chips == [q["ask"] for q in fx["questions"]], (
        "the chat landing's demo questions must match fixture.questions word for word, "
        "or the questions people click are not the ones the acceptance test checks"
    )


def test_expectations_name_records_that_exist(fx: dict) -> None:
    known = {r["id"] for r in fx["records"]} | {t["id"] for t in fx.get("threads", [])}
    keys = ("must_cite", "must_cite_any_of", "must_cite_any_of_2", "stretch_cite_any_of", "must_not_cite", "restricted")
    missing = {
        q["id"]: sorted(x for k in keys for x in q.get(k, []) if x not in known)
        for q in fx["questions"]
    }
    assert not {k: v for k, v in missing.items() if v}, missing


def test_only_the_pricing_committee_can_see_pricing(fx: dict) -> None:
    people = {p["id"]: p for p in fx["people"]}
    groups = {g["id"]: g for g in fx["groups"]}
    restricted_q = next(q for q in fx["questions"] if q.get("restricted"))
    group_of_record = _group_of_record(fx)

    restricted_groups = {group_of_record[x] for x in restricted_q["restricted"]}
    assert restricted_groups == {"pricing-committee"}
    assert not groups["pricing-committee"].get("installer_joins"), "the installer must not see pricing"
    assert "pricing-committee" in people["bob"].get("groups", [])
    assert "pricing-committee" not in people["alice"].get("groups", [])
    assert restricted_q["personas"] == {"alice": "none", "bob": "cites"}


def test_restricted_facts_come_only_from_restricted_records(fx: dict) -> None:
    # The acceptance test fails an answer that repeats one of these, so each must
    # be in a restricted record and in nothing a non-member can read.
    restricted_q = next(q for q in fx["questions"] if q.get("restricted"))
    facts = restricted_q.get("restricted_facts", [])
    assert facts, "the restricted question needs facts to catch a leak in the answer text"
    restricted_ids = set(restricted_q["restricted"])
    inside = " ".join(r["body"] for r in fx["records"] if r["id"] in restricted_ids or r.get("thread") in restricted_ids).lower()
    group_of_record = _group_of_record(fx)
    outside = " ".join(r["body"] for r in fx["records"] if group_of_record[r["id"]] != "pricing-committee").lower()
    assert [f for f in facts if f.lower() not in inside] == []
    assert [f for f in facts if f.lower() in outside] == []


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://github.acme-demo.example/svc-export/pull/211", True),
        ("https://acme-demo.example/", True),
        # Ends with the same letters, but is somebody else's domain.
        ("https://evilacme-demo.example/", False),
        ("https://github.com/acme-demo/svc-export", False),
        ("https://acme-demo.example.attacker.io/", False),
    ],
)
def test_the_link_guard_accepts_only_the_reserved_domain(url: str, ok: bool) -> None:
    assert _on_reserved_domain(url) is ok


@pytest.mark.parametrize(
    ("answer", "cited", "ok"),
    [
        ("I couldn't find a 2026 enterprise pricing strategy you can see.", set(), True),
        # Nothing restricted is cited, but the answer repeats what only the committee knows.
        ("The plan is a platform fee of $48k for 250 seats.", set(), False),
        ("Here it is.", {"drive-pricing-2026"}, False),
        # A run that failed says nothing about what Alice can see.
        ("ERROR: upstream timeout", set(), False),
    ],
)
def test_a_persona_without_access_passes_only_on_a_clean_empty_answer(fx: dict, answer: str, cited: set[str], ok: bool) -> None:
    restricted_q = next(q for q in fx["questions"] if q.get("restricted"))
    assert score(restricted_q, "none", cited, answer)[0] is ok


def test_the_chat_landing_marks_the_restricted_question_from_the_fixture(fx: dict) -> None:
    # The landing locks the question for anyone who cannot open this record, and
    # suggests signing in as this reader; both must still hold in the fixture.
    root = _repo_root()
    if root is None:
        pytest.skip("frontend sources are not in this checkout")
    source = (root / "frontend/app/(main)/workspace/connectors/demo-data/demo-data.ts").read_text(encoding="utf-8")
    title = re.search(r"RESTRICTED_RECORD_TITLE = '([^']+)'", source)
    reader = re.search(r"RESTRICTED_RECORD_READER = `([a-z]+)@\$\{DEMO_ACCOUNT_DOMAIN\}`", source)
    assert title and reader, "demo-data.ts no longer declares the restricted record and its reader"

    restricted_q = next(q for q in fx["questions"] if q.get("restricted"))
    records = {r["id"]: r for r in fx["records"]}
    assert title.group(1) in {records[x]["title"] for x in restricted_q["restricted"] if x in records}
    person = next(p for p in fx["people"] if p["email"] == f"{reader.group(1)}@{RESERVED_DOMAIN}")
    assert "pricing-committee" in person.get("groups", [])

    for locale in CHAT_LOCALES:
        chips = json.loads((root / "frontend/lib/i18n/locales" / f"{locale}.json").read_text(encoding="utf-8"))
        marked = [c["text"] for c in chips["chat"]["demoSuggestions"].values() if c.get("restricted")]
        assert marked == [restricted_q["ask"]], locale
