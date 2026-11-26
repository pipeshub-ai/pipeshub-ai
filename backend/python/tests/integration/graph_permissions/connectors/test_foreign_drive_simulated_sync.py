"""Another organization's shared drive, synced end to end: the real Drive Workspace connector, processor, checkpoints
and access check on a real graph, against a simulated Google Drive.

After each run the check is one rule, for every user and file: PipesHub opens a file for a user exactly when Google does.
"""

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator

import google_behaviour_fakes as fakes
import googleapiclient.http
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from drive_world import DriveWorld, FileState

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.sync.sync_runner import _unlink_what_full_sync_missed
from app.connectors.sources.google.drive.team.connector import (
    FOREIGN_SHARED_DRIVE_DESCRIPTION,
    FOREIGN_SHARED_DRIVES_SYNC_POINT_KEY,
    GoogleDriveTeamConnector,
)
from app.connectors.sources.google.drive.utils.drive_full_sync_reconciler import (
    DriveSyncIncomplete,
)
from app.core.org_context import OrgContext, bind

from ...graph_db.postgres.pg_support import OrgBound
from ..processor_harness import build_processor
from ..write_path_support import TS, _seed_app

pytestmark = pytest.mark.integration

ADMIN, ALICE, BOB, CAROL = (f"{n}@example.com" for n in ("admin", "alice", "bob", "carol"))
PEOPLE = (ALICE, BOB, CAROL)
FOREIGN = "0AFOREIGN"


class FaithfulWorld(DriveWorld):
    """The simulator with limited folders: one passes on nothing it inherits, so a drive member (a Manager aside)
    reaches what is in it only through a grant written on it or below it."""

    def _limited(self, state: FileState) -> bool:
        return bool(state.meta.get("inheritedPermissionsDisabled"))

    def _chain(self, file_id: str) -> list[FileState]:
        chain: list[FileState] = []
        for state in super()._chain(file_id):
            chain.append(state)
            if self._limited(state):
                break
        return chain

    names_only = False  # whether a caller is being shown metadata, which a limited folder gives to those it cuts out

    def _cut_out(self, file_id: str, email: str) -> bool:
        """A drive member who sees a limited folder's name and not what is in it."""
        state = self.files.get(file_id)
        return bool(
            state and not state.deleted and self._limited(state) and not self.direct_role(file_id, email)
            and super().member_role(file_id, email) not in (None, "organizer")
            and not any(self._limited(s) for s in super(FaithfulWorld, self)._chain(file_id)[1:])
        )

    def member_role(self, file_id: str, email: str) -> str | None:
        role = super().member_role(file_id, email)
        if role != "organizer" and any(self._limited(s) for s in super()._chain(file_id)):
            return "reader" if self.names_only and self._cut_out(file_id, email) else None
        return role

    def _access_set(self, file_id: str) -> set[str]:
        return super()._access_set(file_id) | {e for e in self.users if self._cut_out(file_id, e)}

    def _for_viewer(self, state: FileState, email: str) -> dict:
        meta = super()._for_viewer(state, email)
        if self._cut_out(meta["id"], email):
            meta["capabilities"] = {**meta["capabilities"], "canListChildren": False, "canEdit": False, "canShare": False}
        return meta

    def _shown(self, handler: object, req: object) -> object:
        self.names_only = True
        try:
            return handler(req)
        finally:
            self.names_only = False

    def _files_get(self, req: object) -> object:
        return self._shown(super()._files_get, req)

    def _permissions_list(self, req: object) -> object:
        if self._cut_out(self._file_id(req), self._caller(req)):
            return fakes.google_error(403, "insufficientFilePermissions")
        return super()._permissions_list(req)

    def _changes_list(self, req: object) -> object:
        out = self._shown(super()._changes_list, req)
        asked = req.query.get("driveId")
        if asked and isinstance(out, dict):
            # A drive's own feed reports a file that left it as removed from it.
            for change in out["changes"]:
                if not change["removed"] and (change.get("driveId") or asked) != asked:
                    change.pop("file", None)
                    change.update(removed=True, driveId=asked)
        return out

    def _listed_to(self, file_id: str, email: str) -> bool:
        return self.can_open(file_id, email) or self._cut_out(file_id, email)

    def _files_list(self, req: object) -> object:
        return self._shown(super()._files_list, req)

    def limit(self, folder_id: str, *, limited: bool = True) -> None:
        self._mutate(folder_id, lambda: self.files[folder_id].meta.update(inheritedPermissionsDisabled=limited))

    def move_to(self, file_id: str, parent: str) -> None:
        def apply() -> None:
            meta = self.files[file_id].meta
            meta["parents"] = [parent]
            meta["driveId"] = self.files[parent].meta.get("driveId") or parent
        before = self.files[file_id].meta.get("driveId")
        self._mutate(file_id, apply)
        self.log.append(type(self.log[-1])(len(self.log) + 1, file_id, before, set(self.users)))


