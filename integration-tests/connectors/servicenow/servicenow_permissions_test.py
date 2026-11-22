# pyright: ignore-file

"""ServiceNow permissions: what each person can open in PipesHub is what ServiceNow lets them read.

The oracle is ServiceNow itself: the Table API called as each person (``readable_numbers``) runs the
same read ACL as the portal. Each test seeds the case its TC id names, and compares PipesHub's answer
(the record endpoint, which runs the same access check as search and chat) with the oracle for every
person: the instance's personas (README) and the throw-away users this module creates.

  order 1  TC-PERM-01/10/11/12/13  no criteria: only owner, managers and admins (incl. ownership group)
  order 1  TC-PERM-04..09          Can Read = user / group / role / department / location / company
  order 1  TC-PERM-14/15           a role contained in another role; a child group's members
  order 1  TC-PERM-17              a scripted criterion fails closed
  order 1  TC-PERM-18              Match All
  order 1  TC-PERM-19/19b/19c      Cannot Read on the KB and on an article; article Can Read (override off)
  order 1  TC-PERM-33              a user with no email is not synced
  order 1  TC-PERM-34              attachments have their article's access
  order 1  TC-PERM-35/39           whole-world oracle for every person (record endpoint and knowledge hub)
  order 1  regression              a KB with the platform's default criteria is open to every user
  order 1  search                  search finds an article for a reader, not for anyone else
  order 2  TC-PERM-20..30, 32, 36  criteria, memberships, roles, departments and users change; one incremental sync
  order 3  TC-PERM-31              a re-activated user gets their access back
  order 1/3 TC-NOTIF-05/07         the scripted criterion is reported once, in one run summary
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.servicenow.constants import NO_EMAIL_PERSONA, PERSONA_ROLE
from connectors.servicenow.servicenow_helpers import (
    Persona,
    add_account,
    assert_same_access,
    can_open,
    connector_title,
    kb_filter,
    kh_record_ids,
    notices,
    poll,
    record,
    servicenow_connector,
    sync,
    wait_file,
    wait_present,
)
from connectors.servicenow.servicenow_seed import (
    Article,
    ServiceNowRest,
    ServiceNowSeed,
    readable_numbers,
)
from helper.connector_visibility import found, search_connector_as
from helper.graph_provider import GraphProviderProtocol
from helper.indexing_wait import wait_until_record_indexing_completed
from helper.second_user import SecondUser

pytestmark = [
    pytest.mark.integration,
    pytest.mark.servicenow,
    pytest.mark.asyncio(loop_scope="session"),
]


@dataclass
class World:
    seed: ServiceNowSeed
    connector_id: str
    rest: dict[str, ServiceNowRest] = field(repr=False)  # person -> the instance as them
    accounts: dict[str, SecondUser] = field(repr=False)  # person -> their PipesHub account (holds tokens)
    personas: dict[str, Persona] = field(repr=False)
    rounds: dict[str, Any] = field(default_factory=dict)

    def art(self, key: str) -> Article:
        return self.seed.articles[key]

    def numbers(self, *keys: str) -> set[str]:
        return {self.art(k).number for k in keys}

    def kb_numbers(self, *kbs: str) -> set[str]:
        ids = {self.seed.kbs[k] for k in kbs}
        return {a.number for a in self.seed.articles.values() if a.kb in ids}

    async def oracle(self, numbers: set[str] | None = None) -> dict[str, set[str]]:
        """Person -> article numbers ServiceNow lets them read, over every KB of this module."""
        kb_ids = list(self.seed.kbs.values())
        names = list(self.rest)
        answers = await asyncio.gather(*(asyncio.to_thread(readable_numbers, self.rest[n], kb_ids) for n in names))
        return {n: (a & numbers if numbers is not None else a) for n, a in zip(names, answers)}

    async def check(self, graph: GraphProviderProtocol, numbers: set[str], label: str,
                    expected: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
        expected = expected if expected is not None else await self.oracle(numbers)
        await assert_same_access(graph, self.connector_id, expected, self.accounts, numbers, label=label)
        return expected


def _readers(oracle: dict[str, set[str]], number: str) -> set[str]:
    return {who for who, nums in oracle.items() if number in nums}


def _build(seed: ServiceNowSeed, p: dict[str, Persona], rest: ServiceNowRest) -> None:
    """The fixed world, then the objects the mutation round changes."""
    eng2 = seed.persona("phqa.eng2")
    eng = seed.persona("phqa.eng")
    hr = seed.persona("phqa.hr")
    role = rest.one("sys_user_role", f"name={PERSONA_ROLE}")
    assert role, f"the instance has no role {PERSONA_ROLE!r} (README, 'Instance fixture')"
    sid = {name: persona.sys_id for name, persona in p.items()}

    def kb(key: str, **kw: Any) -> str:
        return seed.kb(key, owner=sid["owner"], managers=[sid["kbmgr"]], **kw)


    readers = seed.group("readers", [sid["hr"]])
    seed.group("readers-child", [sid["eng"]], parent=readers)
    owners = seed.group("owners", [sid["ogroup"]])
    uc = seed.criterion
    c_group = uc("uc-group", group=readers)
    c_hr = uc("uc-user-hr", user=sid["hr"])
    kb("none")
    kb("group", read=[c_group])
    kb("user", read=[uc("uc-user-plain", user=sid["plain"])])
    kb("role", read=[uc("uc-role", role=role["sys_id"])])
    for key, field_name, row in (("dept", "department", eng2), ("loc", "location", eng2), ("co", "company", hr)):
        if row.get(field_name):
            kb(key, read=[uc(f"uc-{key}", **{field_name: row[field_name]})])
    kb("matchall", read=[uc("uc-matchall", group=readers, location=eng.get("location") or "", match_all="true")])
    kb("deny", read=[c_group], cannot=[c_hr])
    kb("art", read=[c_group])
    kb("defaults", keep_defaults=True)
    kb("contrib", contribute=[uc("uc-user-contrib", user=sid["contrib"])])
    kb("script", read=[uc("uc-script", advanced="true", script="answer = true;")])
    kb("noemail", read=[uc("uc-user-noemail", user=sid[NO_EMAIL_PERSONA])])

    a = seed.article
    a("n1", seed.kbs["none"])
    a("g1", seed.kbs["group"])
    a("g-og", seed.kbs["group"], ownership_group=owners)
    att = a("g-att", seed.kbs["group"])
    seed.attach(att, "phit-permissions.txt", b"PHIT permissions attachment", "text/plain")
    a("u1", seed.kbs["user"])
    a("r1", seed.kbs["role"])
    for key in ("dept", "loc", "co"):
        if key in seed.kbs:
            a(f"{key}1", seed.kbs[key])
    a("m1", seed.kbs["matchall"])
    a("x1", seed.kbs["deny"])
    a("a-inherit", seed.kbs["art"])
    a("a-read-eng", seed.kbs["art"], can_read_user_criteria=uc("uc-user-eng", user=sid["eng"]))
    a("a-cannot-hr", seed.kbs["art"], cannot_read_user_criteria=c_hr)
    a("df1", seed.kbs["defaults"])
    a("k1", seed.kbs["contrib"])
    a("s1", seed.kbs["script"])
    a("ne1", seed.kbs["noemail"])

    # Mutation round objects (TC-PERM-20..32).
    dept_a, _ = seed.department("dept-a"), seed.department("dept-b")
    seed.user("move", department=dept_a)
    seed.user("deact")
    role_user = seed.user("role")
    seed.grant_role(role_user.sys_id, seed.role("m-reader"))
    g_add = seed.group("gm-add")
    g_rem = seed.group("gm-remove", [sid["hr"]])
    g_deact = seed.group("gm-deact", [sid["eng"]])
    seed.group("gm-edit", [sid["eng2"]])
    kb("m-add-member", read=[uc("uc-m-add", group=g_add)])
    kb("m-remove-member", read=[uc("uc-m-rem", group=g_rem)])
    kb("m-group-deact", read=[uc("uc-m-gdeact", group=g_deact)])
    kb("m-dept", read=[uc("uc-m-dept", department=dept_a)])
    kb("m-role", read=[uc("uc-m-role", role=seed.roles["m-reader"])])
    kb("m-user-deact", read=[uc("uc-m-udeact", user=seed.users["deact"].sys_id)])
    kb("m-edit", read=[uc("uc-m-edit", user=sid["plain"])])
    kb("m-inactive", read=[uc("uc-m-inactive", user=sid["hr"])])
    kb("m-delete", read=[uc("uc-m-delete", user=sid["hr"])])
    kb("m-replace", read=[uc("uc-m-replace-old", user=sid["plain"])])
    uc("uc-m-replace-new", user=sid["eng"])
    uc("uc-m-kb-add", user=sid["hr"])
    kb("m-add")
    kb("m-art", read=[c_group])
    for key in ("m-add-member", "m-remove-member", "m-group-deact", "m-dept", "m-role", "m-user-deact", "m-edit",
                "m-inactive", "m-delete", "m-replace", "m-add"):
        a(f"{key}-1", seed.kbs[key])
    a("m-art-1", seed.kbs["m-art"], can_read_user_criteria=uc("uc-m-art", user=sid["eng"]))
    seed.publish_pending()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def world(
    sn_seed: ServiceNowSeed,
    sn_rest: ServiceNowRest,
    sn_personas: dict[str, Persona],
    sn_oauth_config: str,
    sn_accounts: dict[str, SecondUser],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncIterator[World]:
    await asyncio.to_thread(_build, sn_seed, sn_personas, sn_rest)
    rest = {name: p.rest for name, p in sn_personas.items() if p.rest}
    accounts = {name: p.account for name, p in sn_personas.items() if p.account}
    for key, user in sn_seed.users.items():
        rest[f"phit-{key}"] = sn_rest.as_user(user.user_name, user.password)
        accounts[f"phit-{key}"] = await asyncio.to_thread(add_account, pipeshub_client, sn_accounts, key, user.email)
    async with servicenow_connector(
        pipeshub_client, graph_provider, sn_oauth_config, name="servicenow-perm-it",
        filters=kb_filter(sn_seed.kbs.values()), min_records=len(sn_seed.articles),
    ) as connector_id:
        yield World(sn_seed, connector_id, rest, accounts, sn_personas)


# -- order 1: the fixed world -------------------------------------------------------------------


@pytest.mark.order(1)
async def test_tc_perm_01_10_11_12_13_kb_without_criteria_is_open_only_to_owner_managers_and_admins(
    world: World, graph_provider: GraphProviderProtocol,
) -> None:
    oracle = await world.check(graph_provider, world.numbers("n1"), "KB with no criteria")
    readers = _readers(oracle, world.art("n1").number)
    assert {"owner", "kbmgr", "kadmin"} <= readers, f"ServiceNow's own readers of a KB with no criteria: {readers}"
    assert "plain" not in readers and "hr" not in readers, readers


@pytest.mark.order(1)
async def test_tc_perm_11_ownership_group_member_reads_its_article(world: World, graph_provider: GraphProviderProtocol) -> None:
    await world.check(graph_provider, world.numbers("g-og"), "article with an ownership group")


@pytest.mark.order(1)
@pytest.mark.parametrize("key,who", [
    ("u1", "plain"),   # TC-PERM-04 user
    ("g1", "hr"),      # TC-PERM-05 group
    ("r1", "roledirect"),  # TC-PERM-06 role
    ("dept1", None),   # TC-PERM-07 department
    ("loc1", None),    # TC-PERM-08 location
    ("co1", "hr"),     # TC-PERM-09 company
])
async def test_tc_perm_04_to_09_can_read_by_one_kind_of_criterion(
    world: World, graph_provider: GraphProviderProtocol, key: str, who: str | None,
) -> None:
    if key not in world.seed.articles:
        pytest.skip(f"the persona this case reads from has no {key[:-1]} on the instance")
    oracle = await world.check(graph_provider, world.numbers(key), f"Can Read criterion ({key})")
    if who:
        assert who in _readers(oracle, world.art(key).number), f"ServiceNow does not let {who} read {key}: fixture changed?"


@pytest.mark.order(1)
async def test_tc_perm_14_contained_role_and_15_child_group(world: World, graph_provider: GraphProviderProtocol) -> None:
    oracle = await world.check(graph_provider, world.numbers("r1", "g1"), "contained role / child group")
    assert "rolec" in _readers(oracle, world.art("r1").number), "rolec should read through its container role"
    assert "eng" in _readers(oracle, world.art("g1").number), "eng should read through the child group"


@pytest.mark.order(1)
async def test_tc_perm_17_scripted_criterion_fails_closed(world: World, graph_provider: GraphProviderProtocol) -> None:
    """ServiceNow runs the script (here: everyone); the connector cannot, so only those who read
    without any criteria (owner, managers, admins: the readers of a KB with none) keep access."""
    oracle = await world.oracle(world.numbers("s1", "n1"))
    bypass = _readers(oracle, world.art("n1").number)
    script_readers = _readers(oracle, world.art("s1").number)
    assert script_readers - bypass, "the scripted criterion should let ServiceNow users in beyond the bypass users"
    expected = {who: ({world.art("s1").number} if who in bypass else set()) for who in world.accounts}
    await world.check(graph_provider, world.numbers("s1"), "scripted criterion (fail closed)", expected)


@pytest.mark.order(1)
async def test_tc_perm_18_match_all(world: World, graph_provider: GraphProviderProtocol) -> None:
    """Group Readers AND eng's location: hr is in the group but not at that location."""
    oracle = await world.check(graph_provider, world.numbers("m1", "g1"), "Match All criterion")
    assert _readers(oracle, world.art("m1").number) < _readers(oracle, world.art("g1").number)


