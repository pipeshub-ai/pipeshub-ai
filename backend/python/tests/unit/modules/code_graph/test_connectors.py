"""Which connectors count as a source of code.

The spelling cases carry the weight here: the same connector is stored three
different ways depending on which node you read it off, and a miss means the
code-graph tools silently vanish from an agent that plainly has a repo.
"""
import pytest

from app.config.constants.arangodb import Connectors
from app.modules.code_graph.connectors import (
    CODE_CONNECTOR_TYPES,
    agent_knowledge_has_code_connector,
)


class TestSpellings:
    @pytest.mark.parametrize("stored", [
        "gitlab",           # attached knowledge
        "GitLab",           # App node
        "GITLAB",           # Record.connectorName
        "gitlab_personal",  # attached knowledge, multi-word
        "GITLAB PERSONAL",  # the Connectors enum value itself
        "GitLab Personal",  # App node, multi-word
        "github",
    ])
    def test_every_spelling_of_a_repo_connector_matches(self, stored: str) -> None:
        assert agent_knowledge_has_code_connector([{"type": stored}]) is True

    def test_enum_values_are_matched_verbatim(self) -> None:
        """Guards the underscore/space fold: `GITLAB_PERSONAL.value` is
        `"GITLAB PERSONAL"`, so an upper()-only comparison would miss the
        `gitlab_personal` spelling that attached knowledge actually uses."""
        for value in CODE_CONNECTOR_TYPES:
            assert agent_knowledge_has_code_connector([{"type": value}]) is True


class TestNonCodeConnectors:
    @pytest.mark.parametrize("stored", ["jira", "slack", "confluence", "drive", "KB"])
    def test_other_connectors_do_not_count(self, stored: str) -> None:
        assert agent_knowledge_has_code_connector([{"type": stored}]) is False

    def test_one_repo_among_several_connectors_is_enough(self) -> None:
        knowledge = [{"type": "jira"}, {"type": "slack"}, {"type": "gitlab"}]
        assert agent_knowledge_has_code_connector(knowledge) is True


class TestEmptyAndMalformed:
    @pytest.mark.parametrize("knowledge", [None, [], [{}], [{"type": None}], ["not-a-dict"]])
    def test_nothing_attached_is_false_not_an_error(self, knowledge) -> None:
        assert agent_knowledge_has_code_connector(knowledge) is False


class TestOrgLevelCheck:
    """`has_code_connector_configured` — the org half of the pair, mirroring
    `has_slack_connector_configured`."""

    class _Provider:
        def __init__(self, instances, raises=False) -> None:
            self.instances = instances
            self.raises = raises

        async def get_user_connector_instances(self, **_kwargs):
            if self.raises:
                raise RuntimeError("graph is down")
            return self.instances

    async def _check(self, instances, raises=False) -> bool:
        from app.modules.code_graph.connectors import has_code_connector_configured

        return await has_code_connector_configured(
            self._Provider(instances, raises), "user-1", "org-1"
        )

    @pytest.mark.asyncio
    async def test_configured_repo_connector_is_found(self) -> None:
        assert await self._check([{"type": "GitLab", "isConfigured": True}]) is True

    @pytest.mark.asyncio
    async def test_unconfigured_connector_does_not_count(self) -> None:
        """Present but never set up — the tools would load and find nothing."""
        assert await self._check([{"type": "GitLab", "isConfigured": False}]) is False

    @pytest.mark.asyncio
    async def test_other_connectors_do_not_count(self) -> None:
        assert await self._check([{"type": "Slack", "isConfigured": True}]) is False

    @pytest.mark.asyncio
    async def test_no_instances(self) -> None:
        assert await self._check([]) is False

    @pytest.mark.asyncio
    async def test_a_failed_probe_reports_absence_rather_than_raising(self) -> None:
        """This runs on every request; a graph hiccup must not kill the turn."""
        assert await self._check(None, raises=True) is False


def test_gitlab_is_covered_because_it_is_the_only_producer_today() -> None:
    """GitLab is the sole connector enabling the `code_files` indexing filter
    (`connectors/sources/gitlab/connector.py`). If this ever fails, a rename
    happened and the gate is now shut on every agent."""
    assert Connectors.GITLAB.value in CODE_CONNECTOR_TYPES
    assert Connectors.GITLAB_PERSONAL.value in CODE_CONNECTOR_TYPES