class Sim:
    def __init__(self, provider: object, org: str, http: object) -> None:
        self.provider, self.org, self.http = provider, org, http
        self.connector_id = f"conn-{uuid.uuid4().hex[:8]}"
        self.world = FaithfulWorld(http)
        self.world.admin_email = ADMIN
        self.world.hide_unreadable_parents = True
        for email in (ADMIN, *PEOPLE):
            self.world.add_user(email)
        self.processor, _ = build_processor(provider, org)
        self.connector: GoogleDriveTeamConnector | None = None
        self.user_ids = {e: f"u{i}{uuid.uuid4().hex[:8]}" for i, e in enumerate(PEOPLE)}
        self.incomplete = False

    async def seed(self) -> None:
        await _seed_app(self, self.connector_id, name="Drive Workspace",
                        app_type=Connectors.GOOGLE_DRIVE_WORKSPACE.value, app_group="Google Workspace", org=self.org)
        await self.provider.batch_upsert_nodes(
            [{"_key": key, "userId": key, "email": email, "fullName": email, "orgId": self.org, "isActive": True,
              "createdAtTimestamp": TS, "updatedAtTimestamp": TS} for email, key in self.user_ids.items()],
            collection=CollectionNames.USERS.value,
        )
        # The org's active users, as sign-up links them: only theirs are synced.
        await self.provider.batch_create_edges(
            [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": self.org,
              "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION",
              "createdAtTimestamp": TS, "updatedAtTimestamp": TS} for key in self.user_ids.values()],
            collection=CollectionNames.BELONGS_TO.value,
        )

    async def sync(self) -> None:
        if self.connector is None:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
            account = {"type": "service_account", "project_id": "sim", "private_key_id": "k", "private_key": key,
                       "client_email": "sync@sim.iam.gserviceaccount.com", "client_id": "1", "token_uri": fakes.TOKEN_URI}
            config = fakes.FakeConfigService(
                self.connector_id, {"auth": {"serviceAccountJson": json.dumps(account), "adminEmail": ADMIN}})
            self.connector = GoogleDriveTeamConnector(
                logging.getLogger("drive-sim"), self.processor, self.processor.data_store_provider, config,
                self.connector_id, "team", "admin-user")
            assert await self.connector.init()
        try:
            await self.connector.run_sync()
            self.incomplete = False
        except DriveSyncIncomplete:
            self.incomplete = True

    async def close(self) -> None:
        if self.connector is not None:
            await self.connector.cleanup()

    async def full_resync(self, *, unlink: bool = True) -> None:
        """A full sync as the sync runner does it: every sync point of the connector goes first, and what the run
        did not write loses its links after it (not when the run skipped part of the source: ``unlink``)."""
        _, deleted = await self.provider.delete_sync_points_by_connector_id(connector_id=self.connector_id)
        assert deleted
        seen = self.processor.begin_full_sync_run(self.connector_id)
        try:
            await self.sync()
            if unlink:
                await _unlink_what_full_sync_missed(
                    self.connector, self.connector_id, self.provider, logging.getLogger("drive-sim"), seen)
        finally:
            self.processor.end_full_sync_run(self.connector_id, seen)

    async def record(self, file_id: str) -> object:
        return await self.processor.get_record_by_external_id(
            connector_id=self.connector_id, external_record_id=file_id)

    async def targets(self, node: str, edges: str) -> set[str]:
        found = await self.provider.get_edges_from_node(node, edges)
        return {(e.get("_to") or e.get("to_id") or "").rpartition("/")[2] for e in found}

    async def opens(self, email: str, file_id: str) -> bool:
        record = await self.record(file_id)
        return record is not None and bool(
            await self.provider.check_record_access_with_details(self.user_ids[email], self.org, record.id))

    async def wrong(self, files: list[str] | None = None) -> list[str]:
        """Every user and file where PipesHub and Google disagree."""
        out = []
        roots = {u.root_id for u in self.world.users.values()} | set(self.world.drives)
        for file_id in files or [f for f in self.world.files if f not in roots]:
            for email in PEOPLE:
                google, here = self.world.can_open(file_id, email), await self.opens(email, file_id)
                if google != here:
                    out.append(f"{'LEAK' if here else 'LOST'} {email.split('@')[0]} {file_id}")
        return sorted(out)


