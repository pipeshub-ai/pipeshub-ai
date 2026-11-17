"""Asking people to connect to a per-user connector: invites, reminders, connect links, the roster."""
from __future__ import annotations

import asyncio
import base64
import json
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.connectors.core.base import user_access
from app.connectors.core.base.user_access import (
    LINK_TTL_MS,
    REMINDER_AFTER_MS,
    STATUS_ACTIVE,
    STATUS_REVOKED,
    Invite,
    UserConnection,
    create_link,
    delete_all_connections,
    delete_user_links,
    resolve_link,
)
from app.connectors.core.base.user_access_invites import (
    ConnectRequest,
    send_connect_request,
)

_DAY = 24 * 60 * 60 * 1000
_MOD = "app.connectors.sources.notion.connector"

if TYPE_CHECKING:
    from app.connectors.sources.notion.connector import NotionConnector
    from app.models.entities import User


class _Store:
    """An in-memory configuration store with the calls the per-user code makes."""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    async def get_config(self, key, *args, **kwargs) -> dict | None:
        return self.data.get(key)

    async def set_config(self, key, value, **kwargs) -> bool:
        self.data[key] = value
        return True

    async def delete_config(self, key) -> bool:
        self.data.pop(key, None)
        return True

    async def list_keys_in_directory(self, prefix) -> list[str]:
        return [k for k in self.data if k.startswith(prefix)]


def _connection(user_id: str = "u1", status: str = STATUS_ACTIVE) -> UserConnection:
    return UserConnection(user_id=user_id, email=f"{user_id}@acme.com", token="t", source_user_id=f"n-{user_id}",
                          source_email=f"{user_id}@acme.com", workspace_id="ws-1", status=status, connected_at=1)


def _user(user_id: str, org: str = "org-1", **kw) -> dict:
    return {"userId": user_id, "id": f"k-{user_id}", "email": f"{user_id}@acme.com", "orgId": org,
            "isActive": True, "fullName": user_id.upper(), **kw}


# ---------------------------------------------------------------- store

class TestReminderSchedule:
    def test_reminders_come_at_3_and_10_days_then_stop(self) -> None:
        invite = Invite(user_id="u1")
        invite.sent(0)
        assert not invite.reminder_due(REMINDER_AFTER_MS[0] - 1)
        assert invite.reminder_due(REMINDER_AFTER_MS[0])
        invite.sent(REMINDER_AFTER_MS[0])
        assert not invite.reminder_due(REMINDER_AFTER_MS[1] - 1)
        assert invite.reminder_due(REMINDER_AFTER_MS[1])
        invite.sent(REMINDER_AFTER_MS[1])
        assert invite.send_count == 3 and invite.first_sent_at == 0
        assert not invite.reminder_due(365 * _DAY)

    def test_syncs_that_missed_both_dates_send_one_reminder(self) -> None:
        invite = Invite(user_id="u1")
        invite.sent(0)
        late = 20 * _DAY
        assert invite.reminder_due(late)
        invite.sent(late)
        assert not invite.reminder_due(late + 60 * 60 * 1000)  # the next hourly sync
        assert not invite.reminder_due(late + _DAY)

    def test_an_admins_resend_does_not_use_up_a_reminder(self) -> None:
        invite = Invite(user_id="u1")
        invite.sent(0)
        invite.sent(60 * 1000)  # the admin pressed Remind straight away
        assert invite.reminder_due(REMINDER_AFTER_MS[0])
        invite.sent(REMINDER_AFTER_MS[0])
        assert invite.reminder_due(REMINDER_AFTER_MS[1])

    def test_a_resend_after_a_reminder_date_stands_in_for_that_reminder(self) -> None:
        invite = Invite(user_id="u1")
        invite.sent(0)
        invite.sent(REMINDER_AFTER_MS[0] + _DAY)
        assert not invite.reminder_due(REMINDER_AFTER_MS[1] - 1)
        assert invite.reminder_due(REMINDER_AFTER_MS[1])

    def test_no_reminder_after_opting_out_or_before_any_invite(self) -> None:
        invite = Invite(user_id="u1")
        assert not invite.reminder_due(365 * _DAY)
        invite.sent(0)
        invite.opted_out = True
        assert not invite.reminder_due(365 * _DAY)


