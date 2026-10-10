"""Who reads what after a ServiceNow sync, over a fake instance that answers the Table API.

The fake evaluates the encoded queries the connector sends, so the connector's own
code decides which rows it reads; a recording processor stores what it writes, and
``who_reads`` resolves group grants and inheritance the way the graph does.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.models.entities import AppUser, AppUserGroup, Record, RecordGroup
from app.models.permission import EntityType, Permission
from app.sources.external.servicenow.models import ServiceNowAPIError, TableAPIRecord, TableAPIResponse

KB = "kb-it"
CATEGORY = "cat-it"
ARTICLE = "art-1"


def _matches(row: dict[str, Any], term: str) -> bool:
    if term.endswith("ISNOTEMPTY"):
        return bool(row.get(term[: -len("ISNOTEMPTY")]))
    if "IN" in term and "=" not in term:
        field, values = term.split("IN", 1)
        return str(row.get(field, "")) in values.split(",")
    if "!=" in term:
        field, value = term.split("!=", 1)
        return str(row.get(field, "")) != value
    if "=" in term:
        field, value = term.split("=", 1)
        return str(row.get(field, "")) == value
    if ">" in term:
        field, value = term.split(">", 1)
        return str(row.get(field, "")) > value
    raise AssertionError(f"fake ServiceNow cannot evaluate {term!r}")


def _query_matches(row: dict[str, Any], query: str | None) -> bool:
    """AND of terms; a term starting with OR joins the previous term."""
    if not query:
        return True
    groups: list[list[str]] = []
    for term in query.split("^"):
        if not term or term.startswith("ORDERBY"):
            continue
        if term.startswith("OR") and groups:
            groups[-1].append(term[2:])
        else:
            groups.append([term])
    return all(any(_matches(row, t) for t in group) for group in groups)


class FakeServiceNow:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.failing: set[str] = set()

    def add(self, table: str, **row: Any) -> dict[str, Any]:
        self.tables[table].append(row)
        return row

    def user(self, sys_id: str, *, active: bool = True, **fields: Any) -> None:
        self.add(
            "sys_user", sys_id=sys_id, user_name=sys_id, email=f"{sys_id}@example.com",
            first_name=sys_id, last_name="", active="true" if active else "false",
            sys_created_on="2026-01-01 00:00:00", sys_updated_on="2026-01-01 00:00:00", **fields,
        )

    def group(self, sys_id: str, *members: str) -> None:
        self.add("sys_user_group", sys_id=sys_id, name=sys_id, sys_updated_on="2026-01-01 00:00:00")
        for user in members:
            self.add("sys_user_grmember", sys_id=f"{sys_id}-{user}", user=user, group=sys_id,
                     sys_updated_on="2026-01-01 00:00:00")

    def role(self, sys_id: str, *members: str) -> None:
        self.add("sys_user_role", sys_id=sys_id, name=sys_id, sys_updated_on="2026-01-01 00:00:00")
        for user in members:
            self.add("sys_user_has_role", sys_id=f"{sys_id}-{user}", user=user, role=sys_id, state="active",
                     sys_updated_on="2026-01-01 00:00:00")

    def criteria(self, sys_id: str, **fields: Any) -> None:
        row = {"sys_id": sys_id, "name": sys_id, "active": "true", "match_all": "false", "advanced": "false"}
        row.update({k: (",".join(v) if isinstance(v, (list, tuple)) else v) for k, v in fields.items()})
        self.add("user_criteria", **row)

    def knowledge_base(self, sys_id: str = KB, *, can_read=(), cannot_read=(), can_contribute=(),
                       cannot_contribute=()) -> None:
        self.add("kb_knowledge_base", sys_id=sys_id, title=sys_id, description="", owner="", kb_managers="",
                 active="true", sys_created_on="2026-01-01 00:00:00", sys_updated_on="2026-01-01 00:00:00")
        for table, ids in (
            ("kb_uc_can_read_mtom", can_read), ("kb_uc_cannot_read_mtom", cannot_read),
            ("kb_uc_can_contribute_mtom", can_contribute), ("kb_uc_cannot_contribute_mtom", cannot_contribute),
        ):
            for criteria_id in ids:
                self.add(table, kb_knowledge_base=sys_id, user_criteria=criteria_id)
        self.add("kb_category", sys_id=CATEGORY, label="IT", value="it", parent_id=sys_id, kb_knowledge_base=sys_id,
                 active="true", sys_created_on="2026-01-01 00:00:00", sys_updated_on="2026-01-01 00:00:00")

    def article(self, sys_id: str = ARTICLE, *, author: str = "", can_read=(), cannot_read=(),
                updated: str = "2026-01-01 00:00:00") -> dict[str, Any]:
        rows = [r for r in self.tables["kb_knowledge"] if r["sys_id"] == sys_id]
        for row in rows:
            self.tables["kb_knowledge"].remove(row)
        return self.add(
            "kb_knowledge", sys_id=sys_id, number=sys_id, short_description=sys_id, text="<p>x</p>", author=author,
            kb_knowledge_base=KB, kb_category=CATEGORY, workflow_state="published", active="true",
            published="2026-01-01", can_read_user_criteria=",".join(can_read),
            cannot_read_user_criteria=",".join(cannot_read), sys_created_on="2026-01-01 00:00:00",
            sys_updated_on=updated,
        )

    async def get_now_table_tableName(self, tableName: str, sysparm_query: str | None = None,
                                      sysparm_fields: str | None = None, sysparm_limit: str | None = None,
                                      sysparm_offset: str | None = None, **_: Any) -> TableAPIResponse:
        if tableName in self.failing:
            raise ServiceNowAPIError(503, f"{tableName} unavailable", None)
        rows = [r for r in self.tables[tableName] if _query_matches(r, sysparm_query)]
        if sysparm_query and "ORDERBYsys_updated_on" in sysparm_query:
            rows.sort(key=lambda r: r.get("sys_updated_on", ""))
        offset = int(sysparm_offset or 0)
        rows = rows[offset: offset + int(sysparm_limit)] if sysparm_limit else rows[offset:]
        fields = [f for f in (sysparm_fields or "").split(",") if f]
        result = [TableAPIRecord(**{**{f: "" for f in fields}, **r}) for r in rows]
        return TableAPIResponse(result=result)


class RecordingProcessor:
    """Stores what the connector writes, with the processor's replace semantics."""

    org_id = "org-1"

    def __init__(self) -> None:
        self.app_users: dict[str, AppUser] = {}
        self.group_members: dict[str, set[str]] = defaultdict(set)
        self.record_groups: dict[str, RecordGroup] = {}
        self.record_group_permissions: dict[str, list[Permission]] = {}
        self.records: dict[str, Record] = {}
        self.record_permissions: dict[str, list[Permission]] = {}
        self.gated_emails: set[str] = set()

    async def on_new_app_users(self, users: list[AppUser]) -> None:
        for user in users:
            self.app_users[user.source_user_id] = user
            self.gated_emails.add(user.email)

    async def remove_app_users_absent_from_source(self, connector_id: str, users: list[AppUser]) -> int:
        keep = {u.email.lower() for u in users if u.email}
        removed = {e for e in self.gated_emails if e.lower() not in keep}
        self.gated_emails -= removed
        return len(removed)

    async def get_all_app_users(self, connector_id: str) -> list[AppUser]:
        return list(self.app_users.values())

    async def get_user_by_source_id(self, source_id: str, connector_id: str) -> AppUser | None:
        return self.app_users.get(source_id)

    async def batch_upsert_user_groups(self, groups: list[AppUserGroup]) -> None:
        return None

    async def on_new_user_groups(self, groups: list[tuple[AppUserGroup, list[AppUser]]]) -> None:
        for group, users in groups:
            self.group_members[group.source_user_group_id] = {u.email for u in users}

    async def create_user_group_membership(self, user_source_id: str, group_external_id: str,
                                           connector_id: str) -> bool:
        self.group_members[group_external_id].add(self.app_users[user_source_id].email)
        return True

    async def on_user_group_member_removed(self, external_group_id: str, user_email: str,
                                           connector_id: str) -> bool:
        members = self.group_members.get(external_group_id, set())
        if user_email in members:
            members.discard(user_email)
            return True
        return False

    async def on_new_record_groups(self, groups: list[tuple[RecordGroup, list[Permission] | None]]) -> None:
        for group, permissions in groups:
            self.record_groups[group.external_group_id] = group
            if permissions is not None:
                self.record_group_permissions[group.external_group_id] = list(permissions)

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        return self.records.get(external_record_id)

    async def on_new_records(self, records: list[tuple[Record, list[Permission]]]) -> None:
        for record, permissions in records:
            key = record.external_record_id
            if record.rewrite_permissions or key not in self.record_permissions:
                self.record_permissions[key] = list(permissions)
            else:
                self.record_permissions[key] += list(permissions)
            self.records[key] = record

    def _principals(self, permissions: list[Permission]) -> set[str]:
        who: set[str] = set()
        for p in permissions:
            if p.entity_type == EntityType.USER:
                who.add(p.email)
            elif p.entity_type == EntityType.GROUP:
                who |= self.group_members.get(p.external_id, set())
        return who

    def group_readers(self, external_group_id: str) -> set[str]:
        who: set[str] = set()
        current: str | None = external_group_id
        while current:
            who |= self._principals(self.record_group_permissions.get(current, []))
            group = self.record_groups.get(current)
            current = group.parent_external_group_id if group and group.inherit_permissions else None
        return who

    def readers(self, external_record_id: str) -> set[str]:
        record = self.records[external_record_id]
        who = self._principals(self.record_permissions.get(external_record_id, []))
        if record.inherit_permissions and record.external_record_group_id:
            who |= self.group_readers(record.external_record_group_id)
        return who & self.gated_emails


