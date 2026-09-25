"""The @pipeshub-ai/mcp package, run against a real PipesHub.

Every other test of this package runs it in isolation against fixed example
data. This is the only place the package and a real server are asked whether
they still understand each other, which is the one thing a release actually
depends on.

Nothing here imports from the package. It is run as a separate process, over
the command line, holding a token minted the way a real MCP host mints one --
because that is how a customer runs it, and anything closer would be testing a
different thing.
"""

from __future__ import annotations

import logging

import pytest

logger = logging.getLogger("mcp-package")

pytestmark = [pytest.mark.integration]

# From the package's documented contract. Agents branch on these, so a change
# to any of them is a breaking change whether or not anyone announces it.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAUTHENTICATED = 3


@pytest.fixture(scope="session", autouse=True)
def _report_versions(package_spec: str, resolved_package_version: str, mcp_base_url: str) -> None:
    """Say which build was tested, in the log every failure is read alongside.

    The published package moves without anyone here changing anything, so when
    one of these tests starts failing the version is the difference between an
    obvious cause and a mystery.
    """
    logger.info(
        "Testing %s (resolved to %s) against %s",
        package_spec, resolved_package_version, mcp_base_url,
    )


class TestTheCliCanReachPipesHub:
    def test_whoami_identifies_the_signed_in_user(self, run_cli) -> None:
        result = run_cli("directory", "whoami")

        assert result.exit_code == EXIT_OK, result.stderr
        body = result.json()
        assert body.get("userId"), f"whoami returned no user: {body}"
        assert body.get("orgId"), f"whoami returned no organization: {body}"

    def test_a_revoked_or_absent_token_is_reported_as_unauthenticated(self, run_cli) -> None:
        """Exit 3 is the contract. A host tells the person to sign in again on it."""
        result = run_cli("directory", "whoami", token="")

        assert result.exit_code == EXIT_UNAUTHENTICATED, (
            f"expected exit {EXIT_UNAUTHENTICATED} with no token, "
            f"got {result.exit_code}: {result.stdout} {result.stderr}"
        )

    def test_an_unknown_command_is_a_usage_error(self, run_cli) -> None:
        result = run_cli("not-a-command")

        assert result.exit_code == EXIT_USAGE, result.stderr


class TestTheToolsReturnRealData:
    def test_sources_lists_the_collection_this_test_created(
        self, run_cli, seeded_record
    ) -> None:
        """`sources` is step one of every scoped search, so nothing works without it.

        Depends on the seeded collection rather than asserting the list is merely
        non-empty. On a shared run the suites ahead of this one delete what they
        created, so an organisation with nothing to list is a real outcome -- and
        the command answers exit 6 for it, which is correct and would make a bare
        "exit 0" assertion fail for the wrong reason.
        """
        result = run_cli("sources")

        assert result.exit_code == EXIT_OK, result.stderr
        listed = result.json().get("sources") or []
        names = [s.get("name") for s in listed]
        assert seeded_record["kb_name"] in names, (
            f"the collection this test created is not listed among {names}"
        )

    def test_search_finds_a_document_that_was_put_there(self, run_cli, seeded_record) -> None:
        """The whole chain: index, embed, search, and shape the hit for a model."""
        result = run_cli("search", seeded_record["query"], "--limit", "10")

        assert result.exit_code == EXIT_OK, result.stderr
        hits = result.json().get("hits") or []
        names = [h.get("recordName") for h in hits]
        assert seeded_record["name"] in names, (
            f"the seeded record {seeded_record['name']!r} was not among {names}"
        )

    def test_every_hit_can_be_opened(self, run_cli, seeded_record) -> None:
        """A hit without an id is not a result; nothing can be done with it."""
        result = run_cli("search", seeded_record["query"], "--limit", "5")

        hits = result.json().get("hits") or []
        assert hits, "search returned nothing to check"
        missing = [h for h in hits if not h.get("recordId")]
        assert not missing, f"{len(missing)} hit(s) carry no recordId: {missing}"

    def test_a_record_can_be_read_back(self, run_cli, seeded_record) -> None:
        result = run_cli("get", seeded_record["record_id"])

        assert result.exit_code == EXIT_OK, result.stderr
        assert seeded_record["needle"] in result.stdout, (
            "the document came back without the text it was seeded with"
        )
