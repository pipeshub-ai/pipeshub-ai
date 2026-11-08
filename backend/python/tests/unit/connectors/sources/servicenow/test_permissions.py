"""ServiceNow permissions: every user reads exactly the knowledge articles ServiceNow lets them read.

A whole sync (``run_sync``) runs against ``FakeServiceNow`` with users, groups, roles, criteria and
knowledge bases. What it writes (app users, group members, grants, inheritance, access rules) is kept
by ``Graph``, which answers "who can read this record" with the platform's rule: a user passes the
connector gate as an app user; a grant is a USER (by email), GROUP (by membership) or ORG grant; OPEN
is "inherits from a readable parent, or granted", RESTRICTED is "inherits from a readable parent and
granted". The expected readers come from the rule verified on a Zurich instance:

    readers = PRIV ∪ OG ∪ CONTRIB ∪ ((KR ∩ AR) − AD)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from servicenow_fakes import (
    FakeRecordStore,
    FakeServiceNow,
    MemorySyncPoint,
    migrated_sync_point,
    status_error,
)

from app.config.constants.arangodb import AccessRule, Connectors
from app.connectors.sources.servicenow.servicenow.constants import ServiceNowDefaults
from app.models.entities import AppUser, AppUserGroup, User
from app.models.permission import EntityType
from app.sources.external.servicenow.models import ServiceNowAccessError

T0 = "2024-06-01 00:00:00"
T1 = "2024-06-02 00:00:00"  # after the first run
GUEST = "76f09af6cb1200108ad442fcf7076dbf"
GUEST_SCRIPT = 'answer = user_id == GlideUser.getUser("guest").getID();'  # the out-of-box text
# The default of user_criteria.script is a comment that sets no answer (stand-in text: not recorded verbatim).
TEMPLATE = ("// Set the variable 'answer' to true or false to decide whether the user matches.\n"
            "/* 'user_id' holds the sys_id of the user being checked. */\n")


# --- the fake instance -----------------------------------------------------------------------


class Instance:
    """ServiceNow tables as plain rows; ``tables()`` adds the dot-walked fields the connector queries."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.groups: dict[str, dict] = {}
        self.members: list[tuple[str, str]] = []  # (user, group)
        self.roles: dict[str, str] = {}  # sys_id -> name
        self.has_role: list[dict] = []
        self.contains: list[tuple[str, str]] = []  # (container role, contained role)
        self.criteria: dict[str, dict] = {}
        self.kbs: dict[str, dict] = {}
        self.mtom: dict[str, list[tuple[str, str]]] = {t: [] for t in (
            "kb_uc_can_read_mtom", "kb_uc_cannot_read_mtom", "kb_uc_can_contribute_mtom", "kb_uc_cannot_contribute_mtom")}
        self.articles: dict[str, dict] = {}
        self.attachments: list[dict] = []
        self.departments: dict[str, str] = {}
        self.hidden: set[str] = set()  # sys_ids an ACL hides from the integration user
        self.properties: dict[str, str] = {}  # sys_properties name -> value; read at the start of every run
        self.fake = FakeServiceNow({})

    def user(self, name: str, *, active: bool = True, email: bool = True, **fields: str) -> str:
        self.users[name] = {"sys_id": name, "user_name": name, "email": f"{name}@x.com" if email else "",
                            "first_name": name, "last_name": "", "title": "", "active": str(active).lower(),
                            "department": "", "location": "", "company": "", "sys_created_on": T0,
                            "sys_updated_on": T0, **fields}
        return name

    def group(self, sys_id: str, *members: str, parent: str = "", active: bool = True, manager: str = "") -> str:
        self.groups[sys_id] = {"sys_id": sys_id, "name": sys_id.upper(), "description": "", "parent": parent,
                               "manager": manager, "active": str(active).lower(), "sys_updated_on": T0}
        self.members += [(m, sys_id) for m in members]
        return sys_id

    def role(self, name: str, *holders: str, inherited: tuple[str, ...] = ()) -> str:
        self.roles[f"r-{name}"] = name
        self.has_role += [{"user": h, "role": f"r-{name}", "inherited": "false"} for h in holders]
        self.has_role += [{"user": h, "role": f"r-{name}", "inherited": "true"} for h in inherited]
        return f"r-{name}"

    def crit(self, sys_id: str, **fields: str) -> str:
        self.criteria[sys_id] = {"sys_id": sys_id, "active": "true", "match_all": "false", "advanced": "false",
                                 "script": TEMPLATE, "sys_updated_on": T0, **fields}
        return sys_id

    def kb(self, sys_id: str, *, can: tuple = (), cannot: tuple = (), contribute: tuple = (),
           cannot_contribute: tuple = (), owner: str = "owner", managers: str = "kbmgr", active: bool = True) -> str:
        self.kbs[sys_id] = {"sys_id": sys_id, "title": sys_id, "description": "", "owner": owner, "kb_managers": managers,
                            "active": str(active).lower(), "sys_created_on": T0, "sys_updated_on": T0}
        for table, ids in (("kb_uc_can_read_mtom", can), ("kb_uc_cannot_read_mtom", cannot),
                           ("kb_uc_can_contribute_mtom", contribute), ("kb_uc_cannot_contribute_mtom", cannot_contribute)):
            self.mtom[table] = [r for r in self.mtom[table] if r[0] != sys_id] + [(sys_id, c) for c in ids]
        return sys_id

    def article(self, number: str, kb: str, updated: str = T0, **fields: str) -> str:
        self.articles[number] = {"sys_id": f"s-{number}", "number": number, "short_description": number,
                                 "text": "<p>x</p>", "author": "author", "kb_knowledge_base": kb, "kb_category": "",
                                 "workflow_state": "published", "active": "true", "valid_to": "",
                                 "can_read_user_criteria": "", "cannot_read_user_criteria": "", "ownership_group": "",
                                 "sys_created_on": T0, "sys_updated_on": updated, **fields}
        return number

    def tables(self) -> dict[str, list[dict[str, Any]]]:
        def hide(rows: list[dict]) -> list[dict]:
            return [{**r, "_acl_hidden": r["sys_id"] in self.hidden} for r in rows]

        user_active = {u: r["active"] for u, r in self.users.items()}
        n = iter(range(10**6))
        return {
            "sys_user": hide(list(self.users.values())),
            "sys_user_group": hide(list(self.groups.values())),
            "sys_user_grmember": hide([
                {"sys_id": f"m{next(n):05d}", "user": u, "group": g, "user.active": user_active.get(u, "true"),
                 "group.active": self.groups[g]["active"], "sys_updated_on": T0} for u, g in self.members]),
            "sys_user_role": hide([{"sys_id": i, "name": nm, "description": "", "sys_updated_on": T0}
                                   for i, nm in self.roles.items()]),
            "sys_user_has_role": hide([
                {"sys_id": f"h{next(n):05d}", "state": "active", "user.active": user_active.get(r["user"], "true"),
                 "role.name": self.roles[r["role"]], "sys_updated_on": T0, **r} for r in self.has_role]),
            "sys_user_role_contains": [{"sys_id": f"c{next(n):05d}", "role": a, "contains": b, "sys_updated_on": T0}
                                       for a, b in self.contains],
            **{table: [{"sys_id": v, "name": self.departments.get(v, v), "parent": "", "company": "", "sys_updated_on": T0}
                       for v in sorted({u[f] for u in self.users.values() if u[f]} | (set(self.departments) if f == "department" else set()))]
               for table, f in (("cmn_department", "department"), ("cmn_location", "location"), ("core_company", "company"))},
            "user_criteria": hide(list(self.criteria.values())),
            "kb_knowledge_base": hide(list(self.kbs.values())),
            **{t: hide([{"sys_id": f"{t[6:9]}{next(n):05d}", "kb_knowledge_base": k, "user_criteria": c,
                         "sys_updated_on": T0} for k, c in rows]) for t, rows in self.mtom.items()},
            "kb_knowledge": [{**a, "kb_knowledge_base.active": self.kbs[a["kb_knowledge_base"]]["active"]}
                             for a in self.articles.values()],
            "sys_attachment": self.attachments,
            "sys_properties": [{"sys_id": f"p-{name}", "name": name, "value": value, "sys_updated_on": T0}
                               for name, value in self.properties.items()],
        }


