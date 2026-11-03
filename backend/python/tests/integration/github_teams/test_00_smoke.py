"""A first sync of a small org matches GitHub, record for record and user for user.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_first_sync_matches_github(world, org) -> None:
    gh = world.gh
    issue = gh.add_issue(org["repo"], "Login fails", body="steps")
    gh.comment(issue, "seen it too", org["alice"])
    gh.add_pull(org["repo"], "Fix login", changes={"src/login.py": "ok"})
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert "Login fails" in await world.names("TICKET")