# PostgreSQL where a migrated database is configured (scripts/prepare_pg_test_db.sh). Elsewhere its cases are not
# collected: the graph shards run without one, and fail on any skip.
STORES = ["arango", "neo4j", *(["postgres"] if os.environ.get("PG_CORE_URL") else [])]


@pytest.fixture(params=STORES)
async def sim(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, arango_provider: object, neo4j_provider: object,
) -> AsyncIterator[Sim]:
    if not os.environ.get("SECRET_KEY"):
        monkeypatch.setenv("SECRET_KEY", "simulated")
    org = uuid.uuid4().hex[:24]
    real_sleep = asyncio.sleep

    async def no_wait(_delay: float, *_a: object, **_k: object) -> None:
        await real_sleep(0)

    http = fakes.FakeGoogleHttp()
    http.install(monkeypatch)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr(googleapiclient.http, "time", type("T", (), {"sleep": staticmethod(lambda _s: None)}))
    async with contextlib.AsyncExitStack() as stack:
        if request.param == "postgres":
            from app.core.db import DbConfig, KernelDB
            from app.services.graph_db.postgres.provider import PostgresGraphProvider
            db = KernelDB(DbConfig.from_env("connectors"))
            stack.push_async_callback(db.close)
            await db.start()
            provider = OrgBound(PostgresGraphProvider(logging.getLogger("pg-graph"), db), org)
        else:
            provider = arango_provider if request.param == "arango" else neo4j_provider
        world = Sim(provider, org, http)
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            stack.push_async_callback(world.close)
            await world.seed()
            yield world


def foreign(sim: Sim, members: dict[str, str], files: int = 1) -> None:
    sim.world.add_drive(FOREIGN, "Partner drive", members)
    sim.world.drives[FOREIGN]["foreign"] = True
    for i in range(files):
        sim.world.add_item(f"f{i}", f"f{i}.txt", parent=FOREIGN)


@pytest.mark.parametrize("roles", [{ALICE: "reader", BOB: "reader"}, {ALICE: "writer", BOB: "reader"}, {ALICE: "organizer", BOB: "commenter"}],
                         ids=["viewers", "writer", "manager"])
async def test_members_open_it_and_nobody_else_does(sim: Sim, roles: dict) -> None:
    foreign(sim, roles, files=3)
    await sim.sync()
    assert await sim.wrong() == [] and not sim.incomplete
    await sim.sync()
    assert await sim.wrong() == []


@pytest.mark.parametrize("role", ["reader", "writer"])
async def test_leaving_and_coming_back(sim: Sim, role: str) -> None:
    foreign(sim, {ALICE: role, BOB: "reader"}, files=3)
    await sim.sync()
    del sim.world.drives[FOREIGN]["members"][ALICE]
    await sim.sync()
    assert await sim.wrong() == []
    del sim.world.drives[FOREIGN]["members"][BOB]
    await sim.sync()
    assert await sim.wrong() == [] and await sim.record("f0") is not None
    sim.world.drives[FOREIGN]["members"][ALICE] = role
    await sim.sync()
    assert await sim.wrong() == []


