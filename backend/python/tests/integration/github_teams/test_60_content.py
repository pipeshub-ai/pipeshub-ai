"""Content streaming, reindexing and indexing filters.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from tests.integration.github_teams.helpers import fail
from tests.integration.github_teams.sim.model import Review, ReviewComment, next_id

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_issue_content_has_body_and_comments(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    issue = gh.add_issue(repo, "Crash on save", body="Steps: click save")
    gh.comment(issue, "Workaround: restart", org["alice"])
    await world.sync()
    text = await world.stream_text(f"{repo.id}/issues/{issue.number}")
    assert "Steps: click save" in text and "Workaround: restart" in text and "alice" in text


async def test_pull_request_stream_fails_when_reviews_cannot_be_read(world, org) -> None:
    """Reviews and inline review comments are part of the PR's indexed text. If GitHub
    fails to list them, the stream must fail so indexing retries, rather than index the
    PR without them and never look again (the PR's revision does not change)."""
    gh, repo = world.gh, org["repo"]
    pr = gh.add_pull(repo, "Refactor", changes={"src/app.py": "new"})
    pr.reviews.append(Review(next_id(), "Please split this into two PRs", org["alice"], "CHANGES_REQUESTED", gh.clock.now()))
    pr.review_comments.append(ReviewComment(next_id(), "This leaks the handle", org["alice"], "src/app.py", 1, None,
                                            gh.clock.now(), gh.clock.now()))
    await world.sync()
    fail(world, f"/pulls/{pr.number}/reviews", 502)
    fail(world, f"/pulls/{pr.number}/comments", 502)
    try:
        text = await world.stream_text(f"{repo.id}/pull/{pr.number}")
    except Exception:
        return
    assert "Please split" in text and "leaks the handle" in text, "partial PR content was served as complete"


async def test_pull_request_stream_fails_when_commits_cannot_be_read(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    pr = gh.add_pull(repo, "Refactor", changes={"src/app.py": "new"})
    await world.sync()
    fail(world, f"/pulls/{pr.number}/commits", 502)
    try:
        text = await world.stream_text(f"{repo.id}/pull/{pr.number}")
    except Exception:
        return
    assert "pr Refactor" in text, "partial PR content was served as complete"


async def test_edited_comment_reaches_the_index(world, org) -> None:
    """GitHub's docs do not say whether editing a comment changes the issue's
    ``updated_at``; live data does (docs/SOURCES.md): it does, to the second."""
    gh, repo = world.gh, org["repo"]
    issue = gh.add_issue(repo, "Outage")
    comment = gh.comment(issue, "Root cause: unknown")
    await world.sync()
    record = await world.record(f"{repo.id}/issues/{issue.number}")
    before = record.version, record.external_revision_id
    gh.edit_comment(issue, comment, "Root cause: expired certificate")
    await world.sync()
    record = await world.record(f"{repo.id}/issues/{issue.number}")
    assert (record.version, record.external_revision_id) != before, "the edit never reaches indexing"


async def test_code_file_between_1_and_5_mb_streams_through_the_blob_api(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.commit(repo, {"data/big.txt": "x" * (2 * 1024 * 1024)})
    await world.sync()
    body = await world.stream(f"/{repo.id}/blob/data/big.txt")
    assert len(body) == 2 * 1024 * 1024


async def test_indexing_filters_switch_off_indexing_per_type(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.add_pull(repo, "PR")
    world.set_indexing(issues=False, merge_requests=True, code_files=False)
    await world.sync()
    docs = await world.live_docs()
    status = {d["recordType"]: d.get("indexingStatus") for d in docs.values()}
    assert status["TICKET"] == "AUTO_INDEX_OFF" and status["CODE_FILE"] == "AUTO_INDEX_OFF"
    assert status["PULL_REQUEST"] != "AUTO_INDEX_OFF"


async def test_reindex_refreshes_a_changed_issue(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    issue = gh.add_issue(repo, "Old title")
    await world.sync()
    gh.edit_issue(issue, title="New title")
    record = await world.typed(f"{repo.id}/issues/{issue.number}")
    await world.connector.reindex_records([record])
    assert await world.names("TICKET") == ["New title"]


async def test_connection_test(world, org) -> None:
    await world.start()
    assert await world.connector.test_connection_and_access() is True
    world.gh.sync_token.revoked = True
    with pytest.raises(ConnectorInitError, match="Bad credentials"):
        await world.connector.test_connection_and_access()


async def test_repo_filter_options(world, org) -> None:
    gh = world.gh
    gh.add_repo(org["org"], "gadgets")
    await world.start()
    response = await world.connector.get_filter_options("repo_ids", page=1, limit=20)
    assert response.success
    assert {o.id for o in response.options} == {"acme/widgets", "acme/gadgets"}
    response = await world.connector.get_filter_options("org_ids", page=1, limit=20)
    assert [o.id for o in response.options] == ["acme"]


async def test_large_pull_request_costs_a_bounded_number_of_calls(world, org) -> None:
    """Each update of a PR is streamed again. Fetching every changed file's full
    content made one 300-file PR cost ~300 calls per update."""
    gh, repo = world.gh, org["repo"]
    pr = gh.add_pull(repo, "Vendor bump", changes={f"vendor/f{n}.py": str(n) for n in range(300)})
    await world.sync(index=False)
    start = len(gh.calls)
    text = await world.stream_text(f"{repo.id}/pull/{pr.number}")
    assert "+299" in text  # the last file's diff is still there
    calls = len(gh.calls) - start
    assert calls <= 50, f"one stream cost {calls} API calls"
