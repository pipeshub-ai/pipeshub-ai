# pyright: ignore-file

"""App-only Microsoft Graph helper for Outlook integration tests.

Creates and deletes source resources (mail folders, child folders, messages, attachments,
group membership) using the SAME app-only client the connector uses at
``app/connectors/sources/microsoft/outlook/connector.py`` ``init()``. Endpoints that lack a
generated wrapper (``sendMail``, add/remove group member) go through the Kiota request adapter,
mirroring ``app/connectors/sources/microsoft/common/msgraph_client.py`` (``search_query`` /
``get_delta_response``).

Deletes return ``None`` from the SDK (204 No Content), which the wrappers surface as
``success=False``; callers must verify deletions via read-back, not the ``success`` flag.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import requests
from app.connectors.sources.microsoft.common.outlook_constants import OutlookAPIFields
from app.connectors.sources.microsoft.common.outlook_folders import prefer_immutable_ids
from app.sources.client.microsoft.microsoft import (
    GraphMode,
    MSGraphClientWithClientIdSecretConfig,
)
from app.sources.client.microsoft.microsoft import (
    MSGraphClient as ExternalMSGraphClient,
)
from app.sources.external.microsoft.outlook.outlook import (
    OutlookCalendarContactsDataSource,
    _dict_to_attachment,
    _dict_to_message,
)
from app.sources.external.microsoft.users_groups.users_groups import (
    UsersGroupsDataSource,
)
from kiota_abstractions.method import Method
from kiota_abstractions.request_information import RequestInformation
from msgraph.generated.models.group import Group
from msgraph.generated.models.mail_folder import MailFolder
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.password_profile import PasswordProfile
from msgraph.generated.models.user import User

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger("outlook-source-helper")

_GRAPH_V1 = "https://graph.microsoft.com/v1.0"
_ERROR_MAP = {"4XX": ODataError, "5XX": ODataError}


class OutlookSourceError(RuntimeError):
    """Raised when a Graph create/read operation fails."""


def http_status(error: BaseException) -> int | None:
    """The Graph HTTP status behind an ``OutlookSourceError`` (None when it did not come from Graph)."""
    return getattr(error.__cause__, "response_status_code", None)


class _Obj:
    """Read-only view of a raw Graph JSON object with the SDK's snake_case attribute names."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), name.rstrip("_"))
        return self._data.get(camel, self._data.get(name))

    def __repr__(self) -> str:
        return f"_Obj({self._data.get('id')})"


