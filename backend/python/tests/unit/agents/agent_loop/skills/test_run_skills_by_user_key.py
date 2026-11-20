"""A run reads and writes skills as the asker's graph user key, the kind of id a skill's `createdBy`
holds. The account id the request was signed with is another kind, compared only with an account id.

So a person's own skill made on the skills page reaches their own runs, and a co-worker's never does.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.tools.builtin.data.skills import (
    LoadSkillTool,
    SkillManageTool,
    SkillSearchTool,
)
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.agents.agent_loop.skills.graph_store import (
    GraphSkillStore,
    is_creator,
    skill_visible_to,
)
from app.agents.agent_loop.skills.manager_factory import run_user_key
from app.agents.agent_loop.skills_wiring import build_skill_manager
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG = "org-1"
# Each person's account id (what a request is signed with) and graph user key (what the skills page
# stamps as `createdBy`).
DANA_ACCOUNT, DANA_KEY = "6650f0aa00000000000000d1", "0efd15e5-0000-4000-8000-0000000000d1"
CAROL_ACCOUNT, CAROL_KEY = "6650f0aa00000000000000c1", "7990e47b-0000-4000-8000-0000000000c1"
OWN = "bluebird-notes"


class _Graph(FakeGraphProvider):
    """The org's skills, and the graph users a run reads its key from."""

    def __init__(self) -> None:
        super().__init__()
        self.user_reads = 0
        self.fail_user_reads = False
        self._users = {
            DANA_ACCOUNT: {"_key": DANA_KEY, "userId": DANA_ACCOUNT, "orgId": ORG},
            CAROL_ACCOUNT: {"_key": CAROL_KEY, "userId": CAROL_ACCOUNT, "orgId": ORG},
        }

    async def get_user_by_user_id(
        self, user_id: str, *, raise_on_error: bool = False, org_id: str | None = None,
    ) -> dict[str, Any] | None:
        self.user_reads += 1
        if self.fail_user_reads:
            raise ConnectionError("graph unavailable")
        user = self._users.get(user_id)
        if user is None or (org_id is not None and user["orgId"] != org_id):
            return None
        return dict(user)