async def test_a_leaver_keeps_a_file_shared_with_them(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader", BOB: "reader"}, files=2)
    sim.world.share("f0", {"type": "user", "role": "reader", "emailAddress": ALICE})
    await sim.sync()
    del sim.world.drives[FOREIGN]["members"][ALICE]
    await sim.sync()
    assert await sim.wrong() == []


@pytest.mark.parametrize("roles", [{ALICE: "reader", BOB: "reader"}, {ALICE: "writer", BOB: "reader"}], ids=["viewers", "writer"])
async def test_a_folder_that_turns_limited_cuts_members_out(sim: Sim, roles: dict) -> None:
    foreign(sim, roles)
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    sim.world.share("dir", {"type": "user", "role": "reader", "emailAddress": ALICE})
    await sim.sync()
    assert await sim.wrong() == []
    sim.world.limit("dir")
    await sim.sync()
    assert await sim.wrong() == []
    sim.world.rename("in", "renamed.txt")
    sim.world.rename("dir", "renamed")
    await sim.sync()
    assert await sim.wrong() == []
    await sim.full_resync()
    assert await sim.wrong() == []


async def test_a_file_below_a_limited_folder_shared_with_one_member(sim: Sim) -> None:
    """The member opens the file and is not shown its folder; nobody else may reach it through the drive."""
    foreign(sim, {ALICE: "writer", BOB: "reader"})
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.limit("dir")
    sim.world.add_item("in", "in.txt", parent="dir", perms=[{"type": "user", "role": "writer", "emailAddress": ALICE}])
    await sim.sync()
    assert await sim.wrong() == []
    await sim.sync()
    assert await sim.wrong() == []


@pytest.mark.parametrize("target", ["foreign", "own"])
async def test_a_file_moved_below_a_limited_folder_of_another_drive(sim: Sim, target: str) -> None:
    foreign(sim, {ALICE: "writer", BOB: "reader"}, files=1)
    sim.world.add_drive("0AOWN", "Own drive", {ALICE: "reader", CAROL: "reader"})
    source, dest = ("0AOWN", FOREIGN) if target == "foreign" else (FOREIGN, "0AOWN")
    sim.world.folder("limited", "limited", parent=dest)
    sim.world.limit("limited")
    sim.world.add_item("mv", "mv.txt", parent=source, perms=[{"type": "user", "role": "reader", "emailAddress": ALICE}])
    await sim.sync()
    assert await sim.wrong() == []
    sim.world.move_to("mv", "limited")
    for _ in range(2):
        await sim.sync()
        # In an own drive no synced Manager can list, a member's share below a limited folder is not told apart from
        # their membership (the connector's documented shortcut, the same without a move): lost, never leaked.
        assert await sim.wrong() in ([], ["LOST alice mv"] if target == "own" else [])


@pytest.mark.parametrize("role", ["reader", "writer"])
async def test_a_move_with_its_folder_hidden_drops_the_old_link_and_adds_none(sim: Sim, role: str) -> None:
    """The stored edges, not only access: neither the old folder nor either drive's group is inherited from."""
    foreign(sim, {ALICE: "reader", BOB: "reader"}, files=1)
    sim.world.add_drive("0AOWN", "Own drive", {ALICE: "writer", CAROL: "reader"})
    sim.world.folder("old", "old", parent="0AOWN")
    sim.world.folder("limited", "limited", parent=FOREIGN)
    sim.world.limit("limited")
    sim.world.folder("inner", "inner", parent="limited")
    sim.world.add_item("mv", "mv.txt", parent="old", perms=[{"type": "user", "role": role, "emailAddress": ALICE}])
    await sim.sync()
    node = f"records/{(await sim.record('mv')).id}"
    groups = {d: (await sim.processor.get_record_group_by_external_id(sim.connector_id, d)).id for d in ("0AOWN", FOREIGN)}
    old = (await sim.record("old")).id
    assert old in await sim.targets(node, CollectionNames.INHERIT_PERMISSIONS.value)
    sim.world.move_to("mv", "inner")
    for _ in range(2):
        await sim.sync()
        inherits = await sim.targets(node, CollectionNames.INHERIT_PERMISSIONS.value)
        assert not inherits & {old, *groups.values()}
        assert groups["0AOWN"] not in await sim.targets(node, CollectionNames.BELONGS_TO.value)
        assert await sim.wrong() == []


async def test_a_run_that_fails_to_write_the_group_is_put_right_by_the_next(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader", BOB: "writer"}, files=2)
    await sim.sync()
    del sim.world.drives[FOREIGN]["members"][ALICE]
    real = sim.processor.on_new_record_groups

    async def fails(groups: list) -> None:
        if any(g.external_group_id == FOREIGN for g, _ in groups):
            raise RuntimeError("graph down")
        await real(groups)

    sim.processor.on_new_record_groups = fails
    await sim.sync()
    assert sim.incomplete
    sim.processor.on_new_record_groups = real
    await sim.sync()
    assert await sim.wrong() == []


async def test_a_full_resync_keeps_a_drive_nobody_reaches_and_its_rejoiner(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader"}, files=2)
    sim.world.add_item("mine", "mine.txt", parent=sim.world.users[BOB].root_id, owner=BOB)
    await sim.sync()
    sim.world.drives[FOREIGN]["members"].clear()
    await sim.sync()
    await sim.full_resync()
    assert await sim.record("f0") is not None and await sim.wrong() == []
    sim.world.drives[FOREIGN]["members"][ALICE] = "reader"
    await sim.sync()
    assert await sim.wrong() == []


@pytest.mark.parametrize("first", ["reader", "writer"])
async def test_a_member_who_joins_later_opens_what_is_already_there(sim: Sim, first: str) -> None:
    foreign(sim, {ALICE: first}, files=2)
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    await sim.sync()
    sim.world.drives[FOREIGN]["members"][BOB] = "reader"
    await sim.sync()
    assert await sim.wrong() == []


async def test_a_file_moved_out_of_a_limited_folder_reaches_the_members_again(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader", BOB: "reader"})
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    await sim.sync()
    sim.world.limit("dir")
    await sim.sync()
    assert await sim.wrong() == []
    sim.world.move_to("in", FOREIGN)
    await sim.sync()
    assert await sim.wrong() == []


async def test_a_folder_that_stops_being_limited_lets_the_members_back_in(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader", BOB: "reader"})
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    await sim.sync()
    sim.world.limit("dir")
    await sim.sync()
    sim.world.limit("dir", limited=False)
    await sim.sync()
    await sim.sync()  # the members read the drive again on the run after
    assert await sim.wrong() == []


async def test_new_files_after_the_first_run(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader", BOB: "reader"})
    await sim.sync()
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    sim.world.trash("f0")
    await sim.sync()
    assert await sim.wrong(["in", "dir"]) == []


@pytest.mark.parametrize("roles", [{ALICE: "reader", BOB: "reader"}, {ALICE: "writer", BOB: "reader"}], ids=["viewers", "writer"])
async def test_a_member_who_leaves_before_a_full_resync_is_cut_out_by_it(sim: Sim, roles: dict) -> None:
    foreign(sim, roles, files=2)
    await sim.sync()
    del sim.world.drives[FOREIGN]["members"][BOB]
    await sim.full_resync()
    assert await sim.wrong() == []
    await sim.sync()
    assert await sim.wrong() == []


async def test_the_last_member_leaves_before_a_full_resync(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader"}, files=2)
    sim.world.add_item("mine", "mine.txt", parent=sim.world.users[BOB].root_id, owner=BOB)
    await sim.sync()
    sim.world.drives[FOREIGN]["members"].clear()
    await sim.full_resync()
    assert await sim.wrong() == []
    await sim.sync()
    assert await sim.wrong() == []


@pytest.mark.parametrize("roles", [{ALICE: "reader", BOB: "reader"}, {ALICE: "writer", BOB: "reader"}], ids=["viewers", "writer"])
async def test_a_full_resync_of_an_unchanged_drive(sim: Sim, roles: dict) -> None:
    foreign(sim, roles, files=2)
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    await sim.sync()
    await sim.full_resync()
    assert await sim.wrong() == []


@pytest.mark.parametrize("roles", [{ALICE: "reader", BOB: "reader"}, {ALICE: "writer", BOB: "reader"}], ids=["viewers", "writer"])
async def test_a_folder_limited_before_a_full_resync_cuts_members_out(sim: Sim, roles: dict) -> None:
    foreign(sim, roles)
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    await sim.sync()
    sim.world.limit("dir")
    await sim.full_resync()
    assert await sim.wrong() == []
    await sim.sync()
    assert await sim.wrong() == []


async def test_a_leaver_whose_cleanup_failed_is_cut_out_after_a_full_resync(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader", BOB: "reader"}, files=2)
    await sim.sync()
    del sim.world.drives[FOREIGN]["members"][BOB]
    real = sim.processor.delete_permission_from_record

    async def fails(*_a: object, **_k: object) -> None:
        raise RuntimeError("the graph did not answer")

    sim.processor.delete_permission_from_record = fails
    await sim.sync()
    assert sim.incomplete
    sim.processor.delete_permission_from_record = real
    await sim.full_resync()
    assert await sim.wrong() == []


async def test_the_last_member_leaves_before_a_full_resync_that_unlinks_nothing(sim: Sim) -> None:
    """A full sync that skipped part of the source removes no links of its own."""
    foreign(sim, {ALICE: "reader"}, files=2)
    sim.world.add_item("mine", "mine.txt", parent=sim.world.users[BOB].root_id, owner=BOB)
    await sim.sync()
    sim.world.drives[FOREIGN]["members"].clear()
    await sim.full_resync(unlink=False)
    assert await sim.wrong() == []


@pytest.mark.parametrize("roles", [{ALICE: "reader", BOB: "reader"}, {ALICE: "writer", BOB: "reader"}], ids=["viewers", "writer"])
async def test_members_keep_access_while_their_pass_fails_in_a_full_resync(
    sim: Sim, roles: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign(sim, roles, files=2)
    await sim.sync()
    real = GoogleDriveTeamConnector._reconcile_foreign_drives

    async def fails_for_bob(self: GoogleDriveTeamConnector, email: str | None, *args: object) -> None:
        if email == BOB:
            raise RuntimeError("Google did not answer")
        await real(self, email, *args)

    monkeypatch.setattr(GoogleDriveTeamConnector, "_reconcile_foreign_drives", fails_for_bob)
    await sim.full_resync()
    assert sim.incomplete and await sim.wrong() == []
    group = await sim.processor.get_record_group_by_external_id(sim.connector_id, FOREIGN)
    assert BOB in await sim.processor.get_users_on_record_group(group.id)


@pytest.mark.parametrize("description", [FOREIGN_SHARED_DRIVE_DESCRIPTION, ""], ids=["written", "stub"])
async def test_someone_who_left_before_their_membership_was_ever_stored(sim: Sim, description: str) -> None:
    """What an upgrade finds: grants written by an earlier version, and no record of who held a membership."""
    foreign(sim, {ALICE: "reader", BOB: "reader"}, files=2)
    sim.world.add_item("shared", "shared.txt", parent=FOREIGN)
    sim.world.share("shared", {"type": "user", "role": "reader", "emailAddress": BOB})
    await sim.sync()
    del sim.world.drives[FOREIGN]["members"][BOB]
    await sim.connector.drive_delta_sync_point.delete_sync_point(FOREIGN_SHARED_DRIVES_SYNC_POINT_KEY)
    # An earlier version stored only a stub of the drive, as its files named it.
    group = await sim.processor.get_record_group_by_external_id(sim.connector_id, FOREIGN)
    await sim.provider.update_node(group.id, CollectionNames.RECORD_GROUPS.value, {"description": description})
    await sim.sync()
    assert await sim.wrong() == []
    assert await sim.opens(BOB, "shared")


async def test_a_limited_folder_whose_check_failed_is_checked_after_a_full_resync(
    sim: Sim, monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign(sim, {ALICE: "reader", BOB: "reader"})
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    await sim.sync()
    sim.world.limit("dir")
    with monkeypatch.context() as m:
        async def fails(*_a: object, **_k: object) -> bool:
            raise RuntimeError("the graph did not answer")
        m.setattr(GoogleDriveTeamConnector, "_check_limited_subtree", fails)
        await sim.sync()
    await sim.full_resync()
    assert await sim.wrong() == []


async def test_a_folder_limited_while_the_drives_standing_is_unknown(sim: Sim, monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin's drives.get gives no answer, so no membership is stored yet: the check waits for one."""
    foreign(sim, {ALICE: "reader", BOB: "reader"})
    sim.world.folder("dir", "dir", parent=FOREIGN)
    sim.world.add_item("in", "in.txt", parent="dir")
    with monkeypatch.context() as m:
        async def unknown(*_a: object) -> None:
            return None
        m.setattr(GoogleDriveTeamConnector, "_shared_drive_exists", unknown)
        await sim.sync()
        sim.world.limit("dir")
        await sim.sync()
    await sim.sync()
    assert await sim.wrong() == []


async def test_the_simulated_drive_hides_a_parent_the_reader_cannot_open_everywhere(sim: Sim) -> None:
    foreign(sim, {ALICE: "reader", BOB: "reader"})
    sim.world.folder("limited", "limited", parent=FOREIGN)
    sim.world.folder("inner", "inner", parent="limited")
    sim.world.add_item("child", "child.txt", parent="inner", perms=[{"type": "user", "role": "reader", "emailAddress": ALICE}])
    sim.world.limit("limited")
    await sim.sync()
    await sim.sync()
    user = next(u for u in sim.connector.synced_users if u.email == ALICE)
    source = await sim.connector._build_user_drive_data_source(user)
    listed, token = {}, None
    while True:
        page = await source.files_list(
            driveId=FOREIGN, corpora="drive", supportsAllDrives=True, includeItemsFromAllDrives=True,
            q="trashed = false", fields="nextPageToken, files(id, parents)", **({"pageToken": token} if token else {}))
        listed.update({f["id"]: f.get("parents") for f in page["files"]})
        if not (token := page.get("nextPageToken")):
            break
    got = await source.files_get(fileId="child", supportsAllDrives=True, fields="id, parents")
    assert "child" in listed and not listed["child"] and "inner" not in listed
    assert not got.get("parents")
    assert await sim.wrong() == []