@pytest.fixture()
def instance() -> FakeServiceNow:
    return FakeServiceNow()


@pytest.fixture()
def db() -> RecordingProcessor:
    return RecordingProcessor()


@pytest.fixture()
def connector(instance: FakeServiceNow, db: RecordingProcessor) -> ServiceNowConnector:
    with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
        conn = ServiceNowConnector(
            logger=MagicMock(), data_entities_processor=db, data_store_provider=MagicMock(),
            config_service=AsyncMock(), connector_id="sn-1", scope="team", created_by="u-admin",
        )
    conn.connector_name = Connectors.SERVICENOW
    conn.servicenow_client = MagicMock()
    conn.instance_url = "https://example.service-now.com"
    sync_point = MagicMock()
    sync_point.read_sync_point = AsyncMock(return_value=None)
    sync_point.update_sync_point = AsyncMock()
    for name in ("user_sync_point", "category_sync_point", "article_sync_point"):
        setattr(conn, name, sync_point)
    conn.org_entity_sync_points = {k: sync_point for k in conn.org_entity_sync_points}
    conn._get_fresh_datasource = AsyncMock(return_value=instance)
    return conn


def email(sys_id: str) -> str:
    return f"{sys_id}@example.com"


# --------------------------------------------------------------------------- SERVICENOW-07