@pytest.mark.order(1)
async def test_tc_perm_19_cannot_read_on_kb(world: World, graph_provider: GraphProviderProtocol) -> None:
    oracle = await world.check(graph_provider, world.numbers("x1"), "KB Cannot Read")
    readers = _readers(oracle, world.art("x1").number)
    assert "hr" not in readers and "eng" in readers, readers


@pytest.mark.order(1)
async def test_tc_perm_19b_19c_article_cannot_read_and_can_read(world: World, graph_provider: GraphProviderProtocol) -> None:
    """With ``glide.knowman.apply_article_read_criteria`` as the instance has it (the oracle decides)."""
    oracle = await world.check(graph_provider, world.numbers("a-inherit", "a-read-eng", "a-cannot-hr"),
                               "article-level criteria")
    assert "hr" not in _readers(oracle, world.art("a-cannot-hr").number)


@pytest.mark.order(1)
async def test_reg_default_criteria_kb_is_open_to_every_user(world: World, graph_provider: GraphProviderProtocol) -> None:
    """A new KB gets "Any User" can read: every user who can log in reads it, and so must PipesHub."""
    oracle = await world.check(graph_provider, world.numbers("df1"), "KB with the platform's default criteria")
    assert {"plain", "hr", "eng"} <= _readers(oracle, world.art("df1").number)