class TestRemovalAcrossProcesses:
    async def test_a_connection_written_back_after_a_removal_undoes_itself(self) -> None:
        store = _Store()
        stale = user_access.UserConnection(user_id="u1", email="a@acme.com", token="t", source_user_id="n1",
                                           source_email="a@acme.com", workspace_id="ws", connected_at=100)
        assert await user_access.save_connection(store, "c1", stale) is True
        # Another process removes the user (marker first, then the delete) ...
        await user_access.mark_removed(store, "c1", "u1", now_ms=200)
        await user_access.delete_connection(store, "c1", "u1")
        # ... and a sync that read the connection before that writes it back.
        assert await user_access.save_connection(store, "c1", stale) is False
        assert await user_access.get_connection(store, "c1", "u1") is None

    async def test_connecting_again_after_a_removal_is_kept(self) -> None:
        store = _Store()
        await user_access.mark_removed(store, "c1", "u1", now_ms=200)
        fresh = user_access.UserConnection(user_id="u1", email="a@acme.com", token="t", source_user_id="n1",
                                           source_email="a@acme.com", workspace_id="ws", connected_at=300)
        assert await user_access.save_connection(store, "c1", fresh) is True
        assert await user_access.get_connection(store, "c1", "u1") is not None


    async def test_undoing_a_stale_write_spares_a_newer_connection_stored_meanwhile(self) -> None:
        store = _Store()
        await user_access.mark_removed(store, "c1", "u1", now_ms=200)
        stale = user_access.UserConnection(user_id="u1", email="a@acme.com", token="old", source_user_id="n1",
                                           source_email="a@acme.com", workspace_id="ws", connected_at=100)
        fresh = user_access.UserConnection(user_id="u1", email="a@acme.com", token="new", source_user_id="n1",
                                           source_email="a@acme.com", workspace_id="ws", connected_at=300)
        real_get = store.get_config
        calls = {"n": 0}

        async def get_config(key: str, *a: object, **kw: object) -> object:
            # The stale writer read the marker; another process connects before its cleanup runs.
            if key == user_access.connection_path("c1", "u1") and calls["n"] == 0:
                calls["n"] += 1
                await store.set_config(key, fresh.to_dict())
            return await real_get(key, *a, **kw)

        store.get_config = get_config
        assert await user_access.save_connection(store, "c1", stale) is False
        kept = await user_access.get_connection(store, "c1", "u1")
        assert kept is not None and kept.token == "new"

    async def test_a_removal_between_reading_connections_and_markers_is_seen(self) -> None:
        store = _Store()
        stale = user_access.UserConnection(user_id="u1", email="a@acme.com", token="t", source_user_id="n1",
                                           source_email="a@acme.com", workspace_id="ws", connected_at=100)
        store.data[user_access.connection_path("c1", "u1")] = stale.to_dict()
        real_list = store.list_keys_in_directory

        async def list_keys(prefix: str) -> list[str]:
            if prefix == user_access.connections_prefix("c1"):
                keys = await real_list(prefix)
                await user_access.mark_removed(store, "c1", "u1", now_ms=200)  # another process, right then
                return keys
            return await real_list(prefix)

        store.list_keys_in_directory = list_keys
        assert await user_access.list_connections(store, "c1") == []

    async def test_a_sign_in_whose_start_raced_a_removal_dates_from_before_it(self) -> None:
        routes = _routes()
        store = _Store()
        provider = SimpleNamespace(close=AsyncMock())

        async def start_authorization(**_kw: object) -> str:
            store.data["/services/connectors/c1/sign-in/u1"] = {"oauth": {"state": "s1"}}
            # Another process removes the user while this sign-in is being set up.
            await user_access.mark_removed(store, "c1", "u1", now_ms=10**15)
            return "https://api.notion.com/v1/oauth/authorize?state=s1"

        provider.start_authorization = start_authorization
        with patch.object(routes, "_config_service", return_value=store), \
                patch.object(routes, "OAuthProvider", return_value=provider), \
                patch.object(routes, "user_oauth_config", new=AsyncMock()), \
                patch.object(routes, "current_org_id", return_value="org-1"):
            await routes._authorization_url(MagicMock(), "c1", "u1")
        assert store.data["/services/connectors/c1/sign-in/u1"]["startedAt"] < 10**15

    async def test_a_stale_connection_a_crash_left_behind_is_not_read(self) -> None:
        store = _Store()
        stale = user_access.UserConnection(user_id="u1", email="a@acme.com", token="t", source_user_id="n1",
                                           source_email="a@acme.com", workspace_id="ws", connected_at=100)
        await user_access.mark_removed(store, "c1", "u1", now_ms=200)
        # Written by a process that died before its own check could undo it.
        store.data[user_access.connection_path("c1", "u1")] = stale.to_dict()
        assert await user_access.get_connection(store, "c1", "u1") is None
        assert await user_access.list_connections(store, "c1") == []

    async def test_a_sign_in_begun_before_a_removal_in_another_process_is_undone(self) -> None:
        routes = _routes()
        store = _Store()
        session = "/services/connectors/c1/sign-in/u1"
        store.data[session] = {"oauth": {"state": "s1"}, "startedAt": 100}
        await user_access.mark_removed(store, "c1", "u1", now_ms=200)  # another process; no lock shared
        token = SimpleNamespace(access_token="at", refresh_token="rt")
        provider = SimpleNamespace(handle_callback=AsyncMock(return_value=token), close=AsyncMock())
        user = {"userId": "u1", "email": "a@acme.com", "id": "n1"}
        with patch.object(routes, "_config_service", return_value=store), \
                patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion"})), \
                patch.object(routes, "OAuthProvider", return_value=provider), \
                patch.object(routes, "user_oauth_config", new=AsyncMock()), \
                patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "verify_token", new=AsyncMock(return_value=SimpleNamespace(
                    user_id="p1", email="a@acme.com", workspace_id="ws"))), \
                patch.object(routes, "_request_sync", new=AsyncMock()):
            with pytest.raises(routes._SignInFailed):
                await routes._complete_user_sign_in(
                    MagicMock(), AsyncMock(), user, connector_id="c1", state="s1", code="code")
        assert await user_access.get_connection(store, "c1", "u1") is None