class TestDeactivatedUsers:
    """A deactivated user cannot log in to ServiceNow, so they read nothing there."""

    def _world(self, instance: FakeServiceNow) -> None:
        instance.add("cmn_department", sys_id="dept-it", name="IT", sys_updated_on="2026-01-01 00:00:00")
        instance.user("alice", department="dept-it")
        instance.user("gone", active=False, department="dept-it")
        instance.group("g-help", "alice", "gone")
        instance.role("r-itil", "alice", "gone")
        instance.criteria("c-help", group="g-help")
        instance.criteria("c-itil", role="r-itil")
        instance.criteria("c-dept", department="dept-it")
        instance.criteria("c-gone", user="gone")
        instance.knowledge_base(can_read=["c-help", "c-itil", "c-dept", "c-gone"])
        instance.article(author="gone")

    async def test_a_deactivated_user_holds_no_group_role_or_department_membership(self, connector, instance, db):
        self._world(instance)

        await connector.run_sync()

        assert db.group_members["g-help"] == {email("alice")}
        assert db.group_members["r-itil"] == {email("alice")}
        assert email("gone") not in db.group_members["dept-it"]
        assert email("alice") in db.group_members["dept-it"]

    async def test_a_deactivated_user_reads_nothing(self, connector, instance, db):
        self._world(instance)

        await connector.run_sync()

        assert email("gone") not in db.group_readers(KB)
        assert email("gone") not in db.gated_emails
        assert db.readers(ARTICLE) == {email("alice")}

    async def test_a_user_deactivated_after_a_sync_loses_access_on_the_next(self, connector, instance, db):
        self._world(instance)
        for row in instance.tables["sys_user"]:
            if row["sys_id"] == "gone":
                row["active"] = "true"
        await connector.run_sync()
        assert email("gone") in db.group_members["dept-it"]
        assert email("gone") in db.group_readers(KB)

        for row in instance.tables["sys_user"]:
            if row["sys_id"] == "gone":
                row.update(active="false", sys_updated_on="2026-02-01 00:00:00")
        await connector.run_sync()

        assert email("gone") not in db.group_members["dept-it"]
        assert email("gone") not in db.group_members["g-help"]
        assert email("gone") not in db.gated_emails
        assert email("gone") not in db.readers(ARTICLE)
        assert all(p.email != email("gone") for p in db.record_permissions[ARTICLE])