class Store(FakeRecordStore):
    """FakeRecordStore whose writes add to the stored grants unless the record is a rewrite, as the
    processor does in an incremental sync."""

    async def on_new_records(self, pairs: list[tuple[Any, list]]) -> None:
        for record, permissions in pairs:
            kept = [] if record.rewrite_permissions else self.permissions.get(record.external_record_id, [])
            await super().on_new_records([(record, [*kept, *permissions])])


class Graph:
    """What a sync wrote, and who it lets read a record."""

    def __init__(self, processor: MagicMock) -> None:
        self.app_users: dict[str, AppUser] = {}
        self.withdrawn: set[str] = set()
        self.group_nodes: dict[str, AppUserGroup] = {}
        self.group_members: dict[str, set[str]] = {}
        self.group_grants: dict[str, list] = {}
        self.group_parent: dict[str, str | None] = {}
        self.store = Store().attach(processor)
        p = processor
        p.on_new_app_users = AsyncMock(side_effect=self._users)
        p.get_all_app_users = AsyncMock(side_effect=lambda connector_id, **_: [
            u for s, u in self.app_users.items() if s not in self.withdrawn])
        # As the platform answers it (User.from_arango_user): no source_user_id.
        p.get_user_by_source_id = AsyncMock(side_effect=lambda source_user_id=None, connector_id=None, *a: (
            None if source_user_id in self.withdrawn or source_user_id not in self.app_users
            else User.from_arango_user({"_key": f"node-{source_user_id}", "email": self.app_users[source_user_id].email})))
        p.withdraw_app_user_access = AsyncMock(side_effect=lambda c, users: self.withdrawn.update(
            u.source_user_id for u in users))
        p.batch_upsert_user_groups = AsyncMock(side_effect=lambda groups: self.group_nodes.update(
            {g.source_user_group_id: g for g in groups}))
        p.on_new_user_groups = AsyncMock(side_effect=self._user_groups)
        p.get_all_user_groups = AsyncMock(side_effect=lambda connector_id, **_: list(self.group_nodes.values()))
        p.on_user_group_deleted = AsyncMock(side_effect=self._group_deleted)
        p.on_new_record_groups = AsyncMock(side_effect=self._record_groups)
        p.note_kept_record_group = AsyncMock()

    async def _users(self, users: list[AppUser]) -> None:
        for u in users:
            u.id = f"node-{u.source_user_id}"
            self.app_users[u.source_user_id] = u
            self.withdrawn.discard(u.source_user_id)

    async def _user_groups(self, pairs: list) -> None:
        for group, members in pairs:
            self.group_nodes[group.source_user_group_id] = group
            self.group_members[group.source_user_group_id] = {m.email for m in members}

    async def _group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        self.group_nodes.pop(external_group_id, None)
        self.group_members.pop(external_group_id, None)
        return True

    async def _record_groups(self, pairs: list) -> None:
        for group, permissions in pairs:
            self.group_grants[group.external_group_id] = permissions
            self.group_parent[group.external_group_id] = group.parent_external_group_id

    def _granted(self, user: str, permissions: list) -> bool:
        email = f"{user}@x.com"
        return any(
            (p.entity_type == EntityType.USER and p.email == email)
            or (p.entity_type == EntityType.GROUP and email in self.group_members.get(p.external_id, set()))
            or p.entity_type == EntityType.ORG
            for p in permissions
        )

    def _gate(self, user: str) -> bool:
        return user in self.app_users and user not in self.withdrawn

    def reads_group(self, user: str, group_id: str) -> bool:
        if not self._gate(user) or group_id not in self.group_grants:
            return False
        parent = self.group_parent.get(group_id)
        return self.reads_group(user, parent) if parent else self._granted(user, self.group_grants[group_id])

    def reads(self, user: str, key: str) -> bool:
        record = self.store.records.get(key)
        if record is None or not self._gate(user):
            return False
        granted = self._granted(user, self.store.permissions.get(key, []))
        parent_readable = (self.reads(user, record.parent_external_record_id) if record.parent_external_record_id
                           else self.reads_group(user, record.external_record_group_id))
        inherits = bool(record.inherit_permissions) and parent_readable
        if record.access_rule == AccessRule.RESTRICTED:
            return inherits and granted
        return inherits or granted

    def readers(self, key: str) -> set[str]:
        return {u for u in self.app_users if self.reads(u, key)}

    def kb_readers(self, kb: str) -> set[str]:
        return {u for u in self.app_users if self.reads_group(u, kb)}


PRIV = {"owner", "kbmgr", "kadmin", "admin"}
EVERYONE = {"hr", "eng", "eng2", "plain", "contrib", "owner", "kbmgr", "kadmin", "admin", "kmanager", "author", "knowledge"}


def _people(inst: Instance) -> None:
    """The personas of the instance's test fixture."""
    for name in sorted(EVERYONE):
        inst.user(name)
    inst.group("g-hr", "hr")
    inst.group("g-eng", "eng2")
    inst.group("g-eng-platform", "eng", parent="g-eng")
    inst.role("admin", "admin")
    inst.role("knowledge_admin", "kadmin")
    inst.role("knowledge_manager", "kmanager", "owner", "kbmgr")
    inst.role("knowledge", "knowledge")