class OutlookSourceHelper:
    """Thin app-only Graph wrapper for creating/deleting Outlook test resources."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        # The same immutable ids the connector stores, so ids compare across a move.
        self._graph_client = prefer_immutable_ids(ExternalMSGraphClient.build_with_config(
            MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id),
            mode=GraphMode.APP,
        ))
        self.outlook_ds = OutlookCalendarContactsDataSource(self._graph_client)
        self.users_ds = UsersGroupsDataSource(self._graph_client)
        self._service_client = self._graph_client.get_client().get_ms_graph_service_client()
        self.adapter = self._service_client.request_adapter

    async def close(self) -> None:
        try:
            await self._graph_client.get_client().close()
        except Exception as e:
            logger.warning("OutlookSourceHelper.close failed: %s", e)

    # -- response unwrapping ------------------------------------------------

    @staticmethod
    def _unwrap(resp: Any, context: str) -> Any:
        """Return ``resp.data`` or raise with the wrapper's error."""
        if resp is None or not getattr(resp, "success", False):
            err = getattr(resp, "error", None) if resp is not None else "no response"
            raise OutlookSourceError(f"{context} failed: {err}")
        return resp.data

    # -- generic adapter (no-wrapper endpoints) ----------------------------

    async def _post_no_content(self, url: str, body: dict[str, Any], context: str) -> None:
        ri = RequestInformation(Method.POST, url)
        ri.headers.add("Content-Type", "application/json")
        ri.content = json.dumps(body).encode("utf-8")
        try:
            await self.adapter.send_no_response_content_async(ri, error_map=_ERROR_MAP)
        except ODataError as e:
            raise OutlookSourceError(f"{context} failed: {e}") from e

    async def _delete_no_content(self, url: str, context: str) -> None:
        ri = RequestInformation(Method.DELETE, url)
        try:
            await self.adapter.send_no_response_content_async(ri, error_map=_ERROR_MAP)
        except ODataError as e:
            raise OutlookSourceError(f"{context} failed: {e}") from e

    async def _patch_json(self, url: str, body: dict[str, Any], context: str) -> None:
        ri = RequestInformation(Method.PATCH, url)
        ri.headers.add("Content-Type", "application/json")
        ri.content = json.dumps(body).encode("utf-8")
        try:
            await self.adapter.send_no_response_content_async(ri, error_map=_ERROR_MAP)
        except ODataError as e:
            raise OutlookSourceError(f"{context} failed: {e}") from e

    async def _request_json(
        self, method: Method, url: str, body: dict[str, Any] | None, context: str
    ) -> dict[str, Any]:
        """Send a request and return the JSON body (for endpoints whose new id the caller needs)."""
        ri = RequestInformation(method, url)
        ri.headers.add("Accept", "application/json")
        if body is not None:
            ri.headers.add("Content-Type", "application/json")
            ri.content = json.dumps(body).encode("utf-8")
        try:
            raw = await self.adapter.send_primitive_async(ri, "bytes", error_map=_ERROR_MAP)
        except ODataError as e:
            raise OutlookSourceError(f"{context} failed: {e}") from e
        return json.loads(raw) if raw else {}

    async def _get_json(self, url: str, context: str) -> dict[str, Any]:
        return await self._request_json(Method.GET, url, None, context)

    # -- mail folders -------------------------------------------------------

    @staticmethod
    def _mail_folder(display_name: str) -> MailFolder:
        folder = MailFolder()
        folder.display_name = display_name
        return folder

    async def create_mail_folder(self, user_id: str, display_name: str) -> Any:
        """POST /users/{id}/mailFolders — returns the created MailFolder (has ``.id``)."""
        resp = await self.outlook_ds.users_create_mail_folders(
            user_id, request_body=self._mail_folder(display_name)
        )
        return self._unwrap(resp, f"create_mail_folder({display_name})")

    async def create_child_folder(
        self, user_id: str, parent_folder_id: str, display_name: str
    ) -> Any:
        """POST /users/{id}/mailFolders/{parent}/childFolders — returns the created MailFolder."""
        resp = await self.outlook_ds.users_mail_folders_create_child_folders(
            user_id, parent_folder_id, request_body=self._mail_folder(display_name)
        )
        return self._unwrap(resp, f"create_child_folder({display_name})")

    async def delete_mail_folder(self, user_id: str, folder_id: str) -> None:
        await self._delete_no_content(
            f"{_GRAPH_V1}/users/{user_id}/mailFolders/{folder_id}",
            f"delete_mail_folder({folder_id})",
        )

    async def delete_child_folder(
        self, user_id: str, parent_folder_id: str, child_folder_id: str
    ) -> None:
        await self.delete_mail_folder(user_id, child_folder_id)

    async def permanent_delete_folder(self, user_id: str, folder_id: str) -> None:
        await self._post_no_content(
            f"{_GRAPH_V1}/users/{user_id}/mailFolders/{folder_id}/permanentDelete",
            {},
            f"permanent_delete_folder({folder_id})",
        )

    async def list_folders(self, user_id: str) -> list[Any]:
        """List top-level mail folders for a user (follows Graph nextLink)."""
        folders: list[Any] = []
        next_url: str | None = None
        while True:
            resp = await self.outlook_ds.users_list_mail_folders(
                user_id, next_url=next_url, top=100,
            )
            data = self._unwrap(resp, "list_folders")
            folders.extend(getattr(data, "value", []) or [])
            next_url = getattr(data, "odata_next_link", None)
            if not next_url:
                break
        return folders

    async def list_top_folders_created(self, user_id: str) -> list[tuple[str, str, datetime | None]]:
        """(id, displayName, created) of each top-level folder; created is PR_CREATION_TIME (0x3007).

        mailFolder has no created date of its own, so it is read as a MAPI extended property.
        """
        prop = "SystemTime 0x3007"
        url: str | None = (
            f"{_GRAPH_V1}/users/{user_id}/mailFolders?$top=100&$select=id%2CdisplayName"
            f"&$expand={quote(f'singleValueExtendedProperties($filter=id eq {chr(39)}{prop}{chr(39)})', safe='')}"
        )
        found: list[tuple[str, str, datetime | None]] = []
        while url:
            page = await self._get_json(url, "list_top_folders_created")
            for item in page.get("value", []):
                stamp = next((p.get("value") for p in item.get("singleValueExtendedProperties") or []
                              if p.get("id", "").lower() == prop.lower()), None)
                created = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
                found.append((item["id"], item.get("displayName") or "", created))
            url = page.get("@odata.nextLink")
        return found

    async def get_folder(self, user_id: str, folder_id: str) -> Any | None:
        """GET /users/{id}/mailFolders/{folder_id} — works for well-known ids like ``inbox``."""
        resp = await self.outlook_ds.users_get_mail_folders(user_id, folder_id)
        if resp is None or not getattr(resp, "success", False):
            return None
        return resp.data

    async def find_folder_by_name(self, user_id: str, display_name: str) -> Any | None:
        """Return a top-level folder whose displayName matches (case-insensitive), or None."""
        target = display_name.strip().lower()
        for folder in await self.list_folders(user_id):
            if (getattr(folder, "display_name", "") or "").strip().lower() == target:
                return folder
        return None

    async def folder_exists(self, user_id: str, folder_id: str) -> bool:
        return await self.get_folder(user_id, folder_id) is not None

    async def list_child_folders(self, user_id: str, parent_folder_id: str) -> list[Any]:
        """All child folders of a folder (follows nextLink; Graph pages childFolders at 10)."""
        url: str | None = f"{_GRAPH_V1}/users/{user_id}/mailFolders/{parent_folder_id}/childFolders?$top=100"
        folders: list[Any] = []
        while url:
            page = await self._get_json(url, "list_child_folders")
            folders.extend(_Obj(item) for item in page.get("value", []))
            url = page.get("@odata.nextLink")
        return folders

    async def child_folder_present(self, user_id: str, parent_id: str, child_id: str) -> bool:
        return any(getattr(folder, "id", None) == child_id for folder in await self.list_child_folders(user_id, parent_id))

    async def rename_folder(self, user_id: str, folder_id: str, display_name: str) -> None:
        await self._patch_json(
            f"{_GRAPH_V1}/users/{user_id}/mailFolders/{folder_id}",
            {"displayName": display_name},
            f"rename_folder({folder_id})",
        )

    async def move_folder(self, user_id: str, folder_id: str, destination_id: str) -> str:
        """POST .../mailFolders/{id}/move — returns the folder id after the move."""
        moved = await self._request_json(
            Method.POST,
            f"{_GRAPH_V1}/users/{user_id}/mailFolders/{folder_id}/move",
            {"destinationId": destination_id},
            f"move_folder({folder_id})",
        )
        return moved["id"]

    # -- messages -----------------------------------------------------------

    @staticmethod
    def _recipients(addresses: Iterable[str] | None) -> list[dict[str, Any]]:
        return [{"emailAddress": {"address": addr}} for addr in (addresses or [])]

    def _message_body(
        self,
        subject: str,
        body_html: str,
        to: Iterable[str] | None,
        cc: Iterable[str] | None = None,
        bcc: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
        }
        if to:
            body["toRecipients"] = self._recipients(to)
        if cc:
            body["ccRecipients"] = self._recipients(cc)
        if bcc:
            body["bccRecipients"] = self._recipients(bcc)
        return body

    async def create_message_in_folder(
        self,
        user_id: str,
        folder_id: str,
        subject: str,
        body_html: str,
        to: Iterable[str] | None = None,
        cc: Iterable[str] | None = None,
        bcc: Iterable[str] | None = None,
    ) -> Any:
        """POST /users/{id}/mailFolders/{folder}/messages — returns the created Message (a draft)."""
        resp = await self.outlook_ds.users_mail_folders_create_messages(
            user_id,
            folder_id,
            request_body=_dict_to_message(self._message_body(subject, body_html, to, cc, bcc)),
        )
        return self._unwrap(resp, f"create_message_in_folder({subject})")

    async def create_backdated_message(
        self, user_id: str, folder_id: str, subject: str, body_html: str, received: datetime
    ) -> str:
        """A non-draft message whose receivedDateTime is ``received`` (MAPI delivery time + flags).

        Graph has no writable receivedDateTime; PR_MESSAGE_DELIVERY_TIME (0x0E06) sets it and
        PR_MESSAGE_FLAGS (0x0E07) = 1 (read) clears the draft flag so it behaves like received mail.
        """
        body = self._message_body(subject, body_html, None)
        body["singleValueExtendedProperties"] = [
            {"id": "SystemTime 0x0E06", "value": received.strftime("%Y-%m-%dT%H:%M:%SZ")},
            {"id": "Integer 0x0E07", "value": "1"},
        ]
        created = await self._request_json(
            Method.POST,
            f"{_GRAPH_V1}/users/{user_id}/mailFolders/{folder_id}/messages",
            body,
            f"create_backdated_message({subject})",
        )
        return created["id"]

    async def send_draft(self, user_id: str, message_id: str) -> None:
        """POST /users/{id}/messages/{msg}/send — send an existing draft."""
        await self._post_no_content(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/send", {}, f"send_draft({message_id})"
        )

    async def send_mail(
        self,
        user_id: str,
        subject: str,
        body_html: str,
        to_recipients: Iterable[str],
        cc: Iterable[str] | None = None,
        attachments: Iterable[tuple[str, bytes, str]] = (),
    ) -> None:
        """POST /users/{id}/sendMail (204) — deliver a fresh message into recipient mailboxes."""
        message = self._message_body(subject, body_html, to_recipients, cc)
        if attachments:
            message["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": name,
                    "contentType": content_type,
                    "contentBytes": base64.b64encode(data).decode("ascii"),
                }
                for name, data, content_type in attachments
            ]
        await self._post_no_content(
            f"{_GRAPH_V1}/users/{user_id}/sendMail",
            {"message": message, "saveToSentItems": True},
            f"send_mail({subject})",
        )

    async def reply(self, user_id: str, message_id: str, comment: str) -> None:
        """POST .../messages/{id}/reply — the reply lands in the original sender's mailbox."""
        await self._post_no_content(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/reply",
            {"comment": comment},
            f"reply({message_id})",
        )

    async def reply_all(self, user_id: str, message_id: str, comment: str) -> None:
        """POST .../messages/{id}/replyAll — on a mail sent to a group, this posts into its conversation.

        Graph has no app-only way to post to a group conversation directly (403), but mail
        delivered to the group's address threads into the conversation it answers.
        """
        await self._post_no_content(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/replyAll",
            {"comment": comment},
            f"reply_all({message_id})",
        )

    async def move_message(self, user_id: str, message_id: str, destination_id: str) -> str:
        """POST .../messages/{id}/move — returns the message's id (immutable, so unchanged)."""
        moved = await self._request_json(
            Method.POST,
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/move",
            {"destinationId": destination_id},
            f"move_message({message_id})",
        )
        return moved["id"]

    async def permanent_delete_message(self, user_id: str, message_id: str) -> None:
        await self._post_no_content(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/permanentDelete",
            {},
            f"permanent_delete_message({message_id})",
        )

    async def find_messages_by_subject(
        self, user_id: str, subject: str, folder: str | None = None
    ) -> list[_Obj]:
        """Messages whose subject equals ``subject`` (optionally within one folder or well-known name)."""
        scope = f"mailFolders/{folder}/messages" if folder else "messages"
        escaped = subject.replace("'", "''")
        url: str | None = (
            f"{_GRAPH_V1}/users/{user_id}/{scope}"
            f"?$filter={quote(f'subject eq {chr(39)}{escaped}{chr(39)}', safe='')}"
            "&$select=id%2Csubject%2CparentFolderId%2CinternetMessageId%2CconversationId%2CconversationIndex%2CchangeKey"
        )
        found: list[_Obj] = []
        while url:
            page = await self._get_json(url, f"find_messages_by_subject({subject})")
            found.extend(_Obj(item) for item in page.get("value", []))
            url = page.get("@odata.nextLink")
        return found

    async def search_messages(self, user_id: str, text: str) -> list[_Obj]:
        """Messages in any folder whose subject matches ``text`` ($search; the caller re-checks the subject)."""
        url: str | None = (
            f"{_GRAPH_V1}/users/{user_id}/messages"
            f"?$search={quote(chr(34) + 'subject:' + text + chr(34), safe='')}&$select=id%2Csubject&$top=100"
        )
        found: list[_Obj] = []
        while url:
            page = await self._get_json(url, f"search_messages({text})")
            found.extend(_Obj(item) for item in page.get("value", []))
            url = page.get("@odata.nextLink")
        return found

    async def update_message(self, user_id: str, message_id: str, patch: dict[str, Any]) -> Any:
        """PATCH /users/{id}/messages/{msg} — bumps the message change_key (for reindex-updated tests)."""
        await self._patch_json(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}",
            patch,
            f"update_message({message_id})",
        )
        return await self.get_message(user_id, message_id)

    async def delete_message(self, user_id: str, message_id: str) -> None:
        """DELETE .../messages/{id} (raises on failure, unlike the SDK wrapper)."""
        await self._delete_no_content(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}", f"delete_message({message_id})"
        )

    async def get_message(self, user_id: str, message_id: str) -> Any | None:
        resp = await self.outlook_ds.users_get_messages(user_id, message_id)
        if resp is None or not getattr(resp, "success", False):
            return None
        return resp.data

    async def message_exists(self, user_id: str, message_id: str) -> bool:
        return await self.get_message(user_id, message_id) is not None

    async def list_folder_messages(self, user_id: str, folder_id: str) -> list[Any]:
        """List messages in a folder (single page — sufficient for test folders)."""
        resp = await self.outlook_ds.users_mail_folders_list_messages(user_id, folder_id)
        data = self._unwrap(resp, "list_folder_messages")
        return list(getattr(data, "value", []) or [])

    # -- attachments --------------------------------------------------------

    async def add_attachment(
        self,
        user_id: str,
        message_id: str,
        name: str,
        content_bytes: bytes,
        content_type: str,
    ) -> Any:
        """POST /users/{id}/messages/{msg}/attachments — returns the created FileAttachment."""
        body = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": content_type,
            "contentBytes": base64.b64encode(content_bytes).decode("ascii"),
        }
        resp = await self.outlook_ds.users_messages_create_attachments(
            user_id, message_id, request_body=_dict_to_attachment(body)
        )
        return self._unwrap(resp, f"add_attachment({name})")

    async def add_inline_image(
        self, user_id: str, message_id: str, name: str, content_bytes: bytes, content_id: str
    ) -> str:
        """An inline (``isInline``) image attachment referenced from the body by ``cid:``."""
        created = await self._request_json(
            Method.POST,
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/attachments",
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": name,
                "contentType": "image/png",
                "contentBytes": base64.b64encode(content_bytes).decode("ascii"),
                "isInline": True,
                "contentId": content_id,
            },
            f"add_inline_image({name})",
        )
        return created["id"]

    async def add_item_attachment(self, user_id: str, message_id: str, name: str, subject: str) -> str:
        """An attached Outlook message (``itemAttachment``) — has no contentBytes of its own."""
        created = await self._request_json(
            Method.POST,
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/attachments",
            {
                "@odata.type": "#microsoft.graph.itemAttachment",
                "name": name,
                "item": {
                    "@odata.type": "#microsoft.graph.message",
                    "subject": subject,
                    "body": {"contentType": "Text", "content": "attached item body"},
                },
            },
            f"add_item_attachment({name})",
        )
        return created["id"]

    async def add_large_attachment(
        self, user_id: str, message_id: str, name: str, content_bytes: bytes, content_type: str
    ) -> str:
        """A file attachment over the 3 MB inline limit, through an upload session; returns its id.

        The upload URL is pre-authorized (it must not get the Graph bearer token) and takes
        chunks in multiples of 320 KiB.
        """
        session = await self._request_json(
            Method.POST,
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/attachments/createUploadSession",
            {"AttachmentItem": {
                "attachmentType": "file", "name": name, "size": len(content_bytes), "contentType": content_type,
            }},
            f"create_upload_session({name})",
        )
        upload_url, total, chunk = session["uploadUrl"], len(content_bytes), 320 * 1024 * 10

        def _upload() -> None:
            for start in range(0, total, chunk):
                part = content_bytes[start:start + chunk]
                resp = requests.put(upload_url, data=part, timeout=120, headers={
                    "Content-Length": str(len(part)),
                    "Content-Range": f"bytes {start}-{start + len(part) - 1}/{total}",
                })
                if resp.status_code not in (200, 201):
                    raise OutlookSourceError(f"upload of {name} failed at byte {start}: HTTP {resp.status_code}")

        await asyncio.to_thread(_upload)
        found = [x for x in await self.list_attachments(user_id, message_id) if x.name == name]
        if not found:
            raise OutlookSourceError(f"uploaded attachment {name} not listed on {message_id}")
        return found[0].id

    async def delete_attachment(self, user_id: str, message_id: str, attachment_id: str) -> None:
        await self._delete_no_content(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/attachments/{attachment_id}",
            f"delete_attachment({attachment_id})",
        )

    async def list_attachments(self, user_id: str, message_id: str) -> list[Any]:
        """All attachments of a message, metadata only (follows nextLink)."""
        url: str | None = (
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/attachments"
            "?$select=id%2Cname%2CcontentType%2Csize%2CisInline%2ClastModifiedDateTime"
        )
        found: list[Any] = []
        while url:
            page = await self._get_json(url, "list_attachments")
            found.extend(_Obj(item) for item in page.get("value", []))
            url = page.get("@odata.nextLink")
        return found

    async def get_attachment_bytes(self, user_id: str, message_id: str, attachment_id: str) -> bytes:
        att = await self._get_json(
            f"{_GRAPH_V1}/users/{user_id}/messages/{message_id}/attachments/{attachment_id}",
            "get_attachment",
        )
        return base64.b64decode(att.get("contentBytes") or "")

    # -- group conversations -----------------------------------------------

    async def list_group_threads(self, group_id: str) -> list[_Obj]:
        url: str | None = f"{_GRAPH_V1}/groups/{group_id}/threads?$select=id%2Ctopic%2ClastDeliveredDateTime"
        found: list[_Obj] = []
        while url:
            page = await self._get_json(url, "list_group_threads")
            found.extend(_Obj(item) for item in page.get("value", []))
            url = page.get("@odata.nextLink")
        return found

    async def find_group_thread(self, group_id: str, topic: str) -> _Obj | None:
        for thread in await self.list_group_threads(group_id):
            if thread.topic == topic:
                return thread
        return None

    async def list_thread_posts(self, group_id: str, thread_id: str) -> list[_Obj]:
        url: str | None = (
            f"{_GRAPH_V1}/groups/{group_id}/threads/{thread_id}/posts"
            "?$select=id%2Cfrom%2CreceivedDateTime%2ChasAttachments%2CconversationThreadId"
        )
        found: list[_Obj] = []
        while url:
            page = await self._get_json(url, "list_thread_posts")
            found.extend(_Obj(item) for item in page.get("value", []))
            url = page.get("@odata.nextLink")
        return found

    async def list_post_attachments(self, group_id: str, thread_id: str, post_id: str) -> list[_Obj]:
        """A post's attachments via ``$expand`` (app-only tokens get 403 from .../attachments)."""
        post = await self._get_json(
            f"{_GRAPH_V1}/groups/{group_id}/threads/{thread_id}/posts/{post_id}?$expand=attachments",
            "list_post_attachments",
        )
        return [_Obj(item) for item in post.get("attachments", [])]

    async def delete_group_thread(self, group_id: str, thread_id: str) -> None:
        await self._delete_no_content(
            f"{_GRAPH_V1}/groups/{group_id}/threads/{thread_id}",
            f"delete_group_thread({thread_id})",
        )

    # -- groups / membership ------------------------------------------------

    async def get_group(self, group_id: str) -> Any:
        resp = await self.users_ds.groups_group_get_group(group_id)
        return self._unwrap(resp, f"get_group({group_id})")

    async def list_unified_groups(self) -> list[Group]:
        """Every mail-enabled Microsoft 365 (Unified) group, read the way the connector reads them.

        The unfiltered GET /groups list the connector pages lags a
        ``groupTypes/any(...)`` filtered list by a minute or more in both
        directions: a new group shows in the filtered list first, a deleted one
        leaves it first. Only this list tells a test what the next sync will see.
        """
        found: list[Group] = []
        next_url: str | None = None
        while True:
            resp = await self.users_ds.groups_list_groups(
                next_url=next_url, select=OutlookAPIFields.GROUP_SELECT_FIELDS,
            )
            page = self._unwrap(resp, "list_unified_groups")
            found.extend(
                g for g in page.value or []
                if g.group_types and "Unified" in g.group_types and g.mail_enabled is True
            )
            next_url = page.odata_next_link
            if not next_url:
                return found

    async def list_group_members(self, group_id: str) -> list[Any]:
        resp = await self.users_ds.groups_list_transitive_members(group_id)
        data = self._unwrap(resp, "list_group_members")
        return list(getattr(data, "value", []) or [])

    async def group_member_ids(self, group_id: str) -> set[str]:
        return {getattr(m, "id", None) for m in await self.list_group_members(group_id)}

    async def add_group_member(self, group_id: str, user_id: str) -> None:
        """POST /groups/{id}/members/$ref (204). Wrapper is unreliable; use adapter."""
        body = {"@odata.id": f"{_GRAPH_V1}/directoryObjects/{user_id}"}
        await self._post_no_content(
            f"{_GRAPH_V1}/groups/{group_id}/members/$ref", body,
            f"add_group_member({user_id})",
        )

    async def remove_group_member(self, group_id: str, user_id: str) -> None:
        """DELETE /groups/{id}/members/{user}/$ref (204)."""
        await self._delete_no_content(
            f"{_GRAPH_V1}/groups/{group_id}/members/{user_id}/$ref",
            f"remove_group_member({user_id})",
        )

    # -- users --------------------------------------------------------------

    async def get_user(self, user_id: str) -> Any:
        """GET /users filtered by object id or userPrincipalName."""
        escaped = user_id.replace("'", "''")
        filt = (
            f"userPrincipalName eq '{escaped}'" if "@" in user_id else f"id eq '{escaped}'"
        )
        resp = await self.users_ds.users_user_list_user(filter=filt)
        data = self._unwrap(resp, f"get_user({user_id})")
        values = list(getattr(data, "value", []) or [])
        if not values:
            raise OutlookSourceError(f"get_user({user_id}) returned no user")
        return values[0]

    # -- AAD user / group create (optional, slow — needs Directory.ReadWrite.All) ----------

    async def create_user(
        self, display_name: str, user_principal_name: str, mail_nickname: str, password: str
    ) -> Any:
        """POST /users — create an AAD user (no license/mailbox). Returns the created user."""
        user = User()
        user.account_enabled = True
        user.display_name = display_name
        user.mail_nickname = mail_nickname
        user.user_principal_name = user_principal_name
        profile = PasswordProfile()
        profile.force_change_password_next_sign_in = True
        profile.password = password
        user.password_profile = profile
        resp = await self.users_ds.users_user_create_user(request_body=user)
        return self._unwrap(resp, f"create_user({user_principal_name})")

    async def delete_user(self, user_id: str) -> None:
        """DELETE /users/{id} (needs User.ReadWrite.All; Directory.ReadWrite.All alone is refused)."""
        await self._delete_no_content(f"{_GRAPH_V1}/users/{user_id}", f"delete_user({user_id})")

    async def set_account_enabled(self, user_id: str, enabled: bool) -> None:
        """PATCH /users/{id} accountEnabled (needs User.ReadWrite.All)."""
        await self.update_user(user_id, {"accountEnabled": enabled})

    async def update_user(self, user_id: str, patch: dict[str, Any]) -> None:
        """PATCH /users/{id} (needs User.ReadWrite.All)."""
        await self._patch_json(f"{_GRAPH_V1}/users/{user_id}", patch, f"update_user({user_id})")

    async def purge_deleted_directory_object(self, object_id: str) -> None:
        """DELETE /directory/deletedItems/{id}: a deleted user or group is gone for good, not soft-deleted."""
        await self._delete_no_content(
            f"{_GRAPH_V1}/directory/deletedItems/{object_id}", f"purge_deleted_directory_object({object_id})"
        )

    async def account_enabled(self, user_id: str) -> bool | None:
        user = await self._get_json(f"{_GRAPH_V1}/users/{user_id}?$select=accountEnabled", "account_enabled")
        return user.get("accountEnabled")

    async def create_group(self, display_name: str, mail_nickname: str) -> Any:
        """POST /groups — create a mail-enabled M365 (Unified) group. Returns the created group."""
        group = Group()
        group.display_name = display_name
        group.mail_nickname = mail_nickname
        group.mail_enabled = True
        group.security_enabled = False
        group.group_types = ["Unified"]
        resp = await self.users_ds.groups_group_create_group(request_body=group)
        return self._unwrap(resp, f"create_group({display_name})")

    async def delete_group(self, group_id: str) -> None:
        """DELETE /groups/{id} (no wrapper — use the generic adapter)."""
        await self._delete_no_content(
            f"{_GRAPH_V1}/groups/{group_id}", f"delete_group({group_id})"
        )