def _md(name: str, description: str = "Use when asked about Bluebird", body: str = "The codeword is in R.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


async def _graph() -> _Graph:
    graph = _Graph()
    # As the skills page writes them: `createdBy` is the creator's graph user key.
    await GraphSkillStore(graph, ORG, DANA_KEY, visibility_scope=DANA_KEY).create_skill(OWN, _md(OWN))
    await GraphSkillStore(graph, ORG, CAROL_KEY, visibility_scope=CAROL_KEY).create_skill(
        "carol-private", _md("carol-private", "Use when asked about Carol's deals"),
    )
    await GraphSkillStore(graph, ORG, SEED_IDENTITY).create_skill("pdf-tools", _md("pdf-tools", "Use for PDF files"))
    graph._col("agentSkills")[f"{ORG}_pdf-tools"]["source"] = "builtin"
    return graph


def _context(graph: _Graph, account: str, *, key: str = "", **overrides: Any) -> Any:  # noqa: ANN401
    fields: dict[str, Any] = {
        "org_id": ORG, "user_id": account, "user_key": key, "graph_provider": graph, "retrieval_service": None,
        "user_info": {"userId": account, "orgId": ORG},
    }
    return make_context(**(fields | overrides))


async def _loads(manager: Any, name: str) -> bool:  # noqa: ANN401
    """Whether `load_skill` hands the model the skill: a skill it can't see comes back as an error."""
    out = await LoadSkillTool(manager).execute(name=name)
    return out.success and isinstance(out.data, dict) and "error" not in out.data and out.data.get("name") == name


async def _run_manager(context: Any, *, reads_own: bool = True) -> Any:  # noqa: ANN401
    extension = MagicMock()
    extension.reads_own_skills = MagicMock(return_value=reads_own)
    with patch("app.agents.agent_loop.skills.manager_factory.schedule_builtin_skill_reconcile"), patch(
        "app.modules.agents.chat_extension.get_chat_extension", return_value=extension,
    ):
        return await build_skill_manager(context, MagicMock())


def _names(manager: Any) -> set[str]:  # noqa: ANN401
    return {m.name for m in manager.catalog_snapshot()}


class TestAPersonsOwnSkillInTheirOwnRun:
    async def test_it_is_in_the_catalog_and_skill_search_finds_it(self) -> None:
        manager = await _run_manager(_context(await _graph(), DANA_ACCOUNT, key=DANA_KEY))

        assert _names(manager) == {OWN, "pdf-tools"}
        found = await SkillSearchTool(manager).execute(query="Bluebird")
        assert found.success and OWN in str(found.data)

    async def test_it_loads(self) -> None:
        manager = await _run_manager(_context(await _graph(), DANA_ACCOUNT, key=DANA_KEY))

        assert await _loads(manager, OWN)
        assert "The codeword is in R." in str((await LoadSkillTool(manager).execute(name=OWN)).data)

    async def test_fix_my_skill_edits_it(self) -> None:
        graph = await _graph()
        manager = await _run_manager(_context(graph, DANA_ACCOUNT, key=DANA_KEY))

        out = await SkillManageTool(manager).execute(
            action="patch", name=OWN, old_string="The codeword is in R.", new_string="The budget is 4.2 million.",
        )
        assert out.success, out.error
        assert "4.2 million" in graph._col("agentSkills")[f"{ORG}_{OWN}"]["content"]

    async def test_a_skill_the_run_creates_is_stamped_with_the_graph_user_key(self) -> None:
        graph = await _graph()
        manager = await _run_manager(_context(graph, DANA_ACCOUNT, key=DANA_KEY))

        out = await SkillManageTool(manager).execute(
            action="create", name="bluebird-budget", description="Use for the Bluebird budget", body="4.2 million",
        )
        assert out.success, out.error
        assert graph._col("agentSkills")[f"{ORG}_bluebird-budget"]["createdBy"] == DANA_KEY
        # So the skills page, which scopes by the key, lists it as hers.
        page = GraphSkillStore(graph, ORG, DANA_KEY, visibility_scope=DANA_KEY)
        assert "bluebird-budget" in {m.name for m in await page.list_skills()}

    async def test_without_the_key_on_the_context_the_run_reads_it_from_the_user_record(self) -> None:
        graph = await _graph()
        context = _context(graph, DANA_ACCOUNT)

        assert OWN in _names(await _run_manager(context))
        assert graph.user_reads == 1 and context.user_key == DANA_KEY

    async def test_in_a_turn_others_read_it_stays_out(self) -> None:
        manager = await _run_manager(_context(await _graph(), DANA_ACCOUNT, key=DANA_KEY), reads_own=False)

        assert _names(manager) == {"pdf-tools"}
        assert not await _loads(manager, OWN)


class TestACoworkersSkill:
    async def test_carols_run_never_sees_danas_skill(self) -> None:
        manager = await _run_manager(_context(await _graph(), CAROL_ACCOUNT, key=CAROL_KEY))

        assert _names(manager) == {"carol-private", "pdf-tools"}
        assert OWN not in str((await SkillSearchTool(manager).execute(query="Bluebird")).data)
        assert not await _loads(manager, OWN)

    async def test_carols_run_cannot_edit_it(self) -> None:
        graph = await _graph()
        manager = await _run_manager(_context(graph, CAROL_ACCOUNT, key=CAROL_KEY))

        out = await SkillManageTool(manager).execute(action="patch", name=OWN, old_string="R.", new_string="X.")
        assert not out.success
        assert "The codeword is in R." in graph._col("agentSkills")[f"{ORG}_{OWN}"]["content"]


class TestASkillAnEarlierRunCreated:
    """Runs used to stamp the account id as `createdBy`: such a skill is still its creator's, and only theirs."""

    async def _graph_with_older_skills(self) -> _Graph:
        graph = await _graph()
        await GraphSkillStore(graph, ORG, DANA_ACCOUNT).create_skill("dana-older", _md("dana-older", "Use for old notes"))
        await GraphSkillStore(graph, ORG, CAROL_ACCOUNT).create_skill("carol-older", _md("carol-older", "Use for hers"))
        return graph

    async def test_it_reaches_its_creators_run_and_can_be_edited_there(self) -> None:
        graph = await self._graph_with_older_skills()
        manager = await _run_manager(_context(graph, DANA_ACCOUNT, key=DANA_KEY))

        assert _names(manager) == {OWN, "dana-older", "pdf-tools"}
        out = await SkillManageTool(manager).execute(action="patch", name="dana-older", old_string="R.", new_string="S.")
        assert out.success, out.error

    async def test_it_never_reaches_anyone_elses_run(self) -> None:
        manager = await _run_manager(_context(await self._graph_with_older_skills(), CAROL_ACCOUNT, key=CAROL_KEY))

        assert "dana-older" not in _names(manager)


class TestNoKeyNoSkills:
    async def test_a_person_with_no_graph_user_gets_no_skills(self) -> None:
        assert await _run_manager(_context(await _graph(), "6650f0aa00000000000000ee")) is None

    async def test_a_failed_read_of_the_key_gets_no_skills(self) -> None:
        graph = await _graph()
        graph.fail_user_reads = True
        assert await _run_manager(_context(graph, DANA_ACCOUNT)) is None

    async def test_the_key_of_a_user_in_another_org_is_not_read(self) -> None:
        graph = await _graph()
        assert await run_user_key(_context(graph, DANA_ACCOUNT, org_id="org-2")) is None


class TestTheComparison:
    @pytest.mark.parametrize(
        ("created_by", "key", "account", "expected"),
        [
            (DANA_KEY, DANA_KEY, DANA_ACCOUNT, True),
            (DANA_ACCOUNT, DANA_KEY, DANA_ACCOUNT, True),
            (DANA_KEY, CAROL_KEY, CAROL_ACCOUNT, False),
            (DANA_ACCOUNT, CAROL_KEY, CAROL_ACCOUNT, False),
            (DANA_KEY, None, DANA_ACCOUNT, False),
            ("", DANA_KEY, DANA_ACCOUNT, False),
            (None, DANA_KEY, DANA_ACCOUNT, False),
        ],
        ids=["the key", "an older run's account id", "a co-worker's key", "a co-worker's account id",
             "no key to compare with", "no creator", "a missing creator"],
    )
    def test_a_creator_is_matched_by_one_kind_of_id_at_a_time(
        self, created_by: str | None, key: str | None, account: str, expected: bool,
    ) -> None:
        assert is_creator({"createdBy": created_by}, key, account) is expected

    def test_the_skills_page_matches_by_the_key_alone(self) -> None:
        assert skill_visible_to({"createdBy": DANA_KEY}, DANA_KEY)
        assert not skill_visible_to({"createdBy": DANA_ACCOUNT}, DANA_KEY)