def _connector(sc, inst: Instance, *, apply_article_criteria: bool = False, block: bool = True) -> Graph:
    graph = Graph(sc.data_entities_processor)
    sc.servicenow_client = MagicMock()
    sc._register_authenticated_identity = AsyncMock()
    sc._get_fresh_datasource = AsyncMock(return_value=inst.fake)
    sc.instance_url = "https://acme.service-now.com"
    sc.user_sync_point = MemorySyncPoint()
    sc.org_entity_sync_points = {k: MemorySyncPoint() for k in sc.org_entity_sync_points}
    sc.article_sync_point = migrated_sync_point()
    inst.properties["glide.knowman.apply_article_read_criteria"] = str(apply_article_criteria).lower()
    inst.properties["glide.knowman.block_access_with_no_user_criteria"] = str(block).lower()
    return graph


async def _sync(sc, inst: Instance) -> None:
    inst.fake.tables = inst.tables()
    await sc.run_sync()


def _incremental(sc) -> None:
    """The next run reads deltas from T0 on (users, articles); the 120 s overlap reaches back before T0."""
    sc.user_sync_point.data["users"] = {"last_sync_time": T0}
    sc.article_sync_point.data["articles"] = {"last_sync_time": T0}


# --- the truth table (under both values of apply_article_read_criteria) ---


def _kb_art(inst: Instance) -> None:
    _people(inst)
    inst.kb("kb-art", can=(inst.crit("c-hr", group="g-hr"), inst.crit("c-eng", group="g-eng")),
            contribute=(inst.crit("c-contrib", user="contrib"),))
    inst.article("KB-plain", "kb-art")
    inst.article("KB-read-eng", "kb-art", can_read_user_criteria=inst.crit("c-u-eng", user="eng"))
    inst.article("KB-read-plain", "kb-art", can_read_user_criteria=inst.crit("c-u-plain", user="plain"))
    inst.article("KB-cannot-hr", "kb-art", cannot_read_user_criteria=inst.crit("c-u-hr", user="hr"))
    inst.article("KB-cannot-contrib", "kb-art", cannot_read_user_criteria="c-contrib")
    inst.attachments.append({"sys_id": "att-1", "file_name": "f.pdf", "content_type": "application/pdf",
                             "size_bytes": "1", "table_name": "kb_knowledge", "table_sys_id": "s-KB-read-eng",
                             "sys_created_on": T0, "sys_updated_on": T0})


TRUTH = {
    False: {
        "KB-plain": {"hr", "eng", "eng2", "contrib"},
        "KB-read-eng": {"eng", "contrib"},
        "KB-read-plain": {"contrib"},
        "KB-cannot-hr": {"eng", "eng2", "contrib"},
        "KB-cannot-contrib": {"hr", "eng", "eng2", "contrib"},
    },
    True: {
        "KB-plain": {"hr", "eng", "eng2", "contrib"},
        "KB-read-eng": {"eng"},
        "KB-read-plain": set(),
        "KB-cannot-hr": {"eng", "eng2", "contrib"},
        "KB-cannot-contrib": {"hr", "eng", "eng2"},
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("apply_article_criteria", [False, True])
async def test_article_criteria_truth_table(servicenow_connector, apply_article_criteria) -> None:
    # T1, T2, T4, T7, T8, T10 (eng reads through the child group), T12 (admin, knowledge_admin)
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst, apply_article_criteria=apply_article_criteria)

    await _sync(servicenow_connector, inst)

    for number, readers in TRUTH[apply_article_criteria].items():
        assert graph.readers(number) == readers | PRIV, number
    # an attachment has its article's readers
    assert graph.readers("KB-read-eng/f.pdf/1") == graph.readers("KB-read-eng")


@pytest.mark.asyncio
@pytest.mark.parametrize("apply_article_criteria", [False, True])
async def test_truth_table_holds_on_the_incremental_run_too(servicenow_connector, apply_article_criteria) -> None:
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst, apply_article_criteria=apply_article_criteria)
    await _sync(servicenow_connector, inst)
    _incremental(servicenow_connector)

    await _sync(servicenow_connector, inst)

    for number, readers in TRUTH[apply_article_criteria].items():
        assert graph.readers(number) == readers | PRIV, number


@pytest.mark.asyncio
async def test_article_can_read_never_widens_kb_audience(servicenow_connector) -> None:
    # plain is named by the article's Can Read but is no KB reader: ServiceNow shows it to nobody but PRIV.
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst, apply_article_criteria=True)
    await _sync(servicenow_connector, inst)
    assert "plain" not in graph.readers("KB-read-plain")
    assert "hr" not in graph.readers("KB-read-eng")
    assert graph.store.records["KB-read-eng"].access_rule == AccessRule.RESTRICTED


@pytest.mark.asyncio
async def test_restricted_article_keeps_privileged_readers(servicenow_connector) -> None:
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst, apply_article_criteria=True)
    await _sync(servicenow_connector, inst)
    assert PRIV <= graph.readers("KB-read-plain")


# --- T1, T2: who reads without criteria, and who does not --------------------------------------


@pytest.mark.asyncio
async def test_knowledge_admin_reads_all_kbs_and_knowledge_manager_role_alone_grants_nothing(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb-none")  # no criteria (block=true): T6
    inst.kb("kb-other", can=(inst.crit("c-hr", group="g-hr"),), owner="hr", managers="")
    inst.article("KB-1", "kb-none")
    # Articles with criteria of their own carry the admins as user grants (_kb_privileged_users).
    inst.article("KB-own-read", "kb-other", can_read_user_criteria=inst.crit("c-u-plain", user="plain"))
    inst.article("KB-own-deny", "kb-other", cannot_read_user_criteria=inst.crit("c-u-kadmin", user="kadmin,admin"))
    graph = _connector(servicenow_connector, inst)

    await _sync(servicenow_connector, inst)

    assert graph.kb_readers("kb-none") == PRIV
    assert graph.readers("KB-1") == PRIV
    assert {"kadmin", "admin"} <= graph.kb_readers("kb-other")
    assert {"kadmin", "admin"} <= graph.readers("KB-own-read") & graph.readers("KB-own-deny")
    assert "kmanager" not in graph.kb_readers("kb-other") | graph.readers("KB-1")


@pytest.mark.asyncio
async def test_author_without_criteria_match_has_no_access(servicenow_connector) -> None:
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert all("author" not in graph.readers(n) for n in TRUTH[False])


@pytest.mark.asyncio
async def test_author_in_cannot_read_has_no_access(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.group("g-all", "author", "hr")
    inst.kb("kb", can=(inst.crit("c-all", group="g-all"),))
    inst.article("KB-1", "kb", cannot_read_user_criteria=inst.crit("c-author", user="author"))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"hr"} | PRIV


@pytest.mark.asyncio
async def test_kb_manager_can_read_kb(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),), managers="kbmgr,eng")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert {"kbmgr", "eng"} <= graph.kb_readers("kb")


# --- T3: ownership group ---------------------------------------------------------------------


def _owned(inst: Instance, **article: str) -> Graph | None:
    _people(inst)
    inst.user("og1")
    inst.user("ogmgr")
    inst.group("g-own", "og1", manager="ogmgr", active=article.pop("og_active", "true") == "true")
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB-1", "kb", ownership_group="g-own", **article)
    return None


