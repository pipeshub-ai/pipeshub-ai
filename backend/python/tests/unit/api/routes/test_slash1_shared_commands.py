"""SLASH1 S4: team and org /commands, through the real skills routes over a real store.

Who sees a shared command (a team's members; the whole org; never another org), who may publish and change it (a
team's owners, an org admin), that a run keeps the command content it was admitted with (the SKP1 pin), and what
happens to a deleted user's commands (O3: personal ones are erased, shared ones pass to the org).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agent_loop_lib.commands.render import render_command
from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md
from app.agents.agent_loop.skills.bundle_source import PinnedSkills
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, erase_skill_author
from app.api.routes.skills import (
    ExpandRequest,
    PublishRequest,
    SkillWriteRequest,
    _build_manager,
    command_provenance,
    command_share_targets,
    create_skill,
    expand_command,
    export_skill,
    get_categories,
    get_skill,
    list_skills,
    publish_command,
    search_skills,
    update_skill,
)
from app.harness.contracts.state import RunCommand
from app.schema.arango.documents import agent_skills_schema
from tests.support.bound_org import another_request, bind_test_org
from tests.unit.api.routes.test_skill_commands import TRIAGE
from tests.unit.api.routes.test_skills_custom_lifecycle import (
    _as_role,
    _LifecycleGraphProvider,
    _request,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from unittest.mock import MagicMock

ORG, OTHER_ORG = "org-1", "org-2"


@pytest.fixture
def graph() -> _LifecycleGraphProvider:
    g = _LifecycleGraphProvider()
    for user, name, org in (("asha", "Asha Rao", ORG), ("bo", "Bo Lin", ORG), ("cy", "Cy Park", ORG), ("dee", "Dee Ng", OTHER_ORG)):
        doc = {"_key": f"{user}-key", "userId": user, "orgId": org, "fullName": name}
        g._users[user] = g._col("users")[f"{user}-key"] = doc
    g._col("teams")["ops"] = {"name": "Ops", "orgId": ORG}
    g._col("teams")["sales"] = {"name": "Sales", "orgId": ORG}
    g._col("teams")["foreign"] = {"name": "Foreign", "orgId": OTHER_ORG}
    # Asha owns Ops, Bo is a member of it; Cy is in no team. Dee is in another org.
    g.team_roles[("asha-key", ORG)] = [{"teamId": "ops", "role": "OWNER"}]
    g.team_roles[("bo-key", ORG)] = [{"teamId": "ops", "role": "MEMBER"}]
    return g


def _req(graph: _LifecycleGraphProvider, user: str, org: str = ORG) -> MagicMock:
    return _request(user, org, graph)


async def _menu(req) -> dict[str, dict]:
    return {c["name"]: c for c in json.loads((await list_skills(req, invocation="user")).body)["skills"]}


async def _editor(req) -> set[str]:
    return {s["name"] for s in json.loads((await list_skills(req)).body)["skills"]}


async def _team_triage(graph) -> None:
    asha = _req(graph, "asha")
    await create_skill(asha, TRIAGE)
    with _as_role("member"):
        await publish_command(asha, "triage", PublishRequest(scope="team", teamId="ops"))


class TestWhoSeesASharedCommand:
    async def test_a_team_member_sees_it_with_its_author_and_team(self, graph) -> None:
        await _team_triage(graph)
        triage = (await _menu(_req(graph, "bo")))["triage"]
        assert (triage["owner"], triage["author"], triage["team"], triage["scope"]) == ("team", "Asha Rao", "Ops", "team")

        body = json.loads((await expand_command(_req(graph, "bo"), "triage", ExpandRequest(args="INC-1"))).body)
        assert body["expanded"] == "Look up incident INC-1 in Jira. Depth: quick."
        # A sent command keeps ids, never names; "What was sent" reads the names when shown.
        assert (body["owner"], body["authorId"], body["teamId"], body["authorFormer"]) == ("team", "asha", "ops", False)
        assert "author" not in body and "team" not in body
        names = json.loads((await command_provenance(_req(graph, "bo"), author_id="asha", team_id="ops")).body)
        assert names == {"author": "Asha Rao", "authorFormer": False, "team": "Ops"}

    async def test_a_deleted_or_foreign_author_reads_as_a_former_member(self, graph) -> None:
        graph._users["asha"]["isActive"] = False
        bo = _req(graph, "bo")
        assert json.loads((await command_provenance(bo, author_id="asha")).body)["authorFormer"] is True
        foreign = json.loads((await command_provenance(bo, author_id="dee", team_id="foreign")).body)
        assert foreign == {"author": None, "authorFormer": True, "team": None}

    async def test_someone_outside_the_team_neither_sees_nor_runs_it(self, graph) -> None:
        await _team_triage(graph)
        cy = _req(graph, "cy")
        assert "triage" not in await _menu(cy)
        with pytest.raises(HTTPException) as exc:
            await expand_command(cy, "triage", ExpandRequest(args="INC-1"))
        assert exc.value.detail["code"] == "command_not_found"

    async def test_an_org_command_reaches_everyone_in_the_org_and_no_other_org(self, graph) -> None:
        asha = _req(graph, "asha")
        await create_skill(asha, TRIAGE)
        with _as_role("admin"):
            await publish_command(asha, "triage", PublishRequest(scope="org"))
        assert (await _menu(_req(graph, "cy")))["triage"]["owner"] == "org"

        graph.team_roles[("dee-key", OTHER_ORG)] = [{"teamId": "ops", "role": "OWNER"}]
        dee = _req(graph, "dee", OTHER_ORG)
        with another_request(OTHER_ORG):
            assert "triage" not in await _menu(dee)
            with pytest.raises(HTTPException) as exc:
                await expand_command(dee, "triage", ExpandRequest(args="INC-1"))
        assert exc.value.status_code == 404

    async def test_the_editor_lists_only_what_the_caller_may_change(self, graph) -> None:
        await _team_triage(graph)
        assert "triage" not in await _editor(_req(graph, "bo"))
        assert "triage" in await _editor(_req(graph, "asha"))


class TestWhoMayPublishAndChange:
    async def test_a_member_cannot_share_to_a_team_they_do_not_own(self, graph) -> None:
        bo = _req(graph, "bo")
        await create_skill(bo, TRIAGE)
        with _as_role("member"), pytest.raises(HTTPException) as exc:
            await publish_command(bo, "triage", PublishRequest(scope="team", teamId="ops"))
        assert exc.value.status_code == 403

    async def test_only_an_org_admin_shares_with_the_org(self, graph) -> None:
        asha = _req(graph, "asha")
        await create_skill(asha, TRIAGE)
        with _as_role("member"), pytest.raises(HTTPException) as exc:
            await publish_command(asha, "triage", PublishRequest(scope="org"))
        assert exc.value.status_code == 403
        with _as_role("admin"):
            assert (await publish_command(asha, "triage", PublishRequest(scope="org"))).status_code == 200

    async def test_a_team_of_another_org_is_not_found(self, graph) -> None:
        asha = _req(graph, "asha")
        await create_skill(asha, TRIAGE)
        with _as_role("admin"), pytest.raises(HTTPException) as exc:
            await publish_command(asha, "triage", PublishRequest(scope="team", teamId="foreign"))
        assert exc.value.status_code == 404

    async def test_a_skill_that_is_not_a_command_is_not_shared(self, graph) -> None:
        asha = _req(graph, "asha")
        await create_skill(asha, SkillWriteRequest(name="runbook", description="d", body="b"))
        with _as_role("admin"), pytest.raises(HTTPException) as exc:
            await publish_command(asha, "runbook", PublishRequest(scope="org"))
        assert exc.value.status_code == 400

    async def test_share_targets_are_the_owned_teams_and_the_org_for_an_admin(self, graph) -> None:
        with _as_role("member"):
            targets = json.loads((await command_share_targets(_req(graph, "asha"))).body)
        assert targets == {"org": False, "teams": [{"id": "ops", "name": "Ops"}]}
        with _as_role("admin"):
            assert json.loads((await command_share_targets(_req(graph, "cy"))).body) == {"org": True, "teams": []}

    async def test_a_member_cannot_edit_a_team_command_and_its_owner_can(self, graph) -> None:
        await _team_triage(graph)
        edit = SkillWriteRequest(description="Triage", body="Changed $incident.")
        with _as_role("member"), pytest.raises(HTTPException) as exc:
            await update_skill(_req(graph, "bo"), "triage", edit)
        assert exc.value.status_code == 403
        with _as_role("member"):
            assert (await update_skill(_req(graph, "asha"), "triage", edit)).status_code == 200
        assert graph._col("agentSkills")[f"{ORG}_triage"]["scope"] == "team"

    async def test_the_authors_own_agent_cannot_change_a_shared_command(self, graph) -> None:
        await _team_triage(graph)
        runtime = GraphSkillStore(graph, ORG, "asha-key")  # type: ignore[arg-type]
        with pytest.raises(RegistryError):
            await runtime.update_skill("triage", "---\nname: triage\ndescription: x\n---\n\nInjected.\n")


class TestPinning:
    async def test_an_edit_after_admission_does_not_change_what_the_run_holds(self, graph) -> None:
        await _team_triage(graph)
        bo = _req(graph, "bo")
        admitted = json.loads((await expand_command(bo, "triage", ExpandRequest(args="INC-7 full"))).body)

        with _as_role("member"):
            await update_skill(_req(graph, "asha"), "triage", SkillWriteRequest(description="T", body="Other $incident."))
        later = json.loads((await expand_command(bo, "triage", ExpandRequest(args="INC-7 full"))).body)
        assert later["contentDigest"] != admitted["contentDigest"]

        files = await PinnedSkills(graph, ORG, None).files("triage", admitted["contentDigest"])  # type: ignore[arg-type]
        pinned = parse_skill_md(files["SKILL.md"].decode("utf-8"))
        assert render_command(pinned.body, pinned.metadata.arguments, "INC-7 full").text == admitted["expanded"]


class TestAuthorLeaves:
    async def test_personal_commands_are_erased_and_shared_ones_pass_to_the_org(self, graph) -> None:
        await _team_triage(graph)
        asha = _req(graph, "asha")
        await create_skill(asha, TRIAGE.model_copy(update={"name": "mine"}))
        await update_skill(asha, "mine", SkillWriteRequest(description="v2", body="v2 $incident."))

        assert await erase_skill_author(graph, ORG, ["asha-key", "asha"]) == (1, 1)  # type: ignore[arg-type]
        assert f"{ORG}_mine" not in graph._col("agentSkills")
        assert not [v for v in graph._col("agentSkillVersions").values() if v["name"] == "mine"]

        doc = graph._col("agentSkills")[f"{ORG}_triage"]
        assert (doc["createdBy"], doc["authorFormer"], doc["scope"]) == (None, True, "team")
        assert not [e for e in graph._edges.get("permission", []) if e.get("to_id") == f"{ORG}_triage"]
        triage = (await _menu(_req(graph, "bo")))["triage"]
        assert (triage["author"], triage["authorFormer"]) == (None, True)
        body = json.loads((await expand_command(_req(graph, "bo"), "triage", ExpandRequest(args="INC-1"))).body)
        assert (body["authorFormer"], body["authorId"]) == (True, None)

        assert await erase_skill_author(graph, ORG, ["asha-key", "asha"]) == (0, 0)  # type: ignore[arg-type]

    async def test_the_org_admin_governs_a_command_whose_author_left(self, graph) -> None:
        await _team_triage(graph)
        await erase_skill_author(graph, ORG, ["asha-key"])  # type: ignore[arg-type]
        with _as_role("admin"):
            resp = await update_skill(_req(graph, "cy"), "triage", SkillWriteRequest(description="T", body="Kept $incident."))
        assert resp.status_code == 200


def test_every_column_the_store_writes_is_in_the_arango_schema() -> None:
    """Arango validates `agentSkills` strictly (`additionalProperties: false`), so a column missing here fails the
    write on Arango only."""
    store = GraphSkillStore(None, ORG, "u")  # type: ignore[arg-type]
    skill = parse_skill_md("---\nname: t\ndescription: d\n---\n\nb\n")
    doc = store._skill_to_doc(skill, resources={}, created_by="u", created_at=1, updated_at=1)
    written = {*doc, "scope", "teamId", "authorFormer"} - {"id"}
    assert written <= set(agent_skills_schema["rule"]["properties"])


def test_a_worker_takes_the_command_node_sends_and_refuses_display_fields() -> None:
    """Node strips author and team from the run input (`workerCommand`): this strict model, which workers built
    before S4 also run, refuses fields it does not know."""
    sent = {
        "name": "triage", "version": "1.0.1", "contentDigest": "sha256:" + "a" * 64,
        "args": {"incident": "INC-1"}, "typed": "/triage INC-1", "owner": "team",
    }
    assert RunCommand.model_validate(sent).owner == "team"
    with pytest.raises(ValidationError):
        RunCommand.model_validate({**sent, "authorId": "u-asha"})


class TestOnlyARouteThatNeedsTheAnswerAsksForTheRole:
    """Node is asked whether the caller is an org admin only by a route that hands ``_build_manager`` the question:
    a read of an org command never asks, however the caller stands."""

    async def test_reading_an_org_command_never_asks_node(self, graph) -> None:
        asha = _req(graph, "asha")
        await create_skill(asha, TRIAGE)
        with _as_role("admin"):
            await publish_command(asha, "triage", PublishRequest(scope="org"))
        cy = _req(graph, "cy")
        never = AsyncMock(side_effect=AssertionError("a read asked Node for the caller's role"))
        with patch("app.api.routes.skills.fetch_caller_role", new=never):
            assert "triage" in await _menu(cy)
            for read in (
                get_categories(cy),
                search_skills(cy, q="triage", limit=10),
                get_skill(cy, "triage"),
                export_skill(cy, "triage"),
                expand_command(cy, "triage", ExpandRequest(args="INC-1")),
            ):
                await read
        never.assert_not_awaited()

    async def test_changing_a_skill_without_the_question_is_refused_before_anything_is_read(self, graph) -> None:
        with pytest.raises(TypeError, match="ask_admin"):
            await _build_manager(_req(graph, "asha"), manage="triage")


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """The auth middleware binds the verified token's org; these tests call the route functions directly."""
    yield from bind_test_org(ORG)
