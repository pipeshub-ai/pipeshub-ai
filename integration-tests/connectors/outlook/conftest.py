# pyright: ignore-file

"""Outlook (team / app-only) connector fixtures.

One module-scoped *world* is seeded through app-only Graph before the connector exists, so the
baseline full sync covers every property, data-shape and permission case at once. Later phases
(``outlook_integration_test.py``) mutate that world in batches and run one sync per batch.

Pre-provisioned, never deleted (``OUTLOOK_TEST_*``):
- two licensed mailboxes (primary P, secondary S) in one tenant;
- one Microsoft 365 group with a mailbox (the seed group).

Created per run and removed at teardown, all named ``PHIT-<run>``:
- a folder tree in P's mailbox (one top-level root, so one delete removes it);
- a P→S message (Sent Items copy, S's Inbox copy, S's reply in P's Inbox);
- conversations in the seed group (posted by mailing the group's address — Graph has no
  app-only API to start a group conversation);
- a throwaway M365 group and an AAD user (incremental "added"/"deleted" cases).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest_asyncio
from connectors.outlook import constants as C
from connectors.outlook.constants import OUTLOOK_CONNECTOR_TYPE
from connectors.outlook.outlook_source_helper import (
    OutlookSourceError,
    OutlookSourceHelper,
    http_status,
)
from connectors.outlook.outlook_test_utils import (
    bool_value,
    datetime_after,
    list_value,
    run_sync_and_wait,
    sync_filters,
    wait_until_outlook_condition,
)
from helper.assertions import ConnectorAssertions
from helper.graph_provider_utils import async_poll_until
from helper.pipeshub_users import ensure_pipeshub_user_exists
from helper.second_user import (
    SecondUser,
    log_in_existing_user,
    log_out_existing_user,
)
from helper.source_credentials import source_unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from helper.clients.users_client import UsersClient
    from helper.graph_provider import GraphProviderProtocol
    from pipeshub_client import PipeshubClient

logger = logging.getLogger("outlook-conftest")

_SECRETS = ["OUTLOOK_TEST_TENANT_ID", "OUTLOOK_TEST_CLIENT_ID", "OUTLOOK_TEST_CLIENT_SECRET"]
_USER_GRAPH_TIMEOUT_SEC = int(os.getenv("OUTLOOK_TEST_USER_GRAPH_TIMEOUT", "120"))
# Mail delivery (sendMail → recipient / group mailbox) is asynchronous in Exchange Online.
_DELIVERY_TIMEOUT_SEC = int(os.getenv("OUTLOOK_TEST_DELIVERY_TIMEOUT", "300"))
# Top-level PHIT- folders older than this are leftovers of an earlier run; a live run is younger.
_STALE_FOLDER_AGE = timedelta(hours=6)
# Source objects teardown could not remove, repeated in the terminal summary at session end.
_LEFTOVERS: list[str] = []


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if not _LEFTOVERS:
        return
    logger.warning("Outlook source cleanup left %d object(s) behind: %s", len(_LEFTOVERS), "; ".join(_LEFTOVERS))
    terminalreporter.section("Outlook source leftovers (remove by hand)", sep="!", yellow=True)
    for item in _LEFTOVERS:
        terminalreporter.write_line(item)


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else None


def _user_email(user_obj: Any) -> str:
    """mail or userPrincipalName (mirrors connector AppUser email selection)."""
    return getattr(user_obj, "mail", None) or getattr(user_obj, "user_principal_name", None) or ""


@dataclass
class Mailbox:
    """A seed user: AAD object id + email + PipesHub identities."""

    source_id: str
    email: str
    pipeshub_user_id: str = ""
    graph_id: str = ""
    session: SecondUser | None = None


@dataclass
class OutlookWorld:
    """Every source object the suite asserts on, keyed by role."""

    run: str
    started_at: datetime
    primary: Mailbox
    secondary: Mailbox
    group_id: str
    group_mail: str
    group_domain: str
    folders: dict[str, str] = field(default_factory=dict)
    messages: dict[str, str] = field(default_factory=dict)
    attachments: dict[str, str] = field(default_factory=dict)
    subjects: dict[str, str] = field(default_factory=dict)
    group_threads: dict[str, str] = field(default_factory=dict)
    group_posts: dict[str, str] = field(default_factory=dict)
    group_attachments: dict[str, str] = field(default_factory=dict)
    # Things created mid-suite that teardown must remove.
    extra_message_ids: list[tuple[str, str]] = field(default_factory=list)
    aad_user_ids: list[str] = field(default_factory=list)
    m365_group_ids: list[str] = field(default_factory=list)
    # Deleted users/groups, purged from /directory/deletedItems at teardown.
    deleted_directory_ids: list[str] = field(default_factory=list)
    membership_restore: list[tuple[str, str, bool]] = field(default_factory=list)
    throwaway_group_mail: str = ""
    bulk_message_ids: list[str] = field(default_factory=list)

    @property
    def tag(self) -> str:
        """In the subject of every message this run creates."""
        return f"{C.TEST_RESOURCE_PREFIX}-{self.run}"

    def name(self, label: str) -> str:
        return f"{self.tag}-{label}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def outlook_source_helper() -> AsyncGenerator[OutlookSourceHelper, None]:
    """App-only Graph helper for creating/deleting Outlook test resources."""
    tenant_id, client_id, client_secret = (_env(name) for name in _SECRETS)
    if not tenant_id or not client_id or not client_secret:
        source_unavailable(
            "Outlook credentials not set (OUTLOOK_TEST_TENANT_ID, OUTLOOK_TEST_CLIENT_ID, "
            "OUTLOOK_TEST_CLIENT_SECRET).",
            secrets=_SECRETS,
        )
    helper = OutlookSourceHelper(tenant_id, client_id, client_secret)
    try:
        yield helper
    finally:
        await helper.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol) -> ConnectorAssertions:
    return ConnectorAssertions(graph_provider)


async def _resolve_mailbox(helper: OutlookSourceHelper, raw: str) -> Mailbox:
    """AAD object id or UPN → Mailbox. The connector keys mailboxes by object id, so it must resolve."""
    user = await helper.get_user(raw)
    email = _user_email(user)
    if not getattr(user, "id", None) or not email:
        raise OutlookSourceError(f"seed user {raw} has no object id or email")
    return Mailbox(source_id=user.id, email=email)


async def _wait_for_active_graph_user(graph_provider: GraphProviderProtocol, email: str) -> dict[str, Any]:
    async def _active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        if not user or user.get("isActive") is False:
            return None
        return user

    return await async_poll_until(
        _active, timeout=_USER_GRAPH_TIMEOUT_SEC, interval=2,
        description=f"active Pipeshub graph user for {email}",
    )


async def poll_message(
    helper: OutlookSourceHelper, user_id: str, subject: str, folder: str, description: str
) -> Any:
    """Wait until a message with ``subject`` is in ``folder`` (delivery is asynchronous)."""
    found: list[Any] = []

    async def _visible() -> bool:
        found[:] = await helper.find_messages_by_subject(user_id, subject, folder)
        return bool(found)

    await wait_until_outlook_condition(
        _visible, description=description, timeout=_DELIVERY_TIMEOUT_SEC, poll_interval=5,
    )
    return found[0]


async def _poll_group_thread(helper: OutlookSourceHelper, group_id: str, topic: str) -> Any:
    found: list[Any] = []

    async def _visible() -> bool:
        thread = await helper.find_group_thread(group_id, topic)
        if thread is None:
            return False
        found[:] = [thread]
        return True

    await wait_until_outlook_condition(
        _visible, description=f"group conversation '{topic}'",
        timeout=_DELIVERY_TIMEOUT_SEC, poll_interval=5,
    )
    return found[0]


async def seed_group_post(
    helper: OutlookSourceHelper,
    world: OutlookWorld,
    label: str,
    attachment: tuple[str, bytes, str] | None,
) -> None:
    """Start a conversation in the seed group by mailing its address, then record its ids."""
    topic = world.name(label)
    world.subjects[label] = topic
    await helper.send_mail(
        world.primary.source_id, topic, f"<p>{topic} {C.GROUP_BODY_MARKER}</p>", [world.group_mail],
        attachments=[attachment] if attachment else (),
    )
    thread = await _poll_group_thread(helper, world.group_id, topic)
    world.group_threads[label] = thread.id
    posts = await helper.list_thread_posts(world.group_id, thread.id)
    assert posts, f"group conversation {topic} has no posts"
    world.group_posts[label] = posts[0].id
    if attachment:
        atts: list[Any] = []

        async def _has_attachment() -> bool:
            atts[:] = await helper.list_post_attachments(world.group_id, thread.id, posts[0].id)
            return bool(atts)

        await wait_until_outlook_condition(
            _has_attachment, description=f"attachment on group post {topic}", timeout=120,
        )
        world.group_attachments[label] = atts[0].id
    # The sender's Sent Items copy (and an Inbox copy when the sender is a subscribed member) are
    # ordinary mail in P's mailbox; track them so teardown removes them. The Sent Items copy is
    # also how a later phase replies into this conversation.
    sent = await poll_message(helper, world.primary.source_id, topic, "sentitems", f"{topic} in P sent items")
    world.messages[f"{label}_sent"] = sent.id
    for folder in ("sentitems", "inbox"):
        for msg in await helper.find_messages_by_subject(world.primary.source_id, topic, folder):
            world.extra_message_ids.append((world.primary.source_id, msg.id))


async def _seed_world(helper: OutlookSourceHelper, world: OutlookWorld) -> None:
    """Create the baseline source data. Everything the baseline sync must pick up exists first."""
    p = world.primary.source_id
    f, m, a, s = world.folders, world.messages, world.attachments, world.subjects
    body = C.DEFAULT_MAIL_BODY_HTML

    async def folder(label: str, parent: str | None, name: str | None = None) -> str:
        display = name or world.name(label)
        created = (
            await helper.create_mail_folder(p, display) if parent is None
            else await helper.create_child_folder(p, f[parent], display)
        )
        f[label] = created.id
        return created.id

    async def mail(label: str, folder_label: str, subject: str | None = None, **kwargs: Any) -> str:
        subject = world.name(label) if subject is None else subject
        s[label] = subject
        html = kwargs.pop("body", body)
        created = await helper.create_message_in_folder(p, f[folder_label], subject, html, **kwargs)
        m[label] = created.id
        return created.id

    async def attach(label: str, message_label: str, name: str, data: bytes, content_type: str) -> None:
        created = await helper.add_attachment(p, m[message_label], name, data, content_type)
        a[label] = created.id

    await folder("root", None)
    await folder("props", "root", "Props")
    await mail(
        "basic", "props", subject=world.name("basic ✉ Ünïcødé 日本語 🚀"),
        to=[world.secondary.email], cc=[world.primary.email], bcc=[C.EXTERNAL_ADDRESS],
        body=f"<html><body><p>{C.MAIL_BODY_MARKER}</p></body></html>",
    )
    await mail("empty", "props", subject="", body="")
    await mail("att", "props")
    await attach("txt", "att", C.DEFAULT_ATTACHMENT_NAME, C.DEFAULT_ATTACHMENT_BYTES, C.DEFAULT_ATTACHMENT_CONTENT_TYPE)
    a["inline"] = await helper.add_inline_image(p, m["att"], "phit_inline.png", C.PNG_1PX, "phit-inline")
    a["item"] = await helper.add_item_attachment(p, m["att"], "phit_item", world.name("attached item"))
    await mail("names", "props")
    for label, name in C.ODD_ATTACHMENT_NAMES.items():
        await attach(label, "names", name, label.encode(), "text/plain")
    await mail("many", "props")
    for i in range(C.MANY_ATTACHMENTS):
        await attach(f"many{i:02d}", "many", f"phit_many_{i:02d}.txt", f"attachment {i}\n".encode(), "text/plain")
    if os.getenv("OUTLOOK_TEST_LARGE_ATTACHMENT") == "1":
        await mail("large", "props")
        a["large"] = await helper.add_large_attachment(
            p, m["large"], C.LARGE_ATTACHMENT_NAME, C.large_attachment_bytes(), "application/octet-stream",
        )
    # Already in Deleted Items before the first sync: never synced by default.
    s["in_deleted_items"] = world.name("in deleted items")
    m["in_deleted_items"] = (await helper.create_message_in_folder(p, "deleteditems", s["in_deleted_items"], body)).id
    world.extra_message_ids.append((p, m["in_deleted_items"]))
    s["old"] = world.name("old")
    m["old"] = await helper.create_backdated_message(
        p, f["props"], s["old"], body, world.started_at - timedelta(days=30)
    )

    await folder("deep1", "root", "Deep1")
    await folder("deep2", "deep1", "Deep2")
    await folder("deep3", "deep2", "Deep3")
    await mail("deep", "deep3")

    await folder("bulk", "root", "Bulk")
    bulk_count = int(os.getenv("OUTLOOK_TEST_BULK_MESSAGES", str(C.BULK_MESSAGES)))
    gate = asyncio.Semaphore(C.MAILBOX_CONCURRENCY)

    async def bulk_mail(i: int) -> str:
        async with gate:
            created = await helper.create_message_in_folder(p, f["bulk"], world.name(f"bulk-{i:03d}"), body)
            return created.id

    world.bulk_message_ids = list(await asyncio.gather(*(bulk_mail(i) for i in range(bulk_count))))

    await folder("wide", "root", "Wide")
    for i in range(C.WIDE_CHILD_FOLDERS):
        await folder(f"wide{i:02d}", "wide", f"W{i:02d}")
    await mail("wide_last", f"wide{C.WIDE_CHILD_FOLDERS - 1:02d}")

    # Objects the incremental phases mutate; one mutation per object so the cases stay independent.
    await folder("mut", "root", "Mut")
    await folder("target", "root", "Target")
    await folder("rename", "root", "RenameMe")
    await folder("delete_folder", "root", "DeleteMe")
    await folder("move_folder", "root", "MoveMe")
    await mail("in_deleted_folder", "delete_folder")
    await mail("in_moved_folder", "move_folder")
    for label in ("update", "hard_delete", "soft_delete", "att_remove", "att_add", "reindex_update", "reindex"):
        await mail(label, "mut")
    # Received (non-draft) mail: a draft's revision includes its changeKey, which a move changes.
    s["move"] = world.name("move")
    m["move"] = await helper.create_backdated_message(
        p, f["mut"], s["move"], body, datetime.now(timezone.utc)
    )
    await attach("hard_delete_att", "hard_delete", "phit_gone.txt", b"gone\n", "text/plain")
    await attach("att_remove_att", "att_remove", "phit_removed.txt", b"removed\n", "text/plain")
    await attach("reindex_att", "reindex", "phit_reindex.txt", b"reindex\n", "text/plain")

    # P → S: one message, two mailboxes. S's reply threads under P's Sent Items copy.
    s["exchange"] = world.name("exchange")
    await helper.send_mail(
        p, s["exchange"], f"<p>{C.MAIL_BODY_MARKER} exchange</p>", [world.secondary.email],
        attachments=[(C.EXCHANGE_ATTACHMENT_NAME, C.DEFAULT_ATTACHMENT_BYTES, "text/plain")],
    )
    s_copy = await poll_message(helper, world.secondary.source_id, s["exchange"], "inbox", "exchange mail in S inbox")
    m["s_copy"] = s_copy.id
    world.extra_message_ids.append((world.secondary.source_id, s_copy.id))
    p_sent = await poll_message(helper, p, s["exchange"], "sentitems", "exchange mail in P sent items")
    m["p_sent"] = p_sent.id
    world.extra_message_ids.append((p, p_sent.id))
    await helper.reply(world.secondary.source_id, s_copy.id, f"<p>{C.MAIL_BODY_MARKER} reply</p>")
    s["reply"] = f"RE: {s['exchange']}"
    p_reply = await poll_message(helper, p, s["reply"], "inbox", "S's reply in P inbox")
    m["p_reply"] = p_reply.id
    world.extra_message_ids.append((p, p_reply.id))
    s_sent_reply = await poll_message(
        helper, world.secondary.source_id, s["reply"], "sentitems", "S's reply in S sent items"
    )
    world.extra_message_ids.append((world.secondary.source_id, s_sent_reply.id))

    await seed_group_post(
        helper, world, "group", (C.GROUP_ATTACHMENT_NAME, C.DEFAULT_ATTACHMENT_BYTES, "text/plain")
    )


async def set_membership(helper: OutlookSourceHelper, world: OutlookWorld, user_id: str, member: bool) -> None:
    """Force a seed-group membership state, remembering the original so teardown restores it."""
    is_member = user_id in await helper.group_member_ids(world.group_id)
    if is_member == member:
        return
    if not any(g == world.group_id and u == user_id for g, u, _ in world.membership_restore):
        world.membership_restore.append((world.group_id, user_id, is_member))
    if member:
        await helper.add_group_member(world.group_id, user_id)
    else:
        await helper.remove_group_member(world.group_id, user_id)

    async def _settled() -> bool:
        return (user_id in await helper.group_member_ids(world.group_id)) == member

    await wait_until_outlook_condition(_settled, description=f"seed group membership of {user_id} = {member}")


async def retire_user(helper: OutlookSourceHelper, world: OutlookWorld, user_id: str) -> str | None:
    """Delete an AAD user, or disable and rename it where the app may not delete users.

    Deleting users needs a directory role on top of User.ReadWrite.All, which a test app may lack
    (403) although it can create and disable them. Returns that refusal, or None once deleted.
    """
    try:
        await helper.delete_user(user_id)
    except OutlookSourceError as e:
        if http_status(e) != 403:
            raise
        await helper.update_user(user_id, {"accountEnabled": False, "displayName": world.name("retired user")})
        return str(e)
    if user_id in world.aad_user_ids:
        world.aad_user_ids.remove(user_id)
    world.deleted_directory_ids.append(user_id)
    return None


async def _restore_membership(helper: OutlookSourceHelper, group_id: str, user_id: str, member: bool) -> None:
    if (user_id in await helper.group_member_ids(group_id)) == member:
        return
    if member:
        await helper.add_group_member(group_id, user_id)
    else:
        await helper.remove_group_member(group_id, user_id)


async def _teardown_world(helper: OutlookSourceHelper, world: OutlookWorld) -> None:
    errors: list[str] = []

    async def attempt(label: str, coro: Any) -> None:
        try:
            await coro
        except Exception as e:
            errors.append(f"{label}: {e}")

    async def delete_group(group_id: str) -> None:
        await helper.delete_group(group_id)
        world.deleted_directory_ids.append(group_id)

    async def retire(user_id: str) -> None:
        refused = await retire_user(helper, world, user_id)
        if refused:
            raise OutlookSourceError(f"delete refused, disabled and renamed instead ({refused})")

    async def sweep_messages(user_id: str, skip: set[str]) -> None:
        # Mail no step tracked (a delivery that timed out, an untracked copy) still carries the tag.
        for msg in await helper.search_messages(user_id, world.tag):
            if msg.id in skip or world.tag not in (msg.subject or ""):
                continue
            try:
                await helper.permanent_delete_message(user_id, msg.id)
            except OutlookSourceError as e:
                if http_status(e) != 404:  # the search index lags behind a delete
                    errors.append(f"message {msg.id}: {e}")

    async def sweep_stale_folders(user_id: str) -> None:
        # Earlier runs that died before teardown leave their PHIT- root behind. Only those, only old.
        cutoff = datetime.now(timezone.utc) - _STALE_FOLDER_AGE
        for folder_id, name, created in await helper.list_top_folders_created(user_id):
            if name.startswith(f"{C.TEST_RESOURCE_PREFIX}-") and created is not None and created < cutoff:
                logger.info("TEARDOWN: removing stale folder %r (%s) of %s", name, created, user_id)
                await attempt(f"stale folder {folder_id}", helper.permanent_delete_folder(user_id, folder_id))

    if "root" in world.folders:
        await attempt("root folder", helper.permanent_delete_folder(world.primary.source_id, world.folders["root"]))
    for user_id, message_id in world.extra_message_ids:
        await attempt(f"message {message_id}", helper.permanent_delete_message(user_id, message_id))
    done = {mid for _, mid in world.extra_message_ids} | set(world.messages.values()) | set(world.bulk_message_ids)
    for mailbox in (world.primary, world.secondary):
        await attempt(f"message sweep of {mailbox.email}", sweep_messages(mailbox.source_id, done))
        await attempt(f"stale folder sweep of {mailbox.email}", sweep_stale_folders(mailbox.source_id))
    for label, thread_id in world.group_threads.items():
        await attempt(f"group thread {label}", helper.delete_group_thread(world.group_id, thread_id))
    for group_id, user_id, was_member in reversed(world.membership_restore):
        await attempt(f"membership {user_id}", _restore_membership(helper, group_id, user_id, was_member))
    for group_id in world.m365_group_ids:
        await attempt(f"m365 group {group_id}", delete_group(group_id))
    for user_id in list(world.aad_user_ids):
        await attempt(f"aad user {user_id}", retire(user_id))
    for object_id in world.deleted_directory_ids:
        try:
            await helper.purge_deleted_directory_object(object_id)
        except OutlookSourceError as e:
            if http_status(e) in (403, 404):
                # Not allowed, or not listed yet: Entra purges soft-deleted objects itself after 30 days.
                logger.warning("TEARDOWN: %s left in /directory/deletedItems: %s", object_id, e)
            else:
                errors.append(f"deleted item {object_id}: {e}")
    if errors:
        # Leftovers land in real mailboxes and the directory; make them visible.
        logger.error("Outlook source cleanup left %d item(s): %s", len(errors), "; ".join(errors))
        _LEFTOVERS.extend(errors)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def outlook_world(
    outlook_source_helper: OutlookSourceHelper,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[OutlookWorld, None]:
    raw_users = [u.strip() for u in (_env("OUTLOOK_TEST_USER_IDS") or "").split(",") if u.strip()]
    group_id = _env("OUTLOOK_TEST_GROUP_ID")
    if len(raw_users) < 2 or not group_id:
        source_unavailable(
            "Outlook ITs need OUTLOOK_TEST_USER_IDS (two licensed mailboxes, primary first) "
            "and OUTLOOK_TEST_GROUP_ID (an M365 group with a mailbox).",
            secrets=["OUTLOOK_TEST_USER_IDS", "OUTLOOK_TEST_GROUP_ID"],
        )
    helper = outlook_source_helper
    primary = await _resolve_mailbox(helper, raw_users[0])
    secondary = await _resolve_mailbox(helper, raw_users[1])
    group = await helper.get_group(group_id)
    group_mail = getattr(group, "mail", None)
    if not group_mail:
        raise OutlookSourceError(f"seed group {group_id} has no mail address")

    world = OutlookWorld(
        run=uuid.uuid4().hex[:8],
        started_at=datetime.now(timezone.utc),
        primary=primary,
        secondary=secondary,
        group_id=group_id,
        group_mail=group_mail,
        group_domain=group_mail.split("@", 1)[1],
    )
    world.throwaway_group_mail = f"phitgrp{world.run}@{world.group_domain}"
    try:
        for mailbox in (primary, secondary):
            mailbox.pipeshub_user_id, created = ensure_pipeshub_user_exists(users_client, mailbox.email)
            graph_user = await _wait_for_active_graph_user(graph_provider, mailbox.email)
            mailbox.graph_id = str(graph_user.get("id") or graph_user.get("_key"))
            logger.info("SETUP: Pipeshub user %s graph=%s created=%s", mailbox.email, mailbox.graph_id, created)
        # P is a member of the seed group, S is not; the incremental phases flip S.
        await set_membership(helper, world, primary.source_id, True)
        await set_membership(helper, world, secondary.source_id, False)
        await _seed_world(helper, world)
        for mailbox in (primary, secondary):
            mailbox.session = log_in_existing_user(pipeshub_client, mailbox.pipeshub_user_id, mailbox.email)
        yield world
    finally:
        for mailbox in (primary, secondary):
            if mailbox.session is not None:
                log_out_existing_user(pipeshub_client, mailbox.session)
        await _teardown_world(helper, world)


def baseline_sync_values(world: OutlookWorld) -> dict[str, Any]:
    """The scope every phase returns to: both seed mailboxes, seed + throwaway group, mail since the run.

    The received-date cutoff keeps the real mailboxes' history out of the sync (fast, and no
    unrelated mail is copied), while every message this run creates falls after it.
    """
    cutoff = world.started_at - timedelta(minutes=C.RECEIVED_CUTOFF_SLACK_MINUTES)
    return {
        "users": list_value("in", [world.primary.email, world.secondary.email]),
        "groups": list_value("in", [world.group_mail, world.throwaway_group_mail]),
        "custom_folders": bool_value(True),
        "received_date": datetime_after(int(cutoff.timestamp() * 1000)),
    }


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def outlook_connector(
    outlook_world: OutlookWorld,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Create the connector over the seeded world and run the baseline full sync (B0)."""
    tenant_id, client_id, client_secret = (_env(name) for name in _SECRETS)
    connector_name = f"outlook-test-{outlook_world.run}"
    config: dict[str, Any] = {
        "auth": {
            "authType": C.OUTLOOK_AUTH_TYPE,
            "clientId": client_id,
            "clientSecret": client_secret,
            "tenantId": tenant_id,
            "hasAdminConsent": True,
        },
        "filters": sync_filters(**baseline_sync_values(outlook_world)),
    }
    instance = pipeshub_client.create_connector(
        connector_type=OUTLOOK_CONNECTOR_TYPE,
        instance_name=connector_name,
        scope=C.OUTLOOK_SCOPE,
        config=config,
        auth_type=C.OUTLOOK_AUTH_TYPE,
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state: dict[str, Any] = {"connector_id": connector_id, "connector_name": connector_name}
    try:
        state["baseline_count"] = await run_sync_and_wait(
            pipeshub_client, graph_provider, connector_id,
            lambda: pipeshub_client.toggle_sync(connector_id, enable=True),
            description="B0 baseline full sync", timeout=C.SYNC_TIMEOUT,
        )
        yield state
    finally:
        logger.info("TEARDOWN: deleting connector %s", connector_id)
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
        except Exception as e:
            logger.warning("TEARDOWN: disable failed for %s: %s", connector_id, e)
        # Not caught: a connector delete that leaves data behind errors the teardown and fails the run.
        pipeshub_client.delete_connector(connector_id)
        await graph_provider.assert_all_records_cleaned(
            connector_id,
            timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", str(C.GRAPH_CLEANUP_TIMEOUT))),
        )