@pytest.mark.asyncio
async def test_ownership_group_member_and_manager_read_article_not_kb(servicenow_connector) -> None:
    inst = Instance()
    _owned(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"hr", "og1", "ogmgr"} | PRIV
    assert not {"og1", "ogmgr"} & graph.kb_readers("kb")


@pytest.mark.asyncio
async def test_ownership_group_with_article_can_read_uses_explicit_path(servicenow_connector) -> None:
    inst = Instance()
    _owned(inst, can_read_user_criteria="c-nobody")
    inst.crit("c-nobody", user="plain")  # plain is no KB reader: only PRIV and the ownership group read it
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.store.records["KB-1"].inherit_permissions is False
    assert graph.readers("KB-1") == {"og1", "ogmgr"} | PRIV


@pytest.mark.asyncio
async def test_inactive_ownership_group_grants_nothing(servicenow_connector) -> None:
    inst = Instance()
    _owned(inst, og_active="false")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"hr"} | PRIV


# --- T4, T5: contributors and KB Cannot Read ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("apply_article_criteria", [False, True])
async def test_kb_cannot_read_does_not_deny_contributor(servicenow_connector, apply_article_criteria) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", contribute=(inst.crit("c-contrib", user="contrib"),), cannot=("c-contrib",))
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst, apply_article_criteria=apply_article_criteria)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"contrib"} | PRIV


@pytest.mark.asyncio
async def test_kb_cannot_read_denies_reader_named_in_article_can_read(servicenow_connector) -> None:
    # The article's direct grant must not let a KB-denied contractor in.
    inst = Instance()
    _people(inst)
    inst.group("g-emp", "hr", "eng", "plain")
    inst.kb("kb", can=(inst.crit("c-emp", group="g-emp"),), cannot=(inst.crit("c-ctr", user="plain"),))
    inst.article("KB-1", "kb", can_read_user_criteria=inst.crit("c-art", user="eng,plain"))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"eng"} | PRIV


@pytest.mark.asyncio
async def test_contributor_in_explicit_path_when_property_false(servicenow_connector) -> None:
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "contrib" in graph.readers("KB-cannot-hr")


@pytest.mark.asyncio
async def test_contributor_subject_to_article_cannot_read_when_property_true(servicenow_connector) -> None:
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst, apply_article_criteria=True)
    await _sync(servicenow_connector, inst)
    assert "contrib" not in graph.readers("KB-cannot-contrib")
    assert "contrib" in graph.readers("KB-plain")  # an inheriting article: still a contributor


@pytest.mark.asyncio
async def test_cannot_contribute_subtracted(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", contribute=(inst.crit("c-two", user="contrib,eng"),), cannot_contribute=(inst.crit("c-eng", user="eng"),))
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"contrib"} | PRIV


# --- T6: a KB with no criteria -------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_property_true_keeps_kb_without_criteria_closed(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb")
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst, block=True)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == PRIV


@pytest.mark.asyncio
async def test_block_property_false_opens_kb_without_criteria(servicenow_connector) -> None:
    # Documented behaviour; not seen on an instance with the property false.
    inst = Instance()
    _people(inst)
    inst.kb("kb-none")
    inst.kb("kb-contrib-only", contribute=(inst.crit("c-contrib", user="contrib"),))
    inst.article("KB-1", "kb-none")
    inst.article("KB-2", "kb-contrib-only")
    graph = _connector(servicenow_connector, inst, block=False)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == EVERYONE
    assert graph.readers("KB-2") == {"contrib"} | PRIV  # Can Contribute counts as criteria: stays closed (grants less)


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "blocked"), [("true", True), ("false", False), (None, True), ("", True)])
async def test_block_property_read(servicenow_connector, value, blocked) -> None:
    servicenow_connector._read_instance_property = AsyncMock(return_value=value)
    assert await servicenow_connector._resolve_block_no_criteria() is blocked
    servicenow_connector._read_instance_property = AsyncMock(side_effect=status_error(403))
    assert await servicenow_connector._resolve_block_no_criteria() is True


# --- T9-T16: matching one criterion -----------------------------------------------------------------


async def _readers_of(sc, inst: Instance, **criterion: str) -> set[str]:
    inst.kb("kb", can=(inst.crit("c", **criterion),), owner="", managers="")
    inst.article("KB-1", "kb")
    graph = _connector(sc, inst)
    await _sync(sc, inst)
    return graph.readers("KB-1") - {"kadmin", "admin"}


def _dim(inst: Instance) -> None:
    _people(inst)
    inst.departments.update({"d-eng": "Eng", "d-eng-child": "Eng child"})
    inst.users["eng2"].update(department="d-eng", location="l-pune", company="co")
    inst.users["eng"].update(department="d-eng-child", location="l-pune-child", company="co-sub")
    inst.users["hr"].update(company="co")


@pytest.mark.asyncio
@pytest.mark.parametrize(("criterion", "readers"), [
    ({"user": "plain"}, {"plain"}),
    ({"group": "g-eng"}, {"eng", "eng2"}),  # T10: child group members
    ({"department": "d-eng"}, {"eng2"}),  # T11: exact
    ({"location": "l-pune"}, {"eng2"}),
    ({"company": "co"}, {"hr", "eng2"}),
    ({"group": "g-eng", "location": "l-pune"}, {"eng", "eng2"}),  # T9 Match Any
    ({"group": "g-eng", "location": "l-pune", "match_all": "true"}, {"eng2"}),  # T9 Match All
    ({"user": "plain,hr", "match_all": "true"}, {"plain", "hr"}),  # values in one field are OR'd
    ({"user": "plain", "active": "false"}, set()),  # T15
    ({}, EVERYONE - {"kadmin", "admin"}),  # T13: no field, no script
    ({"advanced": "true"}, EVERYONE - {"kadmin", "admin"}),  # template script only: still everyone
    ({"user": "plain", "advanced": "true", "match_all": "true"}, {"plain"}),  # template script is ignored
    ({"user": "plain", "advanced": "false", "script": "answer = true;"}, {"plain"}),  # T14: fields only, grants less
    ({"user": "plain", "advanced": "true", "script": "answer = true;", "match_all": "true"}, set()),
    ({"advanced": "true", "script": "answer = gs.hasRole('x');"}, set()),  # scripted, no field: unevaluable
])
async def test_criterion_matching(servicenow_connector, criterion, readers) -> None:
    inst = Instance()
    _dim(inst)
    assert await _readers_of(servicenow_connector, inst, **criterion) == readers


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["department", "location", "company"])
async def test_org_criterion_excludes_child_entity(servicenow_connector, dimension) -> None:
    inst = Instance()
    _dim(inst)
    value = {"department": "d-eng", "location": "l-pune", "company": "co"}[dimension]
    assert "eng" not in await _readers_of(servicenow_connector, inst, **{dimension: value})