class TestRemovalDuringSignIn:
    async def test_a_sign_in_waiting_on_a_removal_finds_its_session_gone(self) -> None:
        routes = _routes()
        store = _Store()
        session = "/services/connectors/c1/sign-in/u1"
        store.data[session] = {"oauth": {"state": "s1"}}
        removing, resume = asyncio.Event(), asyncio.Event()
        real_delete = user_access.delete_connection

        async def slow_delete(*args: object) -> bool:
            removing.set()
            await resume.wait()
            return await real_delete(*args)

        async def slow_drop(_config_service: object, key: str) -> bool:
            await asyncio.sleep(0.01)  # a real store answers later; another task may run meanwhile
            store.data.pop(key, None)
            return True

        exchange = AsyncMock()
        user = {"userId": "u1", "email": "a@acme.com", "id": "n1"}
        provider = SimpleNamespace(handle_callback=exchange, close=AsyncMock())
        with patch.object(routes, "_config_service", return_value=store), \
                patch.object(routes, "delete_connection", new=slow_delete), \
                patch.object(routes, "delete_config_confirmed", new=slow_drop), \
                patch.object(routes, "_withdraw", new=AsyncMock()), \
                patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion"})), \
                patch.object(routes, "OAuthProvider", return_value=provider), \
                patch.object(routes, "user_oauth_config", new=AsyncMock()), \
                patch.object(routes, "current_org_id", return_value="org-1"):
            removal = asyncio.create_task(routes._remove(MagicMock(), AsyncMock(), "c1", "u1", "n1"))
            await removing.wait()  # the removal holds the lock
            sign_in = asyncio.create_task(routes._complete_user_sign_in(
                MagicMock(), AsyncMock(), user, connector_id="c1", state="s1", code="code"))
            await asyncio.sleep(0.01)
            resume.set()
            await removal
            with pytest.raises(routes._SignInFailed):
                await sign_in
        exchange.assert_not_awaited()
        assert session not in store.data and await user_access.get_connection(store, "c1", "u1") is None


class TestConnectLinks:
    async def test_a_link_names_its_user_until_it_expires_and_only_its_hash_is_stored(self) -> None:
        store = _Store()
        token = await create_link(store, "c1", "u1", "org-1", now_ms=1000)
        connector_id, user_id, secret = token.split(".", 2)
        assert (connector_id, user_id) == ("c1", "u1") and len(secret) >= 40
        assert all(secret not in key for key in store.data)
        link = await resolve_link(store, token, now_ms=1000 + LINK_TTL_MS - 1)
        assert (link.connector_id, link.user_id, link.org_id) == ("c1", "u1", "org-1")
        assert await resolve_link(store, token, now_ms=1000 + LINK_TTL_MS) is None

    @pytest.mark.parametrize("token", ["", "c1", "c1.", ".secret", "c1.u1", "c1.u1.wrong", "c1.u2.x", "a/b.u1.s",
                                       "c1.u/1.s"])
    async def test_a_malformed_or_unknown_token_names_nobody(self, token) -> None:
        store = _Store()
        await create_link(store, "c1", "u1", "org-1", now_ms=0)
        assert await resolve_link(store, token, now_ms=0) is None

    async def test_a_user_keeps_their_newest_three_links(self) -> None:
        store = _Store()
        tokens = [await create_link(store, "c1", "u1", "org-1", now_ms=10**13 + i) for i in range(4)]
        alive = [await resolve_link(store, t, now_ms=10**13 + 10) is not None for t in tokens]
        assert alive == [False, True, True, True]

    async def test_deleting_a_users_links_keeps_everyone_elses(self) -> None:
        store = _Store()
        mine = await create_link(store, "c1", "u1", "org-1", now_ms=10**13)
        theirs = await create_link(store, "c1", "u2", "org-1", now_ms=10**13)
        assert await delete_user_links(store, "c1", "u1") is True
        assert await resolve_link(store, mine, now_ms=10**13) is None
        assert await resolve_link(store, theirs, now_ms=10**13) is not None

    async def test_a_deleted_connector_takes_invites_links_and_member_list_along(self) -> None:
        store = _Store()
        await create_link(store, "c1", "u1", "org-1", now_ms=0)
        await user_access.save_invite(store, "c1", Invite(user_id="u1", send_count=1))
        await user_access.save_members(store, "c1", [{"email": "a@acme.com"}], now_ms=0)
        await user_access.save_connection(store, "c1", _connection())
        await create_link(store, "other", "u1", "org-1", now_ms=0)
        assert await delete_all_connections(store, "c1") is True
        assert all(not k.startswith("/services/connectors/c1/") for k in store.data)
        assert any(k.startswith("/services/connectors/other/") for k in store.data)


# ---------------------------------------------------------------- sending