@pytest.mark.order(1)
async def test_tc_perm_33_user_without_email_is_not_synced(world: World, graph_provider: GraphProviderProtocol) -> None:
    sys_id = world.personas[NO_EMAIL_PERSONA].sys_id
    assert await graph_provider.get_user_by_source_id(sys_id, world.connector_id) is None
    await world.check(graph_provider, world.numbers("ne1"), "KB only a user without email may read")


@pytest.mark.order(1)
async def test_tc_perm_34_attachment_has_its_articles_access(world: World, graph_provider: GraphProviderProtocol) -> None:
    art = world.art("g-att")
    article = await wait_present(graph_provider, world.connector_id, art.number)
    file_id, _ = await wait_file(graph_provider, world.connector_id, art.number, next(iter(art.attachments)))
    attachment = await wait_present(graph_provider, world.connector_id, file_id)
    await world.check(graph_provider, {art.number}, "article with an attachment")
    differs = {}
    for who, user in world.accounts.items():
        a, f = await asyncio.to_thread(can_open, user, article.id), await asyncio.to_thread(can_open, user, attachment.id)
        if a != f:
            differs[who] = (a, f)
    assert not differs, f"attachment access differs from its article's (article, attachment): {differs}"


@pytest.mark.order(1)
async def test_tc_perm_35_39_every_person_sees_what_servicenow_lets_them_read(
    world: World, graph_provider: GraphProviderProtocol,
) -> None:
    """Whole fixed world, every person: no access leaks across KBs, nothing missing."""
    numbers = {a.number for k, a in world.seed.articles.items() if not k.startswith("m-") and k != "s1"}
    await world.check(graph_provider, numbers, "whole permission world")


