"""A folder delete removes that folder's contents and nothing else.

Two routes delete by folder. ``DELETE /{kb}/folder/{folder}`` removes the folder
with everything beneath it. ``DELETE /{kb}/folder/{folder}/records`` removes the
listed records from that folder; it used to check only that the folder existed
and then deleted whatever ids it was sent anywhere in the knowledge base, so a
record in a sibling folder or at the root could be deleted through another
folder's route.

The second route lives on the connector service only (the gateway does not
forward it), so these tests call the connector service directly.

The tree built here::

    kb/
      root-record
      folder-a/
        a-record
        sub/
          sub-record
      folder-b/
        b-record
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, AsyncGenerator
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio
import requests

from helper.clients.kb_client import KBClient
from helper.stored_names import stored_name

logger = logging.getLogger("cleanup-folder-scope")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]

CONTENT = b"# Scope check\n\nA file that only exists to be deleted, or not.\n"


def _connector_service_url(base_url: str) -> str:
    """The connector service beside the gateway; the integration stack publishes 8088."""
    explicit = os.getenv("PIPESHUB_CONNECTOR_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(netloc=f"{parsed.hostname}:8088"))


def _folder_id(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("folder") or {}, payload.get("data") or {}):
        if isinstance(container, dict):
            for key in ("id", "folderId", "_key"):
                if container.get(key):
                    return str(container[key])
    raise AssertionError(f"No folder id in the create response: {payload}")


def _upload(kb_client: KBClient, kb_id: str, name: str, folder_id: str | None = None) -> str:
    upload = kb_client.upload_file(
        kb_id, name, CONTENT + name.encode(), folder_id=folder_id, mimetype="text/markdown"
    )
    assert upload["summary"]["failed"] == 0, f"Upload of {name} failed: {upload}"
    return str(upload["records"][0]["recordId"])


@pytest_asyncio.fixture(loop_scope="session")
async def folder_tree(kb_client: KBClient) -> AsyncGenerator[dict[str, Any], None]:
    tag = uuid.uuid4().hex[:6]
    kb_id = kb_client.create_kb(f"cleanup-scope-{tag}")["id"]
    try:
        names = {
            "folder_a": f"folder-a-{tag}",
            "sub_folder": f"sub-{tag}",
            "folder_b": f"folder-b-{tag}",
            "root": f"root-record-{tag}.md",
            "a": f"a-record-{tag}.md",
            "sub": f"sub-record-{tag}.md",
            "b": f"b-record-{tag}.md",
        }
        folder_a = _folder_id(kb_client.create_folder(kb_id, names["folder_a"]))
        sub = _folder_id(kb_client.create_folder(kb_id, names["sub_folder"], parent_id=folder_a))
        folder_b = _folder_id(kb_client.create_folder(kb_id, names["folder_b"]))
        yield {
            "kb_id": kb_id,
            "folder_a": folder_a,
            "sub": sub,
            "folder_b": folder_b,
            "names": names,
            "root": _upload(kb_client, kb_id, names["root"]),
            "a": _upload(kb_client, kb_id, names["a"], folder_a),
            "sub_record": _upload(kb_client, kb_id, names["sub"], sub),
            "b": _upload(kb_client, kb_id, names["b"], folder_b),
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


def _record_status(pipeshub_client, record_id: str) -> int:
    pipeshub_client._ensure_access_token()
    return requests.get(
        f"{pipeshub_client.base_url}/api/v1/knowledgeBase/record/{record_id}",
        headers={"Authorization": f"Bearer {pipeshub_client._access_token}"},
        timeout=30,
    ).status_code


def _delete_from_folder(pipeshub_client, kb_id: str, folder_id: str, record_ids: list[str]):
    pipeshub_client._ensure_access_token()
    url = (
        f"{_connector_service_url(pipeshub_client.base_url)}"
        f"/api/v1/kb/{kb_id}/folder/{folder_id}/records"
    )
    try:
        return requests.delete(
            url,
            json={"recordIds": record_ids},
            headers={"Authorization": f"Bearer {pipeshub_client._access_token}"},
            timeout=60,
        )
    except requests.ConnectionError as exc:
        raise AssertionError(
            f"Could not reach the connector service at {url}. Set "
            "PIPESHUB_CONNECTOR_URL if it is not on port 8088 of the gateway's host."
        ) from exc


def _assert_status(pipeshub_client, record_ids: dict[str, str], expected: int, why: str) -> None:
    wrong = {
        label: status
        for label, rid in record_ids.items()
        if (status := _record_status(pipeshub_client, rid)) != expected
    }
    assert not wrong, f"{why} Expected HTTP {expected} for each, got {wrong}."


class TestTheFolderRecordsRoute:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_records_outside_the_folder_are_refused_and_kept(
        self, folder_tree, pipeshub_client
    ) -> None:
        t = folder_tree
        response = _delete_from_folder(
            pipeshub_client, t["kb_id"], t["folder_a"], [t["b"], t["root"]]
        )

        assert response.status_code == 404, (
            f"Deleting a sibling folder's record and a root record through folder A's "
            f"route answered HTTP {response.status_code}: {response.text[:300]}"
        )
        _assert_status(
            pipeshub_client, {"b-record": t["b"], "root-record": t["root"]}, 200,
            "Records outside folder A were deleted through folder A's route.",
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_mixed_list_deletes_only_the_ones_inside(
        self, folder_tree, pipeshub_client
    ) -> None:
        t = folder_tree
        response = _delete_from_folder(
            pipeshub_client, t["kb_id"], t["folder_a"], [t["a"], t["sub_record"], t["b"]]
        )

        assert response.status_code == 200, (
            f"HTTP {response.status_code}: {response.text[:300]}"
        )
        _assert_status(
            pipeshub_client, {"a-record": t["a"], "sub-record": t["sub_record"]}, 404,
            "Records inside folder A (directly and in its sub-folder) should be gone.",
        )
        _assert_status(
            pipeshub_client, {"b-record": t["b"], "root-record": t["root"]}, 200,
            "A record outside folder A was deleted with the ones inside it.",
        )


class TestDeletingAFolderWithASubFolder:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_sub_folder_goes_and_the_siblings_stay(
        self, folder_tree, kb_client, pipeshub_client, graph_provider
    ) -> None:
        t = folder_tree
        kb_client.delete_folder(t["kb_id"], t["folder_a"])

        _assert_status(
            pipeshub_client, {"a-record": t["a"], "sub-record": t["sub_record"]}, 404,
            "Records under the deleted folder, including its sub-folder, are still there.",
        )
        _assert_status(
            pipeshub_client, {"b-record": t["b"], "root-record": t["root"]}, 200,
            "Deleting folder A removed records that were never in it.",
        )

        # The graph holds an uploaded file under its stored name, without the extension.
        names = {label: stored_name(name) for label, name in t["names"].items()}
        for label in ("folder_a", "a", "sub_folder", "sub"):
            assert await graph_provider.get_record_by_name(t["kb_id"], names[label]) is None, (
                f"{names[label]} is still in the graph after its folder was deleted."
            )
        for label in ("folder_b", "b", "root"):
            assert await graph_provider.get_record_by_name(t["kb_id"], names[label]) is not None, (
                f"{names[label]} left the graph although it was outside the deleted folder."
            )