@pytest.mark.asyncio
async def test_criteria_role_matches_inherited_assignment(servicenow_connector) -> None:
    # T12: a role from a group or a containing role arrives as an inherited sys_user_has_role row.
    inst = Instance()
    _people(inst)
    inst.role("reader", "plain", inherited=("hr", "eng"))
    assert await _readers_of(servicenow_connector, inst, role="r-reader") == {"plain", "hr", "eng"}


@pytest.mark.asyncio
async def test_role_with_two_parents_keeps_both(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.role("reader")
    inst.role("container-a", "hr")
    inst.role("container-b", "eng")
    inst.contains += [("r-container-a", "r-reader"), ("r-container-b", "r-reader")]
    assert await _readers_of(servicenow_connector, inst, role="r-reader") == {"hr", "eng"}


@pytest.mark.asyncio
async def test_inactive_role_assignment_ignored(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.role("reader", "plain", "hr")
    inst.has_role[-1]["state"] = "inactive"
    assert await _readers_of(servicenow_connector, inst, role="r-reader") == {"plain"}


@pytest.mark.asyncio
async def test_inactive_group_criterion_matches_nobody(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.user("dormant")
    inst.group("g-dormant", "dormant", active=False)
    assert await _readers_of(servicenow_connector, inst, group="g-dormant") == set()


@pytest.mark.asyncio
async def test_inactive_child_group_not_flattened(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.group("g-child", "plain", parent="g-hr", active=False)
    assert await _readers_of(servicenow_connector, inst, group="g-hr") == {"hr"}


# --- T13, T14, T17: the defaults every new KB gets ------------------------------------------------


def _defaults_kb(inst: Instance) -> None:
    _people(inst)
    inst.user("guest", email=False)  # the out-of-box guest user; some instances give it an email
    inst.crit("any-user")  # Can Read "Any User": no field
    inst.crit(GUEST, advanced="true", user="guest", script=GUEST_SCRIPT)
    inst.crit("has-role", advanced="true", script="answer = gs.getUser().getUserByID(user_id).getRoles().size() > 1;")
    inst.kb("kb-defaults", can=("any-user",), cannot=(GUEST,), contribute=("has-role",), cannot_contribute=(GUEST,))
    inst.article("KB-1", "kb-defaults")


@pytest.mark.asyncio
async def test_default_kb_published_readable_by_plain_user(servicenow_connector) -> None:
    inst = Instance()
    _defaults_kb(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert {"plain", "hr", "author"} <= graph.readers("KB-1")


@pytest.mark.asyncio
async def test_default_kb_open_to_org(servicenow_connector) -> None:
    inst = Instance()
    _defaults_kb(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    read = [p for p in graph.group_grants["kb-defaults"] if p.type.value == "READER"]
    assert [p.entity_type for p in read if p.entity_type == EntityType.ORG] == [EntityType.ORG]


@pytest.mark.asyncio
async def test_guest_user_cannot_read_denies_no_synced_user(servicenow_connector) -> None:
    inst = Instance()
    _defaults_kb(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.kb_readers("kb-defaults") >= EVERYONE


@pytest.mark.asyncio
async def test_edited_guest_criterion_handled_normally(servicenow_connector) -> None:
    inst = Instance()
    _defaults_kb(inst)
    inst.criteria[GUEST]["group"] = "g-hr"  # edited: no longer the out-of-box row, and scripted: denies everyone
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == PRIV


@pytest.mark.asyncio
async def test_empty_cannot_read_denies_everyone(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("any-user"),), cannot=(inst.crit("all-denied"),))
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == PRIV


@pytest.mark.asyncio
async def test_any_user_in_resolved_path_is_all_users(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("any-user"),), cannot=(inst.crit("c-hr", user="hr"),))
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == EVERYONE - {"hr"}


@pytest.mark.asyncio
async def test_match_all_deny_with_script_denies_field_matches_only(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("any-user"),),
            cannot=(inst.crit("c-d", user="hr,eng", match_all="true", script="answer = gs.hasRole('x');"),))
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == EVERYONE - {"hr", "eng"}


@pytest.mark.parametrize(("script", "scripted"), [
    (TEMPLATE, False), ("", False), (None, False), ("  // only a comment\n", False),
    ("answer = true;", True), ("/* x */ answer = user_id == 'a';", True), ("var a = 1;", True),
])
def test_has_script(servicenow_connector, script, scripted) -> None:
    assert servicenow_connector._has_script(SimpleNamespace(script=script)) is scripted


# --- leaks and wrong removals, across full and incremental runs ----------------------------


@pytest.mark.asyncio
async def test_article_restricted_inside_open_kb(servicenow_connector) -> None:
    # leak 1: KB open to Employees, article Can Read = HR: only HR members who are Employees read it.
    inst = Instance()
    _people(inst)
    inst.group("g-emp", "hr", "eng", "plain")
    inst.kb("kb", can=(inst.crit("c-emp", group="g-emp"),))
    inst.article("KB-1", "kb", can_read_user_criteria=inst.crit("c-hr", group="g-hr"))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"hr"} | PRIV


@pytest.mark.asyncio
async def test_department_move_removes_old_org_membership(servicenow_connector) -> None:
    # leak 3: Alice moves from HR to Sales; the incremental run takes HR away.
    inst = Instance()
    _people(inst)
    inst.departments.update({"d-hr": "HR", "d-sales": "Sales"})
    inst.users["hr"]["department"] = "d-hr"
    inst.kb("kb", can=(inst.crit("c-dept", department="d-hr"),))
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"hr"} | PRIV
    _incremental(servicenow_connector)

    inst.users["hr"].update(department="d-sales", sys_updated_on=T1)
    await _sync(servicenow_connector, inst)

    assert graph.readers("KB-1") == PRIV
    assert graph.group_members["d-sales"] == {"hr@x.com"}


@pytest.mark.asyncio
async def test_removed_article_criterion_removes_grant_on_incremental(servicenow_connector) -> None:
    # leak 4: Can Read changes from Finance to Finance-Leads; Finance loses it on the next run.
    inst = Instance()
    _people(inst)
    inst.group("g-fin", "hr", "eng")
    inst.group("g-leads", "hr")
    inst.group("g-all", "hr", "eng", "plain")
    inst.kb("kb", can=(inst.crit("c-all", group="g-all"),))
    inst.article("KB-1", "kb", can_read_user_criteria=inst.crit("c-fin", group="g-fin"))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"hr", "eng"} | PRIV
    _incremental(servicenow_connector)

    inst.article("KB-1", "kb", T1, can_read_user_criteria=inst.crit("c-leads", group="g-leads"))
    await _sync(servicenow_connector, inst)

    assert graph.readers("KB-1") == {"hr"} | PRIV


@pytest.mark.asyncio
async def test_moved_article_drops_old_direct_grants(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.user("ogmember")
    inst.group("g-own", "ogmember")
    inst.kb("kb-a", can=(inst.crit("c-hr", group="g-hr"),))
    inst.kb("kb-b", can=(inst.crit("c-eng", group="g-eng"),), owner="", managers="")
    inst.article("KB-1", "kb-a", ownership_group="g-own")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    _incremental(servicenow_connector)

    inst.article("KB-1", "kb-b", T1)  # moved, and the ownership group dropped
    await _sync(servicenow_connector, inst)

    assert graph.readers("KB-1") == {"eng", "eng2", "kadmin", "admin"}


@pytest.mark.asyncio
async def test_match_all_user_removed_from_group_loses_grant_next_run(servicenow_connector) -> None:
    # leak 5: Carol is resolved by Match All (group AND location); she leaves the group, the article does not change.
    inst = Instance()
    _dim(inst)
    inst.group("g-pune", "eng2", "hr")
    inst.users["hr"]["location"] = "l-pune"
    inst.kb("kb", can=(inst.crit("c-all-users"),))
    inst.article("KB-1", "kb", can_read_user_criteria=inst.crit("c", group="g-pune", location="l-pune", match_all="true"))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"eng2", "hr"} | PRIV
    _incremental(servicenow_connector)

    inst.members.remove(("hr", "g-pune"))
    await _sync(servicenow_connector, inst)

    assert graph.readers("KB-1") == {"eng2"} | PRIV


@pytest.mark.asyncio
async def test_criteria_row_edit_reevaluates_article(servicenow_connector) -> None:
    # A user_criteria row edit changes no article row: the article is re-read anyway.
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-all-users"),))
    inst.article("KB-1", "kb", cannot_read_user_criteria=inst.crit("c-d", user="hr"))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "eng" in graph.readers("KB-1")
    _incremental(servicenow_connector)

    inst.criteria["c-d"].update(user="hr,eng", sys_updated_on=T1)
    await _sync(servicenow_connector, inst)

    assert "eng" not in graph.readers("KB-1") and "hr" not in graph.readers("KB-1")


@pytest.mark.asyncio
async def test_deactivated_user_loses_group_access_and_reactivated_regains(servicenow_connector) -> None:
    # leak 7: Dave is deactivated; his SSO login works, his ServiceNow access is gone.
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),), managers="kbmgr,hr")
    inst.article("KB-1", "kb", cannot_read_user_criteria=inst.crit("c-d", user="plain"))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "hr" in graph.readers("KB-1")
    _incremental(servicenow_connector)

    inst.users["hr"].update(active="false", sys_updated_on=T1)
    await _sync(servicenow_connector, inst)
    assert "hr" not in graph.readers("KB-1") | graph.kb_readers("kb")
    assert "hr@x.com" not in graph.group_members["g-hr"]

    inst.users["hr"].update(active="true", sys_updated_on="2024-06-03 00:00:00")
    servicenow_connector.user_sync_point.data["users"] = {"last_sync_time": T1}
    await _sync(servicenow_connector, inst)
    assert "hr" in graph.readers("KB-1")


