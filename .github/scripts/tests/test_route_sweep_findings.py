"""Tests for the sweep router. No network: GitHub is a recording fake.

The properties worth pinning are the ones that keep a security finding from
ever reaching a public surface — a public issue, a log line, a fallback path.
Everything else is bookkeeping.
"""

from __future__ import annotations

import io
import json
import re
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import route_sweep_findings as router  # noqa: E402

REPO = "acme/widgets"


class FakeGitHub:
    """Records every call; answers from a small script of canned responses."""

    def __init__(
        self,
        *,
        open_issues=(),
        open_advisories=(),
        fail: dict[str, int] | None = None,
        fail_headers: dict[str, str] | None = None,
        fail_body: bytes = b"",
    ):
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.open_issues = [{"title": t} for t in open_issues]
        self.open_advisories = [{"summary": s} for s in open_advisories]
        self.fail = fail or {}
        self.fail_headers = fail_headers or {}
        self.fail_body = fail_body

    def _maybe_fail(self, method: str, path: str) -> None:
        key = f"{method} {path.split('?')[0]}"
        if key in self.fail:
            raise urllib.error.HTTPError(
                path, self.fail[key], "boom", self.fail_headers, io.BytesIO(self.fail_body)  # type: ignore[arg-type]
            )

    def page(self, path: str) -> tuple[list[Any], str | None]:
        """Pages the way GitHub does: by an `after` cursor from `Link`.

        A `page` number is ignored, as the advisories endpoint ignores it, so
        code that counts pages instead of following `Link` re-reads page one.
        """
        self.calls.append(("GET", path, None))
        self._maybe_fail("GET", path)
        items = self.open_issues if "/issues" in path else self.open_advisories
        match = re.search(r"[?&]after=(\d+)", path)
        start = int(match.group(1)) if match else 0
        end = start + 100
        base = re.sub(r"&after=\d+", "", path)
        return items[start:end], (f"{base}&after={end}" if end < len(items) else None)

    def __call__(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        self.calls.append((method, path, body))
        self._maybe_fail(method, path)
        if method == "POST" and path.endswith("/issues"):
            return {"html_url": f"https://github.com/{REPO}/issues/{len(self.calls)}"}
        if method == "POST" and path.endswith("/security-advisories"):
            return {"ghsa_id": f"GHSA-fake-{len(self.calls)}"}
        if method == "POST" and path.endswith("/labels"):
            return {}
        return {}

    def as_client(self) -> Any:
        """Stands in for router.GitHub, so main() can be driven end to end."""
        return type("G", (), {"request": staticmethod(self), "page": staticmethod(self.page)})()

    def posts_to(self, suffix: str) -> list[dict[str, Any]]:
        return [b for m, p, b in self.calls if m == "POST" and p.endswith(suffix) and b]


def security(**over) -> dict[str, Any]:
    base = {
        "kind": "security",
        "title": "SQL built from a request parameter",
        "file": "backend/python/app/api/routes/records.py",
        "line": 88,
        "severity": "high",
        "summary": "The record filter is interpolated into a query string.",
        "failure_scenario": "A crafted filter value reads other tenants' rows.",
        "suggested_fix": "Bind the value as a parameter.",
        "cwe": "CWE-89",
        "area": "python",
    }
    base.update(over)
    return base


def bug(**over) -> dict[str, Any]:
    base = {
        "kind": "bug",
        "title": "Retry loop never sleeps",
        "file": "backend/python/app/connectors/core/retry.py",
        "line": 42,
        "severity": "medium",
        "summary": "The backoff delay is computed and then discarded.",
        "failure_scenario": "A rate-limited API is hammered with immediate retries.",
        "area": "connectors",
    }
    base.update(over)
    return base


def write_report(tmp_path: Path, findings: list[dict[str, Any]]) -> str:
    path = tmp_path / "sweep-findings.json"
    path.write_text(json.dumps({"findings": findings}))
    return str(path)


def run(tmp_path: Path, findings, gh: FakeGitHub, *, dry_run=False) -> router.Outcome:
    outcome = router.Outcome()
    parsed = router.apply_caps(router.load_findings(write_report(tmp_path, findings), outcome), outcome)
    return router.route(parsed, gh, REPO, pages=gh.page, dry_run=dry_run, outcome=outcome)


# --------------------------------------------------------------------------- #
# The security properties
# --------------------------------------------------------------------------- #


def test_a_security_finding_becomes_a_private_advisory_not_an_issue(tmp_path) -> None:
    gh = FakeGitHub()
    out = run(tmp_path, [security()], gh)

    assert out.advisories_created == ["GHSA-fake-3"]
    assert gh.posts_to("/issues") == [], "a security finding must never become a public issue"
    body = gh.posts_to("/security-advisories")[0]
    assert body["severity"] == "high"
    assert body["cwe_ids"] == ["CWE-89"]
    assert "Bind the value" in body["description"], "the proposed fix travels with the advisory"


def test_a_failed_advisory_does_not_fall_back_to_a_public_issue(tmp_path) -> None:
    """The one path that must never exist."""
    gh = FakeGitHub(fail={"POST /repos/acme/widgets/security-advisories": 403})
    out = run(tmp_path, [security()], gh)

    assert out.security_failed == 1
    assert out.advisories_created == []
    assert gh.posts_to("/issues") == [], "an undeliverable security finding is not filed publicly"


def test_a_failed_issue_makes_the_run_fail_too(tmp_path, monkeypatch) -> None:
    """A lost bug report is quieter than a lost security finding, not better."""
    gh = FakeGitHub(fail={"POST /repos/acme/widgets/issues": 500})
    monkeypatch.setattr(router, "GitHub", lambda repo, token: gh.as_client())
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert router.main([write_report(tmp_path, [bug()]), "--repo", REPO]) == 1


def test_a_failed_advisory_makes_the_run_fail(tmp_path, monkeypatch) -> None:
    """Green with a lost security finding would be worse than red."""
    gh = FakeGitHub(fail={"POST /repos/acme/widgets/security-advisories": 403})
    monkeypatch.setattr(router, "GitHub", lambda repo, token: gh.as_client())
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert router.main([write_report(tmp_path, [security()]), "--repo", REPO]) == 1


def test_the_summary_never_contains_security_details(tmp_path) -> None:
    """Workflow logs on a public repository are public."""
    gh = FakeGitHub()
    out = run(tmp_path, [security()], gh)
    text = router.summarise(out)

    for secret in ("SQL built", "records.py", "other tenants", "Bind the value"):
        assert secret not in text, f"security detail leaked into the summary: {secret!r}"
    assert "advisories created: 1" in text
    assert "GHSA-fake-3" in text, "the id alone reveals nothing and is what a maintainer needs"


def test_the_summary_may_name_public_issues(tmp_path) -> None:
    gh = FakeGitHub()
    out = run(tmp_path, [bug()], gh)
    assert "issues/" in router.summarise(out)


# --------------------------------------------------------------------------- #
# Bugs become labelled issues
# --------------------------------------------------------------------------- #


def test_a_bug_becomes_an_issue_with_the_right_labels(tmp_path) -> None:
    gh = FakeGitHub()
    run(tmp_path, [bug(area="connectors")], gh)

    body = gh.posts_to("/issues")[0]
    assert set(body["labels"]) == {"bug", router.MARKER_LABEL, "Connectors"}
    assert body["title"].endswith("(backend/python/app/connectors/core/retry.py)")
    assert "retry.py:42" in body["body"]


def test_an_unknown_area_gets_only_the_base_labels(tmp_path) -> None:
    gh = FakeGitHub()
    run(tmp_path, [bug(area="mystery")], gh)
    assert set(gh.posts_to("/issues")[0]["labels"]) == {"bug", router.MARKER_LABEL}


def test_the_marker_label_is_created_and_an_existing_one_is_fine(tmp_path) -> None:
    gh = FakeGitHub(fail={"POST /repos/acme/widgets/labels": 422})
    run(tmp_path, [bug()], gh)
    assert len(gh.posts_to("/issues")) == 1, "a label that already exists must not block filing"


# --------------------------------------------------------------------------- #
# Not filing the same thing twice
# --------------------------------------------------------------------------- #


def test_a_bug_already_open_for_that_file_is_skipped(tmp_path) -> None:
    gh = FakeGitHub(open_issues=["Something (backend/python/app/connectors/core/retry.py)"])
    out = run(tmp_path, [bug()], gh)
    assert out.skipped_duplicates == 1
    assert gh.posts_to("/issues") == []


def test_a_security_finding_already_in_a_draft_advisory_is_skipped(tmp_path) -> None:
    gh = FakeGitHub(open_advisories=["Earlier wording (backend/python/app/api/routes/records.py)"])
    out = run(tmp_path, [security()], gh)
    assert out.skipped_duplicates == 1
    assert gh.posts_to("/security-advisories") == []


def test_advisory_dedup_reads_every_page(tmp_path) -> None:
    """A matching draft on page two must still count as a duplicate."""
    padding = [f"Unrelated {i} (other{i}.py)" for i in range(100)]
    match = ["Earlier wording (backend/python/app/api/routes/records.py)"]
    gh = FakeGitHub(open_advisories=padding + match)
    out = run(tmp_path, [security()], gh)
    assert out.skipped_duplicates == 1
    assert gh.posts_to("/security-advisories") == []


def test_a_transient_listing_error_does_not_cause_a_duplicate_advisory(tmp_path) -> None:
    """Treating a failed listing as "no drafts" would file on top of a real one."""
    gh = FakeGitHub(fail={"GET /repos/acme/widgets/security-advisories": 500})
    with pytest.raises(urllib.error.HTTPError):
        run(tmp_path, [security()], gh)
    assert gh.posts_to("/security-advisories") == []


def test_a_permission_error_listing_advisories_is_not_fatal(tmp_path) -> None:
    """No permission to list means the create fails visibly on its own."""
    gh = FakeGitHub(fail={
        "GET /repos/acme/widgets/security-advisories": 403,
        "POST /repos/acme/widgets/security-advisories": 403,
    })
    out = run(tmp_path, [security()], gh)
    assert out.security_failed == 1


def test_a_rate_limited_advisory_listing_does_not_cause_a_duplicate(tmp_path) -> None:
    """GitHub answers a rate limit with 403 too; that is not "no permission"."""
    gh = FakeGitHub(
        fail={"GET /repos/acme/widgets/security-advisories": 403},
        fail_body=b'{"message": "You have exceeded a secondary rate limit."}',
    )
    with pytest.raises(urllib.error.HTTPError):
        run(tmp_path, [security()], gh)
    assert gh.posts_to("/security-advisories") == []


def test_a_listing_that_never_ends_fails_instead_of_spinning() -> None:
    def same_page_forever(path: str) -> tuple[list[Any], str | None]:
        return [{"summary": "x"}], path

    with pytest.raises(RuntimeError):
        router.open_advisory_summaries(same_page_forever, REPO)


def test_a_next_link_off_the_api_host_is_not_followed() -> None:
    """The token goes wherever the next page is fetched from."""
    with pytest.raises(ValueError):
        router.next_page_path('<https://attacker.example/steal?after=1>; rel="next"')
    assert router.next_page_path(
        '<https://api.github.com/repositories/1/security-advisories?after=Y3Vy>; rel="next", '
        '<https://api.github.com/repositories/1/security-advisories?before=Zm9v>; rel="prev"'
    ) == "/repositories/1/security-advisories?after=Y3Vy"
    assert router.next_page_path('<https://api.github.com/x?before=a>; rel="prev"') is None
    assert router.next_page_path(None) is None


def test_dedup_is_by_file_not_by_title(tmp_path) -> None:
    """The reviewer rewords things between runs; the path does not move."""
    gh = FakeGitHub(open_issues=["Completely different words (backend/python/app/connectors/core/retry.py)"])
    out = run(tmp_path, [bug(title="Backoff is skipped")], gh)
    assert out.skipped_duplicates == 1


# --------------------------------------------------------------------------- #
# Caps and validation
# --------------------------------------------------------------------------- #


def test_caps_keep_the_highest_severity_findings(tmp_path) -> None:
    findings = [bug(file=f"f{i}.py", severity="low") for i in range(6)] + [bug(file="hot.py", severity="critical")]
    gh = FakeGitHub()
    out = run(tmp_path, findings, gh)

    filed = [b["title"] for b in gh.posts_to("/issues")]
    assert len(filed) == router.MAX_BUGS_PER_RUN
    assert any("hot.py" in t for t in filed), "the critical one must survive the cap"
    assert out.dropped_over_cap == 2


def test_security_and_bug_caps_are_independent(tmp_path) -> None:
    findings = [security(file=f"s{i}.py") for i in range(5)] + [bug(file=f"b{i}.py") for i in range(7)]
    gh = FakeGitHub()
    run(tmp_path, findings, gh)
    assert len(gh.posts_to("/security-advisories")) == router.MAX_SECURITY_PER_RUN
    assert len(gh.posts_to("/issues")) == router.MAX_BUGS_PER_RUN


@pytest.mark.parametrize(
    "broken",
    [
        {"kind": "bug"},
        bug(file=""),
        bug(failure_scenario=""),
        bug(kind="suggestion"),
        bug(line="not a number"),
        bug(line=0),
        bug(line=-4),
        "not even a dict",
    ],
)
def test_unusable_findings_are_dropped_not_filed(tmp_path, broken) -> None:
    gh = FakeGitHub()
    out = run(tmp_path, [broken], gh)
    assert out.dropped_malformed == 1
    assert gh.posts_to("/issues") == [] and gh.posts_to("/security-advisories") == []


def test_a_report_that_is_not_an_object_is_malformed_not_empty(tmp_path) -> None:
    path = tmp_path / "sweep-findings.json"
    path.write_text(json.dumps(["not", "a", "report"]))
    out = router.Outcome()
    assert router.load_findings(str(path), out) == []
    assert out.dropped_malformed == 1


def test_an_unknown_severity_is_treated_as_medium(tmp_path) -> None:
    gh = FakeGitHub()
    run(tmp_path, [security(severity="apocalyptic")], gh)
    assert gh.posts_to("/security-advisories")[0]["severity"] == "medium"


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


def test_dry_run_creates_nothing_and_reports_what_it_would_do(tmp_path) -> None:
    gh = FakeGitHub()
    out = run(tmp_path, [security(), bug()], gh, dry_run=True)

    assert not [c for c in gh.calls if c[0] == "POST"], "a dry run must not write anything"
    assert out.advisories_created == ["(dry run)"]
    assert out.issues_created and out.issues_created[0].startswith("(dry run)")
    assert "SQL built" not in router.summarise(out), "dry run still keeps security details out of logs"