class TestSendConnectRequest:
    async def _send(self, *, mail_ok: bool = True, reconnect: bool = False, notification_ok: bool = True) -> tuple[dict, AsyncMock, AsyncMock, _Store]:
        store = _Store()
        notifications = MagicMock()
        notifications.publish_notification = AsyncMock(return_value=notification_ok)
        mod = "app.connectors.core.base.user_access_invites"
        with patch(f"{mod}.get_configured_frontend_base_url", new=AsyncMock(return_value="https://ph.example")), \
                patch(f"{mod}._send_email", new=AsyncMock(return_value=mail_ok)) as send_email:
            result = await send_connect_request(
                ConnectRequest(org_id="org-1", connector_id="c1", connector_name="Team Notion", source_name="Notion",
                               user_id="u1", email="u1@acme.com", reconnect=reconnect, inviter_name="Admin"),
                config_service=store, notification_service=notifications, logger=MagicMock(), now_ms=0,
            )
        return result, notifications.publish_notification, send_email, store

    async def test_the_user_gets_a_notification_that_opens_the_dialog_and_an_email_with_a_working_link(self) -> None:
        (notified, emailed), publish, send_email, store = await self._send()
        assert notified and emailed
        sent = publish.await_args.kwargs
        assert sent["recipient_user_ids"] == ["u1"]
        assert sent["redirect_link"] == "workspace/connectors/personal/?connect=c1"
        assert sent["title"] == "Connect your Notion account"
        data = send_email.await_args.kwargs["template_data"]
        link = data["link"]
        assert link.startswith("https://ph.example/connect-account?token=")
        assert data["optOutLink"] == f"{link}&optOut=1" and data["reconnect"] is False
        token = parse_qs(urlparse(link).query)["token"][0]
        resolved = await resolve_link(store, token, now_ms=1)
        assert resolved is not None and resolved.user_id == "u1"
        assert send_email.await_args.args[2] == "u1@acme.com"

    async def test_no_mail_server_still_notifies(self) -> None:
        (notified, emailed), *_ = await self._send(mail_ok=False)
        assert notified and not emailed

    async def test_a_reconnect_says_so(self) -> None:
        _, publish, send_email, _ = await self._send(reconnect=True)
        assert publish.await_args.kwargs["title"] == "Reconnect your Notion account"
        assert send_email.await_args.kwargs["template_data"]["reconnect"] is True


# ---------------------------------------------------------------- routes

def _routes() -> ModuleType:
    from app.connectors.api import user_access_router as routes

    return routes


def _admin_request(user_id: str = "admin") -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": user_id}
    return request


class TestRoster:
    async def _roster(self, *, members, connections=(), invites=None, users=(), lookup_fails=False) -> dict:
        from app.models.entities import User

        routes = _routes()
        graph = AsyncMock()
        by_email = {u["email"].lower(): User(email=u["email"], user_id=u["userId"], org_id=u["orgId"],
                                             full_name=u["fullName"], is_active=u["isActive"]) for u in users}

        async def by_emails(emails, **kw) -> dict[str, User]:
            assert kw == {"raise_on_error": True, "org_id": "org-1"}
            if lookup_fails:
                raise RuntimeError("graph down")
            return {e.lower(): by_email[e.lower()] for e in emails if e.lower() in by_email}

        graph.get_users_by_emails.side_effect = by_emails
        by_id = {u["userId"]: u for u in users}
        with patch.object(routes, "is_request_admin", return_value=True), \
                patch.object(routes, "get_user_or_503", new=AsyncMock(side_effect=lambda _g, uid: by_id.get(uid))), \
                patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion", "config": {}})), \
                patch.object(routes, "_config_service", return_value=AsyncMock()), \
                patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "get_members", new=AsyncMock(return_value=(members, 123))), \
                patch.object(routes, "list_connections", new=AsyncMock(return_value=list(connections))), \
                patch.object(routes, "list_invites", new=AsyncMock(return_value=invites or {})):
            return await routes.get_roster("c1", _admin_request(), graph_provider=graph)

    async def test_everyone_listed_is_matched_by_email_with_a_status(self) -> None:
        members = [
            {"sourceUserId": f"n-{u}", "name": u, "email": f"{u.upper()}@acme.com"}
            for u in ("conn", "gone", "inv", "out", "new", "stranger")
        ]
        result = await self._roster(
            members=members,
            connections=[_connection("conn"), _connection("gone", status=STATUS_REVOKED)],
            invites={"inv": Invite("inv", 1, 1, 1), "out": Invite("out", 1, 1, 1, opted_out=True)},
            users=[_user(u) for u in ("conn", "gone", "inv", "out", "new")],
        )
        status = {p["email"].lower(): p["status"] for p in result["people"]}
        assert status == {
            "conn@acme.com": "connected", "gone@acme.com": "needs_reconnect", "inv@acme.com": "invited",
            "out@acme.com": "opted_out", "new@acme.com": "not_invited", "stranger@acme.com": "no_account",
        }
        assert [p["status"] for p in result["people"]] == [
            "needs_reconnect", "not_invited", "invited", "opted_out", "connected", "no_account"]
        assert result["counts"]["connected"] == 1 and result["membersUpdatedAt"] == 123
        assert all("token" not in (p["connection"] or {}) for p in result["people"])

    async def test_a_connected_user_the_member_list_does_not_show_is_still_listed(self) -> None:
        result = await self._roster(members=[], connections=[_connection("u1")], users=[_user("u1")])
        assert [(p["userId"], p["status"]) for p in result["people"]] == [("u1", "connected")]

    async def test_a_failed_user_read_is_a_503_not_everyone_without_an_account(self) -> None:
        with pytest.raises(HTTPException) as e:
            await self._roster(members=[{"email": "a@acme.com"}], users=[_user("a")], lookup_fails=True)
        assert e.value.status_code == 503

    async def test_the_roster_is_a_404_for_anyone_but_an_admin(self) -> None:
        routes = _routes()
        with patch.object(routes, "is_request_admin", return_value=False):
            with pytest.raises(HTTPException) as e:
                await routes.get_roster("c1", MagicMock(), graph_provider=AsyncMock())
        assert e.value.status_code == 404