# --------------------------------------------------------------------------- SERVICENOW-03

class TestMatchAll:
    """Match All: "Option to make every condition required when the user criteria is applied"."""

    async def test_a_match_all_criteria_grants_only_users_meeting_every_condition(self, connector, instance, db):
        instance.user("both", company="acme", department="dept-it")
        instance.user("it-elsewhere", company="other", department="dept-it")
        instance.user("acme-hr", company="acme", department="dept-hr")
        instance.criteria("c-acme-it", company="acme", department="dept-it", match_all="true")
        instance.knowledge_base(can_contribute=["c-acme-it"])
        instance.article()

        await connector.run_sync()

        assert db.group_readers(KB) == {email("both")}
        assert db.readers(ARTICLE) == {email("both")}

    async def test_a_match_all_criteria_over_two_groups_needs_both(self, connector, instance, db):
        for user in ("a", "b", "c"):
            instance.user(user)
        instance.group("g1", "a", "b")
        instance.group("g2", "b", "c")
        instance.criteria("c-both", group=["g1", "g2"], match_all="true")
        instance.knowledge_base(can_read=["c-both"])

        await connector.run_sync()

        assert db.group_readers(KB) == {email("b")}

    async def test_a_criteria_without_match_all_still_grants_through_its_principals(self, connector, instance, db):
        for user in ("a", "b", "c"):
            instance.user(user, department="dept-it" if user == "c" else "")
        instance.group("g1", "a")
        instance.criteria("c-any", group="g1", department="dept-it")
        instance.knowledge_base(can_read=["c-any"])

        await connector.run_sync()

        assert db.group_readers(KB) == {email("a"), email("c")}
        assert {p.entity_type for p in db.record_group_permissions[KB]} == {EntityType.GROUP}


# --------------------------------------------------------------------------- SERVICENOW-04