@pytest.mark.asyncio
async def test_users_deactivated_before_the_upgrade_lose_access_on_the_first_run(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.users["hr"]["active"] = "false"
    graph = _connector(servicenow_connector, inst)
    graph.app_users["hr"] = AppUser(app_name=Connectors.SERVICENOW, connector_id="c", source_user_id="hr",
                                    email="hr@x.com", full_name="hr")
    servicenow_connector.user_sync_point.data["users"] = {"last_sync_time": T1}  # a stored checkpoint, no users/v2
    inst.kb("kb")
    await _sync(servicenow_connector, inst)
    assert "hr" in graph.withdrawn
    assert servicenow_connector.user_sync_point.data["users/v2"] == {"done": True}


@pytest.mark.asyncio
async def test_missing_deny_row_fails_closed(servicenow_connector) -> None:
    # leak 8: a Cannot Read row hidden from the integration user is not "no deny".
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"), inst.crit("c-eng", group="g-eng")))
    # Read with a row it can see: a request whose rows are all hidden is an unreadable table (below).
    inst.article("KB-1", "kb", cannot_read_user_criteria=f"{inst.crit('c-d', user='hr')},{inst.crit('c-x', user='plain')}")
    inst.hidden.add("c-d")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == PRIV
    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_deleted_deny_row_ignored(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB-1", "kb", cannot_read_user_criteria="c-deleted")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"hr"} | PRIV
    assert servicenow_connector.full_sync_coverage().complete is True


@pytest.mark.asyncio
async def test_partly_hidden_deny_members_fail_closed(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.group("g-ctr", "hr", "eng")
    inst.kb("kb", can=(inst.crit("c-all-users"),), cannot=(inst.crit("c-ctr", group="g-ctr"),))
    inst.article("KB-1", "kb")
    tables = inst.tables()
    tables["sys_user_grmember"] = [{**r, "_acl_hidden": r["user"] == "eng"} for r in tables["sys_user_grmember"]]
    graph = _connector(servicenow_connector, inst)
    inst.fake.tables = tables
    await servicenow_connector.run_sync()
    assert "plain" not in graph.readers("KB-1")  # whom the deny reaches is unknown: everyone it may cover is denied


@pytest.mark.asyncio
async def test_deleted_group_removed(servicenow_connector) -> None:
    # leak 11: a deleted group keeps no node and no members.
    inst = Instance()
    _people(inst)
    inst.group("g-x", "plain")
    inst.kb("kb", can=(inst.crit("c-x", group="g-x"),))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "g-x" in graph.group_nodes

    del inst.groups["g-x"]
    inst.members.remove(("plain", "g-x"))
    await _sync(servicenow_connector, inst)

    assert "g-x" not in graph.group_nodes
    assert "plain" not in graph.kb_readers("kb")


@pytest.mark.asyncio
async def test_inactive_group_grants_nothing_and_reactivated_group_regains_members(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "hr" in graph.kb_readers("kb")
    inst.groups["g-hr"]["active"] = "false"
    await _sync(servicenow_connector, inst)
    assert "g-hr" not in graph.group_members  # not written, and the stored one removed
    assert "hr" not in graph.kb_readers("kb")

    inst.groups["g-hr"]["active"] = "true"
    await _sync(servicenow_connector, inst)
    assert "hr" in graph.kb_readers("kb")


# --- unreadable access tables (non-admin integration user) -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("table", [
    "sys_user_grmember", "sys_user_has_role", "sys_user_group", "sys_user_role", "kb_uc_can_read_mtom",
])
async def test_all_hidden_access_table_is_unreadable_not_empty(servicenow_connector, table) -> None:
    # The instance answers 200 with X-Total-Count > 0 and no rows: nothing may be written as "none".
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst)
    tables = inst.tables()
    tables[table] = [{**r, "_acl_hidden": True} for r in tables[table]]
    inst.fake.tables = tables

    with pytest.raises(ServiceNowAccessError) as raised:
        await servicenow_connector.run_sync()

    assert raised.value.table == table
    assert servicenow_connector.full_sync_coverage().complete is False
    assert not graph.store.records  # no article written with what the run could not read
    if table in ("sys_user_has_role", "kb_uc_can_read_mtom"):
        assert "kb-art" not in graph.group_grants


@pytest.mark.asyncio
async def test_all_hidden_criteria_grant_nothing_and_the_run_completes(servicenow_connector) -> None:
    # Criteria are read by id, so a hidden one is unknown, not a table the account cannot read.
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst)
    tables = inst.tables()
    tables["user_criteria"] = [{**r, "_acl_hidden": True} for r in tables["user_criteria"]]
    inst.fake.tables = tables

    await servicenow_connector.run_sync()

    assert "kb-art" not in graph.group_grants
    assert all(graph.readers(n) == set() for n in TRUTH[False])
    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["kb_uc_cannot_read_mtom", "sys_user_has_role", "user_criteria", "sys_user_grmember"])