class TestInvitesRoute:
    async def _invite(self, *, user_ids=None, all_people=False, roster_people=(), connections=(), users=None,
                      invites=None, sent=(True, True)) -> tuple[dict, AsyncMock, list]:
        routes = _routes()
        users = users or {}
        saved: list[Invite] = []

        async def save(_cfg, _cid, invite) -> None:
            saved.append(invite)

        async def lookup(_graph, user_id) -> dict | None:
            return {"admin": _user("admin"), **users}.get(user_id)

        background = []

        def spawn(coro, name=None) -> asyncio.Task:
            background.append(coro)
            return asyncio.get_running_loop().create_task(asyncio.sleep(0))

        async def get_invite(_cfg, _cid, uid) -> Invite | None:
            return (invites or {}).get(uid)

        with patch.object(routes, "is_request_admin", return_value=True), \
                patch.object(routes, "_per_user_connector",
                             new=AsyncMock(return_value={"type": "Notion", "name": "Team Notion", "config": {}})), \
                patch.object(routes, "spawn_detached", new=spawn), \
                patch.object(routes, "get_invite", new=get_invite), \
                patch.object(routes, "get_user_or_503", new=lookup), \
                patch.object(routes, "get_org_or_503", new=AsyncMock(return_value={"name": "Acme"})), \
                patch.object(routes, "_config_service", return_value=AsyncMock()), \
                patch.object(routes, "_notification_service", return_value=MagicMock()), \
                patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "list_connections", new=AsyncMock(return_value=list(connections))), \
                patch.object(routes, "list_invites", new=AsyncMock(return_value=invites or {})), \
                patch.object(routes, "_roster", new=AsyncMock(return_value={"people": list(roster_people)})), \
                patch.object(routes, "save_invite", new=save), \
                patch.object(routes, "send_connect_request", new=AsyncMock(return_value=sent)) as send:
            result = await routes.send_invites(
                "c1", _admin_request(), user_ids=user_ids, all_people=all_people, graph_provider=AsyncMock())
            for coro in background:
                await coro
        return result, send, saved

    async def test_someone_who_opts_out_while_invite_all_runs_is_skipped(self) -> None:
        routes = _routes()
        store = _Store()
        sent = []

        class _FakeSender:
            config_service = store
            base = SimpleNamespace(connector_id="c1")

            async def __call__(self, user: dict) -> None:
                sent.append(user["userId"])
                if user["userId"] == "a":  # b opts out while a's email goes out
                    await user_access.save_invite(store, "c1", Invite("b", 1, 1, 1, opted_out=True))

        await routes._send_all(_FakeSender(), [{"userId": "a"}, {"userId": "b"}, {"userId": "c"}], "org-1")
        assert sent == ["a", "c"]

    async def test_invite_all_reaches_who_is_not_connected_and_did_not_opt_out(self) -> None:
        people = [{"userId": u, "status": s} for u, s in (
            ("a", "not_invited"), ("b", "invited"), ("c", "needs_reconnect"), ("d", "opted_out"),
            ("e", "connected"), (None, "no_account"), ("f", "needs_reconnect"), ("g", "invited"))]
        now = 10**13
        result, send, saved = await self._invite(
            all_people=True, roster_people=people,
            connections=[_connection("c", status=STATUS_REVOKED), _connection("f", status=STATUS_REVOKED)],
            users={u: _user(u) for u in "abcdefg"},
            invites={"b": Invite("b", 1, 1, 1), "f": Invite("f", 1, 1, 1, opted_out=True),
                     "g": Invite("g", now - 1000, now - 1000, 1)})
        assert sorted(call.args[0].user_id for call in send.await_args_list) == ["a", "b", "c"]
        assert result == {"sent": 3, "emailed": None, "queued": True, "skipped": [
            {"userId": "f", "reason": "opted_out"}, {"userId": "g", "reason": "recently_sent"}]}
        reconnect = {call.args[0].user_id: call.args[0].reconnect for call in send.await_args_list}
        assert reconnect == {"a": False, "b": False, "c": True}
        assert {i.user_id: i.send_count for i in saved} == {"a": 1, "b": 2, "c": 1}
        assert send.await_args_list[0].args[0].inviter_name == "ADMIN"

    async def test_named_people_are_reached_even_if_they_opted_out_but_never_strangers_or_the_connected(self) -> None:
        result, send, _ = await self._invite(
            user_ids=["opted", "conn", "other-org", "nobody"],
            connections=[_connection("conn")],
            users={"opted": _user("opted"), "conn": _user("conn"), "other-org": _user("other-org", org="org-2")},
            invites={"opted": Invite("opted", 1, 1, 1, opted_out=True)})
        assert [call.args[0].user_id for call in send.await_args_list] == ["opted"]
        assert result["sent"] == 1 and result["queued"] is False
        assert {s["userId"]: s["reason"] for s in result["skipped"]} == {
            "conn": "connected", "other-org": "not_found", "nobody": "not_found"}

    async def test_a_failed_mail_is_counted_and_nothing_sent_is_not_recorded(self) -> None:
        result, _, saved = await self._invite(user_ids=["a"], users={"a": _user("a")}, sent=(True, False))
        assert result["sent"] == 1 and result["emailed"] == 0 and saved[0].send_count == 1
        result, _, saved = await self._invite(user_ids=["a"], users={"a": _user("a")}, sent=(False, False))
        assert result["sent"] == 0 and saved == [] and result["skipped"] == [{"userId": "a", "reason": "not_sent"}]

    @pytest.mark.parametrize(("user_ids", "all_people"), [(None, False), (["a"], True)])
    async def test_exactly_one_of_userids_or_all(self, user_ids, all_people) -> None:
        with pytest.raises(HTTPException) as e:
            await self._invite(user_ids=user_ids, all_people=all_people)
        assert e.value.status_code == 400

    async def test_only_admins_invite(self) -> None:
        routes = _routes()
        with patch.object(routes, "is_request_admin", return_value=False):
            with pytest.raises(HTTPException) as e:
                await routes.send_invites("c1", MagicMock(), user_ids=["a"], graph_provider=AsyncMock())
        assert e.value.status_code == 404


