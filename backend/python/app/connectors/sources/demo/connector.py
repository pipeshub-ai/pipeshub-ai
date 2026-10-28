"""Demo connector: loads the bundled Acme Corp fixture as connector-shaped records.

The fixture is a small synthetic company. Every record is emitted through the
normal entity processor with a real record type (pull request, ticket, message,
file, comment, page, mail, deal, case), the connector name of the system it
imitates (GitHub, Jira, Slack, Google Drive, ServiceNow, Confluence, Gmail,
Salesforce, and knowledge-base uploads), relations between records, and
per-record group permissions — so search, citations, and permission filtering
behave exactly as they would for real connectors, without any external service.

Each record names its people in the fields that system's real connector fills
(``records.py``), so the entity processor links them to members and outside
people exactly as in production; CRM accounts become external organisations
the way the Salesforce connector writes them (``accounts.py``).

Slack messages that share a thread are emitted as one record per thread, the
way the Slack connector groups message bursts.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    OriginTypes,
    PermissionModel,
    RecordRelations,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import (
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.connectors.sources.demo.accounts import sync_accounts
from app.connectors.sources.demo.fixture_loader import (
    FIXTURE_PATH,
    load_fixture,
    record_web_url,
    split_threads,
)
from app.connectors.sources.demo.records import (
    KIND_TO_GROUP_TYPE,
    SYSTEM_TO_CONNECTOR,
    People,
    build_record,
)
from app.connectors.sources.demo.rendering import render_bodies
from app.connectors.sources.demo.systems import thread_slots
from app.models.entities import (
    AppUser,
    AppUserGroup,
    MessageRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_store.data_store import DataStoreProvider

DEMO_CONNECTOR_NAME = "Demo"
DEMO_ICON_PATH = "/icons/connectors/default.svg"
_MARKDOWN = "text/markdown"

# How long a sync waits for the sign-in personas to appear in the graph. The
# grace is what an instance without personas pays before it gives up on them;
# the longer wait only applies once one persona has arrived, which means the
# rest are on their way.
_MEMBERS_GRACE_SECONDS = 15.0
_MEMBERS_WAIT_SECONDS = 60.0
_MEMBERS_POLL_SECONDS = 1.0


async def _wait_for_members(
    emails: list[str],
    lookup: Callable[[str], Awaitable[object | None]],
    logger: Logger,
    grace: float = _MEMBERS_GRACE_SECONDS,
    timeout: float = _MEMBERS_WAIT_SECONDS,
    poll: float = _MEMBERS_POLL_SECONDS,
) -> list[str]:
    """Wait for *emails* to have accounts in the graph; report those that do not.

    Accounts are created through the outbox, a second or two behind the API
    call that asks for them, while enabling a connector reaches the sync
    straight away. Group membership skips a member the graph has not caught up
    with, so a sync that overtakes the first-run script would leave the
    personas out of every group.

    The personas are optional, though: a demo loaded from the connectors page,
    or headless without `PIPESHUB_DEMO_PERSONAS`, never creates them and is
    meant for the installer alone. So the wait gives up rather than failing the
    sync, and gives up early while none of them have appeared.

    *lookup* has to answer for a real account and nothing else. The sync writes
    user rows of its own for the fixture's authors, and one of those would
    otherwise answer "yes" for a persona who has no account at all.
    """
    missing = list(emails)
    loop = asyncio.get_running_loop()
    started = loop.time()
    arrived = False
    while missing:
        still_missing = [email for email in missing if await lookup(email) is None]
        arrived = arrived or len(still_missing) < len(missing)
        missing = still_missing
        if not missing:
            return []
        waited = loop.time() - started
        if waited >= timeout or (not arrived and waited >= grace):
            return sorted(missing)
        logger.info("Demo connector waiting for %d account(s) to be created", len(missing))
        await asyncio.sleep(poll)
    return []


def _people_to_create(people: list[dict], absent: set[str]) -> list[dict]:
    """Fixture people this sync may write a user row for.

    Members only: someone outside the company (``kind: external``) is not an
    org member, and reaches the graph as a person node when a record names
    them, the way a real connector's outside contact does. Of the members,
    everyone except a sign-in persona with no account. Writing a row for one of
    those does more than waste a document: the outbox handler that creates the
    real account looks the address up first, and a row that appears between its
    lookup and its write leaves two users with the same address — the account
    the persona signs in as, and the one this connector hung their group
    membership on. The fixture's authors never have accounts, and nothing else
    ever writes them, so they are safe to create.
    """
    return [
        p for p in people
        if p.get("kind", "member") == "member" and not (p.get("login") and p["email"] in absent)
    ]


# Bump when a change must reach records whose text didn't change: the entity
# processor only rewrites a stored record when its revision differs.
# v2: hide_weburl on every record. v3: people fields on every record.
_REVISION_SALT = "v3"


def _revision_of(body: str) -> str:
    """A content hash the entity processor compares on resync, so an edited
    fixture body re-indexes without deleting and recreating the connector."""
    return hashlib.sha256(f"{_REVISION_SALT}\n{body}".encode("utf-8")).hexdigest()[:16]


def _epoch_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)

def _group_permission(group_id: str) -> Permission:
    return Permission(external_id=group_id, type=PermissionType.READ, entity_type=EntityType.GROUP)


class DemoApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.DEMO, AppGroups.DEMO, connector_id)


@(
    ConnectorBuilder(DEMO_CONNECTOR_NAME)
    .in_group(AppGroups.DEMO.value)
    .with_supported_auth_types("NONE")
    .with_description(
        "Acme Corp, a small fictional company: pull requests, tickets, chat threads, "
        "documents, mail, deals and support cases across nine systems, with two people "
        "who can see different things. Nothing external is contacted."
    )
    .with_categories(["Demo"])
    .with_scopes([ConnectorScope.TEAM.value])
    .with_permission_model(PermissionModel.RECORD_LEVEL)
    .configure(
        lambda builder: builder.with_icon(DEMO_ICON_PATH)
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink("Try PipesHub with demo data", "https://docs.pipeshub.com/demo-data", "setup")
        )
        .with_sync_strategies([SyncStrategy.MANUAL], selected=SyncStrategy.MANUAL)
        .with_sync_support(True)
        .with_agent_support(False)
        .with_hide_connector(False)
    )
    .build_decorator()
)
class DemoConnector(BaseConnector):
    """Loads the Acme Corp fixture; see the module docstring."""

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str = ConnectorScope.TEAM.value,
        created_by: str = "",
    ) -> None:
        super().__init__(
            DemoApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_id = connector_id
        self._fixture: dict[str, Any] | None = None
        self._bodies: dict[str, str] = {}  # external id -> markdown

    # ------------------------------------------------------------------ fixture

    def _load_fixture(self) -> dict[str, Any]:
        if self._fixture is None:
            self._fixture = load_fixture()
            self._bodies = render_bodies(self._fixture)
        return self._fixture

    async def _account_for(self, email: str) -> object | None:
        """The org account for *email*, ignoring rows a connector sync wrote.

        Syncing an app user the graph has never seen writes a placeholder row
        carrying the fixture's own id and `isActive` false. Only the handler
        that consumes `userAdded` makes a row somebody can sign in as, and it
        marks it active, so that is what counts as an account here.
        """
        user = await self.data_entities_processor.get_user_by_email(email)
        return user if user is not None and user.is_active else None

    async def _installer_app_user(self) -> AppUser | None:
        """The org user who created this connector, as a demo app user.

        Added to the groups the fixture marks `installer_joins`, so the person
        who set the demo up sees the shared data without being an Acme persona.
        """
        if not self.created_by:
            return None
        try:
            user = await self.data_entities_processor.get_user_by_user_id(self.created_by)
        except Exception as exc:  # a missing installer only costs them visibility
            self.logger.warning("Could not resolve the connector creator %s: %s", self.created_by, exc)
            return None
        if user is None or not user.email:
            return None
        return AppUser(
            app_name=Connectors.DEMO,
            connector_id=self.connector_id,
            source_user_id="installer",
            email=user.email,
            full_name=user.full_name or user.email,
            is_active=True,
        )

    # ------------------------------------------------------------- lifecycle

    async def init(self) -> bool:
        try:
            fx = self._load_fixture()
            self.logger.info(
                "Demo connector ready: %d records, %d containers, %d people",
                len(fx["records"]), len(fx["containers"]), len(fx["people"]),
            )
            return True
        except Exception as exc:  # surfaced to the caller as a failed init
            self.logger.error("Demo connector could not load its fixture: %s", exc)
            return False

    async def test_connection_and_access(self) -> bool:
        return FIXTURE_PATH.is_file()

    async def get_signed_url(self, record: Record) -> str | None:
        return None

    async def stream_record(
        self,
        record: Record,
        user_id: str | None = None,
        convertTo: str | None = None,
    ) -> StreamingResponse:
        self._load_fixture()
        body = self._bodies.get(record.external_record_id)
        if body is None:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Demo record {record.external_record_id!r} is not in the fixture",
            )
        async def _gen() -> AsyncGenerator[bytes, None]:
            yield body.encode("utf-8")

        # HTTP headers are latin-1 and titles carry dashes and quotes, so name
        # the download after the ASCII external id instead.
        return StreamingResponse(
            _gen(),
            media_type=_MARKDOWN,
            headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.md"'},
        )

    # ------------------------------------------------------------------ sync

    async def run_sync(self) -> None:
        fx = self._load_fixture()
        now = _epoch_ms(datetime.now(timezone.utc))

        # The personas who can sign in want their accounts before anything else
        # here runs: step 1 writes a user row for anyone the graph is missing,
        # which would both answer the wait itself and race the account the
        # outbox is about to create. Fixture people without `login` are authors
        # only and never have an account; the installer was read out of the
        # graph, so they already do.
        sign_in_emails = [p["email"] for p in fx["people"] if p.get("login")]
        absent = set(
            await _wait_for_members(sign_in_emails, self._account_for, self.logger)
        )
        if absent:
            self.logger.info(
                "Demo connector syncing without %s: no account on this instance. "
                "Sync again if you add them, to put them in their groups.",
                ", ".join(sorted(absent)),
            )

        app_users, installer = await self._sync_people(fx, absent)
        memberships = await self._sync_groups(fx, app_users, installer)
        record_groups = await self._sync_record_groups(fx, now)
        records = await self._sync_records(fx, record_groups, now)
        relation_count = await self._sync_relations(fx, records)

        self.logger.info(
            "Demo sync complete: %d records, %d containers, %d groups, %d relations",
            len(records), len(record_groups), len(memberships), relation_count,
        )

    async def _sync_people(
        self, fx: Mapping[str, Any], absent: set[str],
    ) -> tuple[dict[str, AppUser], AppUser | None]:
        """Members as app users. Users whose email matches an org account get
        linked to this app; the rest exist only as authors."""
        app_users: dict[str, AppUser] = {}
        for p in _people_to_create(fx["people"], absent):
            app_users[p["id"]] = AppUser(
                app_name=Connectors.DEMO,
                connector_id=self.connector_id,
                source_user_id=p["id"],
                email=p["email"],
                full_name=p["name"],
                title=p.get("title"),
                is_active=bool(p.get("login")),
            )
        installer = await self._installer_app_user()
        if installer is not None:
            app_users["installer"] = installer
        await self.data_entities_processor.on_new_app_users(list(app_users.values()))
        return app_users, installer

    async def _sync_groups(
        self, fx: Mapping[str, Any], app_users: dict[str, AppUser], installer: AppUser | None,
    ) -> list[tuple[AppUserGroup, list[AppUser]]]:
        """Groups and their members — the only mechanism permissions use here."""
        memberships: list[tuple[AppUserGroup, list[AppUser]]] = []
        for g in fx["groups"]:
            group = AppUserGroup(
                app_name=Connectors.DEMO,
                connector_id=self.connector_id,
                source_user_group_id=g["id"],
                name=g["name"],
            )
            # A persona with no account is not in `app_users`, and has nothing
            # for a membership edge to point at.
            members = [
                app_users[p["id"]]
                for p in fx["people"]
                if g["id"] in p.get("groups", []) and p["id"] in app_users
            ]
            if installer is not None and g.get("installer_joins"):
                members.append(installer)
            memberships.append((group, members))
        await self.data_entities_processor.on_new_user_groups(memberships)
        return memberships

    async def _sync_record_groups(self, fx: Mapping[str, Any], now: int) -> dict[str, RecordGroup]:
        """Containers as record groups (repos, projects, channels, folders,
        spaces, mailboxes, accounts...), then the CRM accounts' organisations."""
        record_groups: dict[str, RecordGroup] = {}
        rg_batch: list[tuple[RecordGroup, list[Permission]]] = []
        for c in fx["containers"]:
            rg = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=c["name"],
                external_group_id=c["id"],
                connector_name=SYSTEM_TO_CONNECTOR[c["system"]],
                connector_id=self.connector_id,
                group_type=KIND_TO_GROUP_TYPE[c["kind"]],
                created_at=now,
                updated_at=now,
            )
            record_groups[c["id"]] = rg
            rg_batch.append((rg, [_group_permission(c["group"])]))
        await self.data_entities_processor.on_new_record_groups(rg_batch)

        organizations = {o["id"]: o for o in fx.get("organizations", [])}
        accounts = [
            (record_groups[c["id"]], organizations[c["organization"]])
            for c in fx["containers"]
            if c["system"] == "SALESFORCE"
        ]
        await sync_accounts(self.data_store_provider, self.data_entities_processor.org_id, accounts, now)
        return record_groups

    async def _sync_records(
        self, fx: Mapping[str, Any], record_groups: dict[str, RecordGroup], now: int,
    ) -> dict[str, Record]:
        """Records. Threads collapse to one message record each."""
        people = {p["id"]: p for p in fx["people"]}
        containers = {c["id"]: c for c in fx["containers"]}
        threads = {t["id"]: t for t in fx.get("threads", [])}
        singles, by_thread = split_threads(fx)
        records: dict[str, Record] = {}
        batch: list[tuple[Record, list[Permission]]] = []
        for rec in singles:
            c = containers[rec["container"]]
            record = self._build_record(rec, c, people, record_groups[c["id"]], now)
            records[rec["id"]] = record
            batch.append((record, [_group_permission(rec.get("group") or c["group"])]))
        for tid, msgs in by_thread.items():
            c = containers[threads[tid]["container"]]
            record = self._build_thread(threads[tid], msgs, c, People(people), record_groups[c["id"]], now)
            records[tid] = record
            batch.append((record, [_group_permission(msgs[0].get("group") or c["group"])]))
        await self.data_entities_processor.on_new_records(batch)
        return records

    async def _sync_relations(self, fx: Mapping[str, Any], records: dict[str, Record]) -> int:
        """Relations, using the ids the store settled on."""
        stored: dict[str, Record] = {}
        for fixture_id in records:
            found = await self.data_entities_processor.get_record_by_external_id(self.connector_id, fixture_id)
            if found is not None:
                stored[fixture_id] = found
        relation_count = 0
        for rec in fx["records"]:
            src = stored.get(rec["id"])
            if src is None:
                continue
            for rel in rec.get("relations", []):
                dst = stored.get(rel["to"])
                if dst is not None:
                    await self.data_entities_processor.create_record_relation(
                        src.id, dst.id, RecordRelations[rel["type"]].value
                    )
                    relation_count += 1
            if rec.get("parent") and rec["parent"] in stored:
                await self.data_entities_processor.create_record_relation(
                    stored[rec["parent"]].id, src.id, RecordRelations.PARENT_CHILD.value
                )
                relation_count += 1
        return relation_count

    def _common_fields(
        self, item: Mapping[str, Any], container: Mapping[str, Any], rg: RecordGroup,
        created: object, updated: object, now: int,
    ) -> dict[str, Any]:
        external_id = item["id"]
        return {
            "org_id": self.data_entities_processor.org_id,
            "record_name": item["title"],
            "external_record_id": external_id,
            "external_revision_id": _revision_of(self._bodies[external_id]),
            "external_record_group_id": container["id"],
            "record_group_id": rg.id,
            "version": 1,
            "origin": OriginTypes.CONNECTOR,
            "connector_name": SYSTEM_TO_CONNECTOR[container["system"]],
            "connector_id": self.connector_id,
            "mime_type": _MARKDOWN,
            # The fixture's addresses are made up, so "Open in Jira/GitHub/..." would
            # lead nowhere: hide_weburl hides it and citations open the record in
            # PipesHub. The address itself stays, because search drops a non-file
            # result whose record has none (retrieval_service).
            "weburl": record_web_url(item, container),
            "hide_weburl": True,
            "source_created_at": _epoch_ms(created),
            "source_updated_at": _epoch_ms(updated or created),
            "created_at": now,
            "updated_at": now,
        }

    def _build_record(
        self, rec: Mapping[str, Any], container: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]],
        rg: RecordGroup, now: int,
    ) -> Record:
        common = self._common_fields(rec, container, rg, rec["created"], rec.get("updated"), now)
        return build_record(rec, container, People(people), common)

    def _build_thread(
        self, thread: Mapping[str, Any], msgs: list[dict], container: Mapping[str, Any], people: People,
        rg: RecordGroup, now: int,
    ) -> MessageRecord:
        common = self._common_fields(thread, container, rg, msgs[0]["created"], msgs[-1]["created"], now)
        involved = thread_slots(msgs)["involved"]
        return MessageRecord(
            record_type=RecordType.MESSAGE,
            record_group_type=RecordGroupType.SLACK_THREAD,
            thread_id=thread["id"],
            has_replies=len(msgs) > 1,
            author_email=people.email(involved),
            involved_user_source_ids=involved,
            **common,
        )

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    def handle_webhook_notification(self, notification: dict) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def reindex_records(self, record_results: list[Record]) -> None:
        await self.data_entities_processor.reindex_existing_records(record_results)

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        return FilterOptionsResponse(success=True, options=[], page=page, limit=limit, has_more=False)

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        data_entities_processor: DataSourceEntitiesProcessor,
        scope: str = ConnectorScope.TEAM.value,
        created_by: str = "",
        **kwargs: object,
    ) -> DemoConnector:
        return DemoConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