async def test_403_on_access_table_stops_the_run_naming_it(servicenow_connector, table) -> None:
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst)
    inst.fake.fail = lambda t, q: status_error(403, "User Not Authorized") if t == table else None

    with pytest.raises(ServiceNowAccessError) as raised:
        await _sync(servicenow_connector, inst)

    assert raised.value.table == table and "User Not Authorized" in raised.value.message
    assert "kb-art" not in graph.group_grants or table == "sys_user_grmember"


@pytest.mark.asyncio
async def test_hidden_kb_in_partial_listing_is_kept_and_deleted_one_removed(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    for kb in ("kb-a", "kb-b", "kb-c"):
        inst.kb(kb)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)

    inst.hidden.add("kb-b")  # still there, this account cannot see it
    del inst.kbs["kb-c"]  # deleted
    await _sync(servicenow_connector, inst)

    assert graph.store.deleted_groups == ["kb-c"]
    assert servicenow_connector.category_sync_point.data["kb_scope"]["ids"] == ["kb-a", "kb-b"]


@pytest.mark.asyncio
async def test_reconcile_with_partial_listing_keeps_hidden_article(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb")
    for n in ("KB-1", "KB-2", "KB-3"):
        inst.article(n, "kb")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.store.keys() == ["KB-1", "KB-2", "KB-3"]

    tables = inst.tables()
    tables["kb_knowledge"] = [{**r, "_acl_hidden": r["number"] == "KB-2"} for r in tables["kb_knowledge"]
                              if r["number"] != "KB-3"]
    inst.fake.tables = tables
    servicenow_connector.article_sync_point.data.pop("articles_reconcile/last")
    await servicenow_connector.run_sync()

    assert graph.store.keys() == ["KB-1", "KB-2"]


@pytest.mark.asyncio
async def test_partially_hidden_members_under_share_only(servicenow_connector) -> None:
    # Some membership rows hidden (some shown): the visible ones are written, nothing is denied wrongly.
    inst = Instance()
    _people(inst)
    inst.group("g-team", "hr", "eng")
    inst.kb("kb", can=(inst.crit("c-team", group="g-team"),))
    tables = inst.tables()
    tables["sys_user_grmember"] = [{**r, "_acl_hidden": r["user"] == "eng"} for r in tables["sys_user_grmember"]]
    graph = _connector(servicenow_connector, inst)
    inst.fake.tables = tables
    await servicenow_connector.run_sync()
    assert graph.kb_readers("kb") == {"hr"} | PRIV


# --- out-of-box criteria are trusted only while unedited ---------------------------------------


@pytest.mark.asyncio
async def test_guest_criterion_with_an_edited_script_denies_everyone(servicenow_connector) -> None:
    # The script is not evaluated here, so whom the edited row denies is unknown: it may deny anyone.
    inst = Instance()
    _defaults_kb(inst)
    inst.criteria[GUEST]["script"] = GUEST_SCRIPT[:-1] + " || gs.getUser().isMemberOf('Contractors');"
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == PRIV
    assert "plain" not in graph.kb_readers("kb-defaults")


@pytest.mark.asyncio
async def test_guest_criterion_naming_a_synced_user_denies_them(servicenow_connector) -> None:
    inst = Instance()
    _defaults_kb(inst)
    inst.criteria[GUEST]["user"] = "plain"
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "plain" not in graph.readers("KB-1") | graph.kb_readers("kb-defaults")


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_email", [True, False])
async def test_unedited_guest_criterion_denies_only_the_guest(servicenow_connector, guest_email) -> None:
    # Its script matches exactly the user named guest, whether that user is synced (has an email) or not.
    inst = Instance()
    _defaults_kb(inst)
    inst.users["guest"]["email"] = "guest@x.com" if guest_email else ""
    inst.kb("kb-main", can=(inst.crit("c-hr", group="g-hr"),), contribute=(inst.crit("c-contrib", user="contrib"),),
            cannot_contribute=(GUEST,))
    inst.article("KB-2", "kb-main")
    inst.article("KB-3", "kb-main", cannot_read_user_criteria=GUEST)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == EVERYONE
    assert "guest" not in graph.kb_readers("kb-defaults") and graph.kb_readers("kb-defaults") >= EVERYONE
    assert graph.readers("KB-2") == graph.readers("KB-3") == {"hr", "contrib"} | PRIV


@pytest.mark.asyncio
async def test_guest_is_not_synced_so_a_default_kb_stays_one_org_grant(servicenow_connector) -> None:
    # Some instances give the anonymous guest an email; nobody signs in to PipesHub as it.
    inst = Instance()
    _defaults_kb(inst)
    inst.users["guest"]["email"] = "guest@x.com"
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "guest" not in graph.app_users
    read = [p for p in graph.group_grants["kb-defaults"] if p.type.value == "READER"]
    assert EntityType.ORG in {p.entity_type for p in read}  # not one grant per user
    assert {p.email for p in read if p.entity_type == EntityType.USER} <= {f"{u}@x.com" for u in PRIV}
    # A guest synced before is withdrawn by the user reconcile
    assert await servicenow_connector._users_gone({"guest", "plain"}) == {"guest"}


@pytest.mark.asyncio
async def test_unedited_guest_criterion_is_not_reported_as_scripted(servicenow_connector) -> None:
    inst = Instance()
    _defaults_kb(inst)
    _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert GUEST not in servicenow_connector._criteria_noted["scripted"]
    assert "has-role" in servicenow_connector._criteria_noted["scripted"]  # a script that is not out-of-box


@pytest.mark.asyncio
async def test_has_role_criterion_with_a_field_added_takes_the_normal_path(servicenow_connector) -> None:
    # Under Match All its script is AND'd with the group and not evaluated here: it grants nobody,
    # not every user with a role.
    inst = Instance()
    _people(inst)
    has_role = inst.crit(ServiceNowDefaults.HAS_ROLE_CRITERIA, advanced="true", group="g-hr", match_all="true",
                         script=ServiceNowDefaults.HAS_ROLE_CRITERIA_SCRIPT)
    inst.kb("kb", can=(has_role,), owner="", managers="")
    inst.article("KB-1", "kb")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert graph.readers("KB-1") == {"kadmin", "admin"}


# --- users ServiceNow no longer has, or that lost their email ----------------------------------


def _users_kb(inst: Instance) -> None:
    _people(inst)
    inst.kb("kb-org", can=(inst.crit("c-any"),))  # an ORG grant
    inst.kb("kb-hr", can=(inst.crit("c-u-hr", user="hr"),))  # a USER grant
    inst.article("KB-1", "kb-hr", cannot_read_user_criteria=inst.crit("c-u-plain", user="plain"))


@pytest.mark.asyncio
async def test_deleted_user_loses_org_and_user_grants(servicenow_connector) -> None:
    inst = Instance()
    _users_kb(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "hr" in graph.kb_readers("kb-org") & graph.kb_readers("kb-hr") & graph.readers("KB-1")
    _incremental(servicenow_connector)
    # A day later: deleted at the source, which leaves no row a delta could see.
    servicenow_connector.user_sync_point.data["users_reconcile/last"] = {"last_sync_time": T0}
    del inst.users["hr"]
    inst.members.remove(("hr", "g-hr"))

    await _sync(servicenow_connector, inst)

    assert "hr" in graph.withdrawn
    assert "hr" not in graph.kb_readers("kb-org") | graph.kb_readers("kb-hr") | graph.readers("KB-1")


@pytest.mark.asyncio
async def test_email_cleared_user_loses_access(servicenow_connector) -> None:
    inst = Instance()
    _users_kb(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    _incremental(servicenow_connector)
    inst.users["hr"].update(email="", sys_updated_on=T1)

    await _sync(servicenow_connector, inst)

    assert "hr" in graph.withdrawn
    assert "hr" not in graph.kb_readers("kb-org") | graph.readers("KB-1")


@pytest.mark.asyncio
async def test_user_an_acl_hides_from_the_user_check_keeps_access(servicenow_connector) -> None:
    inst = Instance()
    _users_kb(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    _incremental(servicenow_connector)
    servicenow_connector.user_sync_point.data["users_reconcile/last"] = {"last_sync_time": T0}
    inst.hidden.add("hr")  # still there, ServiceNow counts it

    await _sync(servicenow_connector, inst)

    assert "hr" not in graph.withdrawn
    assert "hr" in graph.kb_readers("kb-org")


# --- instance properties, groups, ownership groups ---------------------------------------------


@pytest.mark.asyncio
async def test_property_change_between_runs_applies_without_a_restart(servicenow_connector) -> None:
    inst = Instance()
    _kb_art(inst)
    graph = _connector(servicenow_connector, inst, apply_article_criteria=False)
    await _sync(servicenow_connector, inst)
    assert "contrib" in graph.readers("KB-read-plain")
    _incremental(servicenow_connector)
    inst.properties["glide.knowman.apply_article_read_criteria"] = "true"

    await _sync(servicenow_connector, inst)

    assert servicenow_connector.apply_article_read_criteria is True
    assert graph.readers("KB-read-plain") == PRIV  # its own Can Read now holds the contributor too


@pytest.mark.asyncio
async def test_inactive_parent_group_matches_no_member_of_its_active_child(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.group("g-old", active=False)
    inst.group("g-kid", "plain", parent="g-old")
    old = inst.crit("c-old", group="g-old")
    inst.kb("kb-deny", can=(inst.crit("c-any"),), cannot=(old,))  # resolved path, as a deny
    inst.kb("kb-allow", can=(old,), cannot=(inst.crit("c-u-hr", user="hr"),))  # resolved path, as an allow
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "plain" in graph.kb_readers("kb-deny")
    assert "plain" not in graph.kb_readers("kb-allow")


@pytest.mark.asyncio
async def test_ownership_group_child_group_member_reads_the_article(servicenow_connector) -> None:
    # As ServiceNow grants it: members of a child group of the ownership group read the article too.
    inst = Instance()
    _owned(inst)
    inst.group("g-own-kid", "plain", parent="g-own")
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert "plain" in graph.readers("KB-1")
    assert "plain" not in graph.kb_readers("kb")


# --- criteria this account cannot read in full ---------------------------------------------------


@pytest.mark.asyncio
async def test_criterion_with_a_hidden_field_gives_no_org_grant(servicenow_connector) -> None:
    # A field ACL leaves the key out: read without its script, the row would pass for "Any User".
    inst = Instance()
    _people(inst)
    inst.criteria["c-x"] = {**inst.criteria[inst.crit("c-x")], "_hidden_fields": ("script",)}
    inst.kb("kb", can=("c-x",))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    assert not any(p.entity_type == EntityType.ORG for p in graph.group_grants.get("kb", []))
    assert "c-x" in servicenow_connector._criteria_noted["hidden"]
    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_hidden_deny_criterion_alone_does_not_stop_the_run(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB-1", "kb", cannot_read_user_criteria=inst.crit("c-d", user="hr"))
    inst.hidden.add("c-d")  # the only row of its read: ServiceNow counts it and shows none
    graph = _connector(servicenow_connector, inst)

    await _sync(servicenow_connector, inst)

    assert graph.readers("KB-1") == PRIV
    assert "c-d" in servicenow_connector._criteria_noted["hidden"]


@pytest.mark.asyncio
async def test_delta_of_hidden_users_only_does_not_stop_the_run(servicenow_connector) -> None:
    inst = Instance()
    _users_kb(inst)
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    _incremental(servicenow_connector)
    servicenow_connector.user_sync_point.data["users"] = {"last_sync_time": T1}
    inst.users["hr"].update(title="changed", sys_updated_on="2024-06-03 00:00:00")  # the only row of the delta
    inst.hidden.add("hr")

    await _sync(servicenow_connector, inst)

    assert "hr" not in graph.withdrawn


@pytest.mark.asyncio
async def test_hidden_allow_criterion_keeps_the_stored_kb_grants(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb-a", can=(inst.crit("c-hr", group="g-hr"), inst.crit("c-eng", user="eng")))
    graph = _connector(servicenow_connector, inst)
    await _sync(servicenow_connector, inst)
    stored = graph.group_grants["kb-a"]
    assert "eng" in graph.kb_readers("kb-a")

    inst.hidden.add("c-eng")  # unknown is not "grants nobody"
    await _sync(servicenow_connector, inst)

    assert graph.group_grants["kb-a"] is stored
    assert "eng" in graph.kb_readers("kb-a")
    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_hidden_group_that_is_kept_makes_the_full_sync_incomplete(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.group("g-other", "plain")
    inst.kb("kb-a", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB-a", "kb-a")
    sc = servicenow_connector
    graph = _connector(sc, inst)
    await _sync(sc, inst)
    assert sc.full_sync_coverage().complete is True

    inst.hidden.add("g-other")  # an ACL hides it now; it is still there
    sc.data_entities_processor.full_sync_running = MagicMock(return_value=True)  # the run that unlinks
    await _sync(sc, inst)

    assert "g-other" in graph.group_nodes
    assert sc.full_sync_coverage().complete is False