class TestRemindersRoute:
    async def test_a_user_turns_their_own_reminders_off_and_on(self) -> None:
        routes = _routes()
        store = _Store()
        with patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion", "config": {}})), \
                patch.object(routes, "get_user_or_503", new=AsyncMock(return_value=_user("u1"))), \
                patch.object(routes, "_config_service", return_value=store):
            assert await routes.set_my_reminders("c1", _admin_request("u1"), opted_out=True,
                                                 graph_provider=AsyncMock()) == {"optedOut": True}
            assert (await user_access.get_invite(store, "c1", "u1")).opted_out is True
            await routes.set_my_reminders("c1", _admin_request("u1"), opted_out=False, graph_provider=AsyncMock())
            assert (await user_access.get_invite(store, "c1", "u1")).opted_out is False


class TestConnectLinkRoutes:
    def _patches(self, routes, store, *, user=None, gate=None) -> list:
        return [
            patch.object(routes, "_config_service", return_value=store),
            patch.object(routes, "get_user_or_503", new=AsyncMock(return_value=user if user is not None else _user("u1"))),
            patch.object(routes, "_per_user_connector",
                         new=gate or AsyncMock(return_value={"type": "Notion", "name": "Team Notion",
                                                             "config": {"auth": {"authType": "OAUTH"}}})),
        ]

    async def _call(self, fn, store, *args, user=None, gate=None, **kwargs) -> dict:
        routes = _routes()
        patches = self._patches(routes, store, user=user, gate=gate)
        for p in patches:
            p.start()
        try:
            return await fn(*args, graph_provider=AsyncMock(), **kwargs)
        finally:
            for p in reversed(patches):
                p.stop()

    async def test_the_public_link_routes_need_no_sign_in_and_nothing_else_does(self) -> None:
        from app import connectors_main

        public = [p for p in connectors_main.EXCLUDE_PATHS if "user-access" in p]
        assert sorted(public) == sorted(f"/api/v1/connectors/user-access/link/{n}"
                                        for n in ("info", "authorize", "complete", "opt-out"))

    async def test_info_names_the_connector_and_the_user(self) -> None:
        routes = _routes()
        store = _Store()
        token = await create_link(store, "c1", "u1", "org-1", now_ms=10**13)
        result = await self._call(routes.connect_link_info, store, MagicMock(), token=token)
        assert result["connectorName"] == "Team Notion" and result["email"] == "u1@acme.com"
        assert result["connected"] is False and result["signInAvailable"] is True

    async def test_the_link_runs_in_its_own_organisation(self) -> None:
        routes = _routes()
        store = _Store()
        token = await create_link(store, "c1", "u1", "org-7", now_ms=10**13)
        seen = {}

        async def gate(*_a) -> dict:
            seen["org"] = routes.current_org_id()
            return {"type": "Notion", "name": "N", "config": {}}

        await self._call(routes.connect_link_info, store, MagicMock(), token=token,
                         user=_user("u1", org="org-7"), gate=gate)
        assert seen["org"] == "org-7"

    @pytest.mark.parametrize("case", ["unknown", "expired", "left_org", "inactive", "not_per_user"])
    async def test_a_dead_link_is_a_404(self, case) -> None:
        routes = _routes()
        store = _Store()
        now = 10**13
        token = await create_link(store, "c1", "u1", "org-1", now_ms=0 if case == "expired" else now)
        user = {"left_org": _user("u1", org="org-2"), "inactive": _user("u1", isActive=False)}.get(case)
        gate = AsyncMock(side_effect=HTTPException(404)) if case == "not_per_user" else None
        with pytest.raises(HTTPException) as e:
            await self._call(routes.connect_link_info, store, MagicMock(),
                             token="c1.nope" if case == "unknown" else token, user=user, gate=gate)
        assert e.value.status_code == 404

    async def test_authorize_starts_a_sign_in_for_the_link_user_in_their_org(self) -> None:
        routes = _routes()
        store = _Store()
        token = await create_link(store, "c1", "u1", "org-1", now_ms=10**13)
        provider = MagicMock()
        provider.start_authorization = AsyncMock(return_value="https://api.notion.com/v1/oauth/authorize?state=s1")
        provider.close = AsyncMock()
        with patch.object(routes, "user_oauth_config", new=AsyncMock(return_value=MagicMock())), \
                patch.object(routes, "OAuthProvider", return_value=provider) as make_provider:
            result = await self._call(routes.connect_link_authorize, store, MagicMock(), token=token)
        assert make_provider.call_args.args[2] == "/services/connectors/c1/sign-in/u1"
        state = parse_qs(urlparse(result["authorizationUrl"]).query)["state"][0]
        decoded = json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)))
        assert decoded == {"state": "s1", "connector_id": "c1", "userAccess": True,
                           "linkUser": "u1", "linkOrg": "org-1"}

    def _state(self, **over) -> str:
        data = {"state": "s1", "connector_id": "c1", "userAccess": True, "linkUser": "u1", "linkOrg": "org-1", **over}
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()

    async def test_complete_connects_the_user_the_state_names(self) -> None:
        routes = _routes()
        complete = AsyncMock(return_value={"name": "Team Notion"})
        with patch.object(routes, "_complete_user_sign_in", new=complete):
            result = await self._call(routes.connect_link_complete, _Store(), MagicMock(),
                                      code="code", state=self._state())
        assert result == {"success": True, "connectorName": "Team Notion"}
        assert complete.await_args.args[2]["userId"] == "u1"
        assert complete.await_args.kwargs == {"connector_id": "c1", "state": "s1", "code": "code"}

    @pytest.mark.parametrize("state", ["garbage", "e30", None])
    async def test_complete_never_raises_on_a_bad_state(self, state) -> None:
        routes = _routes()
        bad = state or self._state(userAccess=None)
        result = await self._call(routes.connect_link_complete, _Store(), MagicMock(), code="c", state=bad)
        assert result["success"] is False

    async def test_complete_refuses_a_user_outside_the_named_org(self) -> None:
        routes = _routes()
        complete = AsyncMock()
        with patch.object(routes, "_complete_user_sign_in", new=complete):
            result = await self._call(routes.connect_link_complete, _Store(), MagicMock(), code="c",
                                      state=self._state(linkOrg="org-2"))
        assert result == {"success": False,
                          "error_message": "Notion sign-in did not complete. Use the link in your email to try again."}
        complete.assert_not_awaited()

    async def test_complete_reports_a_refused_account_without_raising(self) -> None:
        routes = _routes()
        with patch.object(routes, "_complete_user_sign_in",
                          new=AsyncMock(side_effect=routes._SignInFailed("wrong account", shareable=True))):
            result = await self._call(routes.connect_link_complete, _Store(), MagicMock(), code="c",
                                      state=self._state())
        assert result == {"success": False, "error_message": "wrong account"}

    async def test_a_forged_state_leaves_the_users_sign_in_in_progress_alone(self) -> None:
        routes = _routes()
        store = _Store()
        session = "/services/connectors/c1/sign-in/u1"
        store.data[session] = {"oauth": {"state": "the-real-one"}}
        result = await self._call(routes.connect_link_complete, store, MagicMock(), code="c",
                                  state=self._state(state="forged"))
        assert result["success"] is False
        assert store.data[session] == {"oauth": {"state": "the-real-one"}}

    async def test_public_routes_never_act_as_a_signed_in_user(self) -> None:
        routes = _routes()
        store = _Store()
        token = await create_link(store, "c1", "u1", "org-1", now_ms=10**13)
        seen = {}

        async def gate(*_a) -> dict:
            from app.core.org_context import current

            seen["kind"] = current().kind
            return {"type": "Notion", "name": "N", "config": {}}

        await self._call(routes.connect_link_info, store, MagicMock(), token=token, gate=gate)
        assert seen["kind"] == "service"

    async def test_opt_out_from_the_email(self) -> None:
        routes = _routes()
        store = _Store()
        token = await create_link(store, "c1", "u1", "org-1", now_ms=10**13)
        assert await self._call(routes.connect_link_opt_out, store, MagicMock(), token=token) == {"optedOut": True}
        assert (await user_access.get_invite(store, "c1", "u1")).opted_out is True