@pytest.mark.order(1)
async def test_tc_perm_39_knowledge_hub_shows_each_person_what_servicenow_does(
    world: World, graph_provider: GraphProviderProtocol, pipeshub_client: PipeshubClient,
) -> None:
    keys = [k for k in world.seed.articles if not k.startswith("m-") and k != "s1"]
    numbers = {world.art(k).number for k in keys}
    oracle = await world.oracle(numbers)
    ids = {}
    for k in keys:
        rec = await wait_present(graph_provider, world.connector_id, world.art(k).number)
        ids[rec.id] = world.art(k).number
    wrong = {}
    for who, user in world.accounts.items():
        seen = {ids[r] for r in await asyncio.to_thread(kh_record_ids, pipeshub_client, user, world.connector_id) if r in ids}
        if seen != oracle[who]:
            wrong[who] = (sorted(oracle[who] - seen), sorted(seen - oracle[who]))
    assert not wrong, f"knowledge hub differs from ServiceNow (missing, extra): {wrong}"


@pytest.mark.order(1)
async def test_search_finds_an_article_for_a_reader_only(
    world: World, graph_provider: GraphProviderProtocol,
) -> None:
    art = world.art("u1")
    rec = await wait_until_record_indexing_completed(graph_provider, world.connector_id, art.number, timeout=600)
    oracle = await world.oracle({art.number})
    reader, outsider = world.accounts["plain"], world.accounts["hr"]
    assert art.number in oracle["plain"] and art.number not in oracle["hr"]

    async def _hit() -> bool:
        resp = await asyncio.to_thread(search_connector_as, reader, world.connector_id, art.token)
        return found(resp, rec.virtual_record_id)
    await poll(_hit, f"search as plain should find {art.number}", 300)
    resp = await asyncio.to_thread(search_connector_as, outsider, world.connector_id, art.token)
    assert resp.status_code in (200, 403, 404) and not found(resp, rec.virtual_record_id), "hr found an article it may not read"


