"""Who reads what the token's account reads: the PipesHub user who set the connector up (the creator), only
when GitLab's member listing proves they have no account of their own there.

A creator the listing names reads what their own membership gives, nothing more: no owner grant made for
the creator alone, and no link to the token's account. A creator a whole listing does not name gets the
token's view, and is linked to its account. Only the account's own email identifies a member, and GitLab shows
it only to an admin's token: a member known by a public email alone, or by none, may be the creator, so such a
listing proves nothing and gives them nothing.
"""

from typing import Any

from tests.integration.gitlab_simulated.glsim.model import OWNER, Membership
from tests.integration.gitlab_simulated.world import World

USERS, PERMISSION = "users", "permission"


def marker(world: World) -> list[str]:
    """The users a listing of this connector has named: the keys in the listed-authenticators marker."""
    return world.config_service.kv.get(f"/services/connectors/{world.connector_id}/listedAuthenticators") or []


def become_creator(world: World, email: str) -> None:
    world.creator_email = email
    world.creator_id = world.users[email]


def bot_token(world: World, org: dict[str, Any]) -> Any:  # noqa: ANN401
    """The connector signs in as a bot account that owns the group, and nobody's PipesHub account."""
    bot = world.gl.add_user("bot", world.email("bot"))
    org["group"].members[bot.id] = Membership(OWNER)
    world.gl.set_sync_account(bot)
    world.configure(org["project"])
    return bot


def admin_token(world: World, org: dict[str, Any]) -> Any:  # noqa: ANN401
    """The connector signs in as an instance admin who is a member of nothing: the member lists have no row for
    the token's account, so the connector puts the creator in them under its GitLab id, while they hold its view."""
    root = world.gl.add_user("root", world.email("root"), is_admin=True)
    world.gl.set_sync_account(root)
    world.configure(org["project"])
    return root


async def user_key(world: World, email: str) -> str:
    user = await world.graph.get_user_by_email(email, org_id=world.org_id)
    assert user is not None, email
    return user.id


async def linked_to(world: World, email: str) -> set[str]:
    """The keys of the accounts this user is linked to for the connector (authenticatedAs)."""
    edges = await world.graph.get_edges_from_node(f"{USERS}/{await user_key(world, email)}", "authenticatedAs")
    return {e["_to"].split("/", 1)[1] for e in edges or [] if e.get("connectorId") == world.connector_id}


async def own_roles(world: World, email: str) -> dict[str, str]:
    """{record group external id: role} of the grants this user holds directly on the connector's groups."""
    by_key = {d.get("_key") or d.get("id"): ext for ext, d in (await world.groups()).items()}
    edges = await world.graph.get_edges_from_node(f"{USERS}/{await user_key(world, email)}", PERMISSION)
    out = {}
    for e in edges or []:
        coll, key = e["_to"].split("/", 1)
        if coll == "recordGroups" and key in by_key:
            out[by_key[key]] = e.get("role")
    return out


async def test_a_listed_creator_without_the_token_gets_no_creator_only_owner_and_no_link(world, org) -> None:
    """The token is a bot's; the creator is a guest the project lists under their own account. They must not
    read the bot's view: not the confidential issue, not the code or merge requests a guest cannot read."""
    gl, p = world.gl, org["project"]
    bot_token(world, org)
    become_creator(world, org["gus"].email)
    gl.add_issue(p, "Open issue", author=org["dev"])
    gl.add_issue(p, "Confidential", author=org["dev"], confidential=True)
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()

    assert world.creator_listed()
    assert await linked_to(world, world.creator_email) == set(), "a listed creator is linked to the bot's account"
    # The connector grants every member OWNER on what their level reaches; a guest of the project reaches the
    # project and its work items, not the group, whose owner grant only the token's view gives.
    roles = await own_roles(world, world.creator_email)
    assert set(roles) == {str(p.id), f"{p.id}-work-items"}, f"a listed creator got a creator-only grant: {roles}"
    assert marker(world) == [await user_key(world, world.creator_email)]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_an_unlisted_creator_gets_nothing_from_a_token_shown_public_emails_only(world, org) -> None:
    """The bot is no admin: GitLab shows it each member's public email, which the member chose. Any of them may be
    the creator under another address, so nothing is proved."""
    gl, p = world.gl, org["project"]
    bot_token(world, org)
    become_creator(world, world.email("carol"))
    gl.add_issue(p, "Open issue", author=org["dev"])
    gl.add_issue(p, "Confidential", author=org["dev"], confidential=True)
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()

    assert not world.creator_listed()
    assert not world.creator_proved_absent()
    assert await linked_to(world, world.creator_email) == set()
    assert await own_roles(world, world.creator_email) == {}
    assert await world.visible_to(world.creator_email) == set()
    assert marker(world) == []
    diff = await world.diff()
    assert diff.clean(), diff


async def test_a_creator_an_admin_token_lists_by_their_own_email_alone_is_not_linked(world, org) -> None:
    """The creator's public email is another address: only the admin's token is shown the one they use here."""
    gl, p = world.gl, org["project"]
    admin_token(world, org)
    become_creator(world, org["gus"].email)
    org["gus"].public_email = world.email("gus-public")
    gl.add_issue(p, "Confidential", author=org["dev"], confidential=True)
    await world.sync()

    assert world.creator_listed()
    assert await linked_to(world, world.creator_email) == set()
    assert marker(world) == [await user_key(world, world.creator_email)]


async def test_an_unlisted_creator_gets_the_view_of_an_admin_token_with_no_membership(world, org) -> None:
    """The row the connector makes for the creator under the token's GitLab id is not read from GitLab, and does
    not make the creator a listed member, on this sync or the next."""
    gl, p = world.gl, org["project"]
    root = admin_token(world, org)
    become_creator(world, world.email("carol"))
    gl.add_issue(p, "Open issue", author=org["dev"])
    gl.add_issue(p, "Confidential", author=org["dev"], confidential=True)
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    await world.sync()      # the row the first sync wrote for the creator must not read as a listing on the next

    assert world.creator_proved_absent()
    assert marker(world) == []
    assert await linked_to(world, world.creator_email) == {await user_key(world, root.email)}
    assert (await own_roles(world, world.creator_email)).get(org["group"].full_path) == "OWNER"
    diff = await world.diff()
    assert diff.clean(), diff


async def test_a_member_with_a_hidden_email_leaves_an_unlisted_creator_without_the_view(world, org) -> None:
    """That member may be the creator under their own account: not in the listing is not proved."""
    gl, p = world.gl, org["project"]
    bot_token(world, org)
    become_creator(world, world.email("carol"))
    org["dev"].public_email = ""
    gl.add_issue(p, "Confidential", author=org["rita"], confidential=True)
    await world.sync()

    assert not world.creator_listed()
    assert not world.creator_proved_absent()
    assert await linked_to(world, world.creator_email) == set()
    assert await own_roles(world, world.creator_email) == {}
    assert await world.visible_to(world.creator_email) == set()
    assert marker(world) == []
    diff = await world.diff([world.creator_email])
    assert diff.clean(), diff