class TestConnectDropsLinks:
    async def test_connecting_ends_the_users_links(self) -> None:
        from app.connectors.sources.notion.per_user import NotionIdentity

        routes = _routes()
        store = _Store()
        token = await create_link(store, "c1", "u1", "org-1", now_ms=10**13)
        await user_access.save_invite(store, "c1", Invite("u1", 1, 1, 2))
        with patch.object(routes, "verify_token", new=AsyncMock(return_value=NotionIdentity("p1", "u1@acme.com", "ws-1"))), \
                patch.object(routes, "_config_service", return_value=store), \
                patch.object(routes, "_request_sync", new=AsyncMock()):
            await routes._connect(MagicMock(), {"type": "Notion"}, "c1", _user("u1"), "tok", method="token")
        assert await resolve_link(store, token, now_ms=10**13) is None
        assert await user_access.get_connection(store, "c1", "u1") is not None
        assert await user_access.get_invite(store, "c1", "u1") is None


# ---------------------------------------------------------------- sync

def _sync_connector() -> NotionConnector:
    from app.connectors.sources.notion.connector import NotionConnector

    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_user_by_user_id = AsyncMock()
    dep.withdraw_user_from_connector = AsyncMock()
    return NotionConnector(
        logger=MagicMock(), data_entities_processor=dep, data_store_provider=MagicMock(),
        config_service=_Store(), connector_id="c1", scope="team", created_by="admin",
    )