class TestCannotRead:
    """Cannot Read wins over Can Read, for a knowledge base and for an article (ServiceNow docs,
    "Managing access to knowledge bases and knowledge articles"); a criteria the connector cannot
    evaluate never widens access."""

    def _team(self, instance: FakeServiceNow, *users: str) -> None:
        for user in users:
            instance.user(user)
        instance.group("g-team", *users)
        instance.criteria("c-team", group="g-team")

    async def test_a_user_denied_by_cannot_read_does_not_read_the_knowledge_base(self, connector, instance, db):
        self._team(instance, "a", "b", "guest")
        instance.criteria("c-guest", user="guest")
        instance.knowledge_base(can_read=["c-team"], cannot_read=["c-guest"])
        instance.article()

        await connector.run_sync()

        assert db.group_readers(KB) == {email("a"), email("b")}
        assert db.readers(ARTICLE) == {email("a"), email("b")}

    async def test_cannot_read_also_removes_a_contributor(self, connector, instance, db):
        self._team(instance, "a", "b")
        instance.criteria("c-b", user="b")
        instance.knowledge_base(can_contribute=["c-team"], cannot_read=["c-b"])

        await connector.run_sync()

        assert db.group_readers(KB) == {email("a")}

    async def test_cannot_contribute_takes_away_the_write_grant(self, connector, instance, db):
        self._team(instance, "a", "b")
        instance.criteria("c-b", user="b")
        instance.knowledge_base(can_read=["c-team"], can_contribute=["c-team"], cannot_contribute=["c-b"])

        await connector.run_sync()

        grants = {p.email: p.type.value for p in db.record_group_permissions[KB]}
        assert grants == {email("a"): "WRITER", email("b"): "READER"}

    @pytest.mark.parametrize("deny", ["script", "unreadable"])
    async def test_a_cannot_read_that_cannot_be_evaluated_denies_everyone(self, connector, instance, db, deny):
        self._team(instance, "a", "b")
        if deny == "script":
            instance.criteria("c-deny", advanced="true", script="answer = gs.getUser().isMemberOf('x');")
        instance.knowledge_base(can_read=["c-team"], cannot_read=["c-deny"])

        await connector.run_sync()

        assert db.group_readers(KB) == set()

    async def test_a_scripted_can_read_grants_nobody_even_with_other_conditions(self, connector, instance, db):
        self._team(instance, "a", "b")
        instance.criteria("c-script", group="g-team", advanced="true", script="answer = false;")
        instance.knowledge_base(can_read=["c-script"])

        await connector.run_sync()

        assert db.group_readers(KB) == set()

    async def test_a_cannot_read_list_that_cannot_be_read_fails_the_sync(self, connector, instance, db):
        self._team(instance, "a")
        instance.knowledge_base(can_read=["c-team"])
        instance.failing.add("kb_uc_cannot_read_mtom")

        with pytest.raises(ServiceNowAPIError):
            await connector.run_sync()

    async def test_an_article_cannot_read_hides_it_from_a_knowledge_base_reader(self, connector, instance, db):
        self._team(instance, "a", "b")
        instance.criteria("c-b", user="b")
        instance.knowledge_base(can_read=["c-team"])
        instance.article(cannot_read=["c-b"])

        await connector.run_sync()

        assert db.group_readers(KB) == {email("a"), email("b")}
        assert db.readers(ARTICLE) == {email("a")}

    async def test_without_the_override_an_article_needs_both_its_own_and_its_knowledge_base_criteria(
        self, connector, instance, db,
    ):
        self._team(instance, "a", "b")
        instance.user("outsider")
        instance.group("g-article", "b", "outsider")
        instance.criteria("c-article", group="g-article")
        instance.knowledge_base(can_read=["c-team"])
        instance.article(can_read=["c-article"])
        connector.apply_article_read_criteria = False

        await connector.run_sync()

        assert db.readers(ARTICLE) == {email("b")}

    async def test_with_the_override_the_article_criteria_decide(self, connector, instance, db):
        self._team(instance, "a", "b")
        instance.user("outsider")
        instance.group("g-article", "b", "outsider")
        instance.criteria("c-article", group="g-article")
        instance.knowledge_base(can_read=["c-team"])
        instance.article(can_read=["c-article"])
        connector.apply_article_read_criteria = True

        await connector.run_sync()

        assert db.readers(ARTICLE) == {email("b"), email("outsider")}

    async def test_the_override_does_not_lift_the_knowledge_base_cannot_read(self, connector, instance, db):
        self._team(instance, "a", "b")
        instance.criteria("c-b", user="b")
        instance.knowledge_base(can_read=["c-team"], cannot_read=["c-b"])
        instance.article(can_read=["c-team"])
        connector.apply_article_read_criteria = True

        await connector.run_sync()

        assert db.readers(ARTICLE) == {email("a")}

    async def test_an_unchanged_restricted_article_follows_a_membership_change_on_a_delta_sync(
        self, connector, instance, db,
    ):
        self._team(instance, "a", "b")
        instance.criteria("c-b", user="b")
        instance.knowledge_base(can_read=["c-team"])
        instance.article(cannot_read=["c-b"])
        await connector.run_sync()
        assert db.readers(ARTICLE) == {email("a")}

        instance.tables["sys_user_grmember"] = [
            r for r in instance.tables["sys_user_grmember"] if r["user"] != "a"
        ]
        connector.article_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2026-06-01 00:00:00"}
        )
        await connector.run_sync()

        assert db.readers(ARTICLE) == set()


# --------------------------------------------------------------------------- SERVICENOW-01

class TestAnyUserCriteria:
    """A user criteria with no condition and no script matches every user ("Any User for KB")."""

    async def test_an_any_user_criteria_lets_every_active_user_read(self, connector, instance, db):
        for user in ("a", "b"):
            instance.user(user)
        instance.user("gone", active=False)
        instance.criteria("c-any")
        instance.knowledge_base(can_read=["c-any"])
        instance.article()

        await connector.run_sync()

        assert db.group_readers(KB) == {email("a"), email("b")}
        assert db.readers(ARTICLE) == {email("a"), email("b")}

    async def test_any_user_minus_cannot_read(self, connector, instance, db):
        for user in ("a", "b", "guest"):
            instance.user(user)
        instance.criteria("c-any")
        instance.criteria("c-guest", user="guest")
        instance.knowledge_base(can_read=["c-any"], cannot_read=["c-guest"])

        await connector.run_sync()

        assert db.group_readers(KB) == {email("a"), email("b")}