# -- order 2: one round of changes, one incremental sync ------------------------------------------


def _mutate(s: ServiceNowSeed, sid: dict[str, str]) -> None:
    """Round 1: one change per TC id, all at once."""
    s.add_member(s.groups["gm-add"], sid["plain"])                                   # PERM-27
    s.remove_member(s.groups["gm-remove"], sid["hr"])                                # PERM-28
    s.rest.patch("sys_user_group", s.groups["gm-deact"], {"active": "false"})        # PERM-32
    s.rest.patch("sys_user", s.users["move"].sys_id, {"department": s.departments["dept-b"]})  # PERM-26
    s.revoke_role(s.users["role"].sys_id, s.roles["m-reader"])                       # PERM-29
    s.rest.patch("sys_user", s.users["deact"].sys_id, {"active": "false"})           # PERM-30
    s.rest.patch("user_criteria", s.criteria["uc-m-edit"], {"group": s.groups["gm-edit"]})  # PERM-23
    s.rest.patch("user_criteria", s.criteria["uc-m-inactive"], {"active": "false"})  # PERM-24
    s.rest.delete("user_criteria", s.criteria["uc-m-delete"])                        # PERM-25
    s.unlink(s.kbs["m-replace"], "read", s.criteria["uc-m-replace-old"])             # PERM-22
    s.link(s.kbs["m-replace"], "read", s.criteria["uc-m-replace-new"])
    s.link(s.kbs["m-add"], "read", s.criteria["uc-m-kb-add"])                        # PERM-21
    s.checkout(s.articles["m-art-1"], can_read_user_criteria="")                      # PERM-20



@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def round1(world: World, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol) -> dict[str, Any]:
    s, sid = world.seed, {n: p.sys_id for n, p in world.personas.items()}
    before = {k: await record(graph_provider, world.connector_id, world.art(k).number) for k in ("m-add-1",)}

    await asyncio.to_thread(_mutate, s, sid)
    await sync(pipeshub_client, graph_provider, world.connector_id)
    return {"before": before}