class TestSyncReminders:
    async def test_due_reminders_go_to_who_has_not_connected_and_are_recorded(self) -> None:
        connector = _sync_connector()
        store = connector.config_service
        now = 100 * _DAY
        due, early, opted, connected, waiting = (
            Invite("due", now - 4 * _DAY, now - 4 * _DAY, 1), Invite("early", now - _DAY, now - _DAY, 1),
            Invite("opted", 0, 0, 1, opted_out=True), Invite("conn", 0, 0, 1), Invite("wait", 0, 0, 1))
        for invite in (due, early, opted, connected, waiting):
            await user_access.save_invite(store, "c1", invite)
        await user_access.save_connection(store, "c1", _connection("wait", status=STATUS_REVOKED))
        connector.data_entities_processor.get_user_by_user_id.side_effect = (
            lambda uid: SimpleNamespace(email=f"{uid}@acme.com", is_active=True))
        connector._ask_to_connect = AsyncMock(return_value=True)
        with patch(f"{_MOD}.get_epoch_timestamp_in_ms", return_value=now):
            await connector._send_due_reminders({"conn"})
        connector._ask_to_connect.assert_awaited_once_with("due", "due@acme.com", reconnect=False)
        assert (await user_access.get_invite(store, "c1", "due")).send_count == 2

    async def test_a_reminder_failure_never_fails_the_sync(self) -> None:
        connector = _sync_connector()
        connector.config_service.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("store down"))
        await connector._send_due_reminders(set())

    async def test_a_revoke_asks_to_reconnect_once(self) -> None:
        connector = _sync_connector()
        await user_access.save_connection(connector.config_service, "c1", _connection("u1"))
        connector._ask_to_connect = AsyncMock(return_value=True)
        connection = _connection("u1")
        await connector._revoke_connection(connection, SimpleNamespace(id="k-u1"), "refused")
        connector._ask_to_connect.assert_awaited_once_with("u1", "u1@acme.com", reconnect=True)
        await connector._revoke_connection(connection, SimpleNamespace(id="k-u1"), "refused again")
        assert connector._ask_to_connect.await_count == 1

    async def test_a_revoke_of_a_connection_removed_meanwhile_asks_nothing(self) -> None:
        connector = _sync_connector()
        connector._ask_to_connect = AsyncMock(return_value=True)
        await connector._revoke_connection(_connection("u1"), SimpleNamespace(id="k-u1"), "refused")
        connector._ask_to_connect.assert_not_awaited()


class TestSyncUsersWhoLeft:
    async def _run(self, user) -> tuple[NotionConnector, _Store]:
        connector = _sync_connector()
        store = connector.config_service
        await user_access.save_connection(store, "c1", _connection("u1"))
        await user_access.save_invite(store, "c1", Invite("u1", 1, 1, 1))
        await create_link(store, "c1", "u1", "org-1", now_ms=10**13)
        connector.data_entities_processor.get_user_by_user_id.return_value = user
        connector._get_fresh_datasource = AsyncMock()
        connector._assert_required_capabilities = AsyncMock()
        connector._ensure_workspace_from_bot = AsyncMock()
        connector.workspace_id = "ws-1"
        connector._prepare_per_user_workspace = AsyncMock()
        connector._load_bot_user_ids = AsyncMock()
        connector._save_member_list = AsyncMock()
        connector._link_connected_users = AsyncMock()
        connector._send_due_reminders = AsyncMock()
        connector._run_user_pass = AsyncMock(return_value=True)
        with patch(f"{_MOD}.load_connector_filters", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
            await connector._run_per_user_sync()
        return connector, store

    async def test_a_deactivated_user_loses_access_but_keeps_the_connection(self) -> None:
        connector, store = await self._run(SimpleNamespace(id="k-u1", email="u1@acme.com", is_active=False))
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once_with("c1", "k-u1")
        connector._run_user_pass.assert_not_awaited()
        assert await user_access.get_connection(store, "c1", "u1") is not None

    async def test_a_deleted_user_leaves_no_credential_behind(self) -> None:
        _, store = await self._run(None)
        assert not any("u1" in key for key in store.data)


class TestMemberList:
    def _page(self, results, *, has_more=False, cursor=None, success=True) -> SimpleNamespace:
        return SimpleNamespace(success=success, data=SimpleNamespace(json=lambda: {
            "results": results, "has_more": has_more, "next_cursor": cursor}))

    async def _read(self, pages) -> list[dict]:
        connector = _sync_connector()
        datasource = MagicMock()
        datasource.list_users = AsyncMock(side_effect=pages)
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        return await connector._read_member_list()

    async def test_people_with_an_email_across_pages(self) -> None:
        members = await self._read([
            self._page([{"type": "person", "id": "p1", "name": "Ann", "person": {"email": "ann@acme.com"}},
                        {"type": "bot", "id": "b1"},
                        {"type": "person", "id": "p2", "name": "No Mail", "person": {}}],
                       has_more=True, cursor="c2"),
            self._page([{"type": "person", "id": "p3", "name": "Bo", "person": {"email": "bo@acme.com"}}]),
        ])
        assert members == [{"sourceUserId": "p1", "name": "Ann", "email": "ann@acme.com"},
                           {"sourceUserId": "p3", "name": "Bo", "email": "bo@acme.com"}]

    @pytest.mark.parametrize("pages", [
        "failed", "stuck_cursor", "no_cursor",
    ])
    async def test_a_list_read_short_is_none(self, pages) -> None:
        first = {"failed": self._page([], success=False),
                 "stuck_cursor": self._page([], has_more=True, cursor=None),
                 "no_cursor": self._page([{"type": "person", "id": "p1", "person": {"email": "a@a.com"}}],
                                         has_more=True)}[pages]
        assert await self._read([first, self._page([])]) is None

    async def test_a_short_list_does_not_replace_the_last_whole_one(self) -> None:
        connector = _sync_connector()
        await user_access.save_members(connector.config_service, "c1", [{"email": "old@acme.com"}], now_ms=1)
        connector._read_member_list = AsyncMock(return_value=None)
        await connector._save_member_list()
        members, updated = await user_access.get_members(connector.config_service, "c1")
        assert members == [{"email": "old@acme.com"}] and updated == 1