@pytest.mark.order(2)
@pytest.mark.parametrize("kb_key,gains,loses", [
    ("m-add-member", "plain", None),          # TC-PERM-27 user added to group
    ("m-remove-member", None, "hr"),          # TC-PERM-28 user removed from group
    ("m-group-deact", None, "eng"),           # TC-PERM-32 group deactivated
    ("m-dept", None, "phit-move"),            # TC-PERM-26 user moves department
    ("m-role", None, "phit-role"),            # TC-PERM-29 role revoked
    ("m-user-deact", None, "phit-deact"),     # TC-PERM-30 user deactivated
    ("m-edit", "eng2", None),                 # TC-PERM-23 criterion edited (a group added)
    ("m-inactive", None, "hr"),               # TC-PERM-24 criterion deactivated
    ("m-delete", None, "hr"),                 # TC-PERM-25 criterion deleted
    ("m-replace", "eng", "plain"),            # TC-PERM-22 criterion replaced
    ("m-add", "hr", None),                    # TC-PERM-21 criterion added
    ("m-art", "hr", None),                    # TC-PERM-20 article criterion removed (now inherits the KB)
])
async def test_tc_perm_20_to_30_32_a_change_at_servicenow_reaches_pipeshub_on_incremental_sync(
    world: World, round1: dict[str, Any], graph_provider: GraphProviderProtocol,
    kb_key: str, gains: str | None, loses: str | None,
) -> None:
    numbers = world.kb_numbers(kb_key)
    oracle = await world.check(graph_provider, numbers, f"after the change on {kb_key}")
    number = next(iter(numbers))
    readers = _readers(oracle, number)
    if gains:
        assert gains in readers, f"ServiceNow should now let {gains} read {kb_key}"
    if loses:
        assert loses not in readers, f"ServiceNow should no longer let {loses} read {kb_key}"


@pytest.mark.order(2)
async def test_tc_perm_36_permission_only_change_does_not_reindex(
    world: World, round1: dict[str, Any], graph_provider: GraphProviderProtocol,
) -> None:
    before = round1["before"]["m-add-1"]
    now = await record(graph_provider, world.connector_id, world.art("m-add-1").number)
    assert before is not None and now is not None
    assert now.id == before.id and now.external_revision_id == before.external_revision_id, (
        "adding a Can Read criterion changed the article's revision, so it was re-indexed")


# -- order 3: a user comes back ------------------------------------------------------------------


@pytest.mark.order(3)
async def test_tc_perm_31_reactivated_user_gets_access_back(
    world: World, round1: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> None:
    await asyncio.to_thread(world.seed.rest.patch, "sys_user", world.seed.users["deact"].sys_id,
                            {"active": "true", "locked_out": "false"})  # deactivating also locks the user out
    await sync(pipeshub_client, graph_provider, world.connector_id)
    oracle = await world.check(graph_provider, world.kb_numbers("m-user-deact"), "re-activated user")
    assert "phit-deact" in _readers(oracle, world.art("m-user-deact-1").number)



# -- TC-NOTIF-05/07: one run summary names the scripted criterion, once ----------------------------


def _script_summaries(client: PipeshubClient, world: World) -> list[dict[str, Any]]:
    title = f"{connector_title(client, world.connector_id)}: sync finished with problems"
    # "1 user criterion uses a script, which ..." / "2 user criteria use a script, which ..."
    return [n for n in notices(client, title)
            if n.get("title") == title and "a script, which PipesHub does not run" in str(n.get("message"))]


@pytest.mark.order(1)
async def test_tc_notif_05_07_scripted_criterion_is_in_the_run_summary(world: World,
                                                                       pipeshub_client: PipeshubClient) -> None:
    async def _sent() -> list:
        return _script_summaries(pipeshub_client, world)
    sent = await poll(_sent, "a 'sync finished with problems' notice naming the scripted criterion", 180)
    assert len(sent) == 1, f"{len(sent)} summaries name the scripted criterion"


@pytest.mark.order(3)
async def test_tc_notif_05_scripted_criterion_is_not_repeated_by_later_runs(
    world: World, round1: dict[str, Any], pipeshub_client: PipeshubClient,
) -> None:
    """Two more syncs ran (round 1, re-activation): the unchanged scripted criterion is not reported again."""
    sent = _script_summaries(pipeshub_client, world)
    assert len(sent) == 1, f"the unchanged scripted criterion was reported {len(sent)} times"
