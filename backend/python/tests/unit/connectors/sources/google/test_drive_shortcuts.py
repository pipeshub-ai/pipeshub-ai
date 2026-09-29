"""Shortcut expansion stops on cycles and does not store the shortcut itself."""

import logging

from app.connectors.sources.google.drive.utils.drive_shortcuts import (
    materialize_drive_shortcuts,
)

SHORTCUT = "application/vnd.google-apps.shortcut"
FOLDER = "application/vnd.google-apps.folder"
LOGGER = logging.getLogger("drive-shortcuts")


def _shortcut(file_id: str, target_id: str, target_mime: str) -> dict:
    return {
        "id": file_id,
        "name": file_id,
        "mimeType": SHORTCUT,
        "shortcutDetails": {"targetId": target_id, "targetMimeType": target_mime},
    }


async def test_a_file_shortcut_is_replaced_by_its_target_once() -> None:
    target = {"id": "doc", "name": "Notes", "mimeType": "application/vnd.google-apps.document"}
    fetched: list[str] = []

    async def get_metadata(file_id: str) -> dict:
        fetched.append(file_id)
        return target

    async def list_children(_folder_id: str, _seen: set[str]) -> list[dict]:
        raise AssertionError("a file shortcut should not list children")

    items = [
        _shortcut("link", "doc", target["mimeType"]),
        target,
    ]
    result = await materialize_drive_shortcuts(
        items,
        get_metadata=get_metadata,
        list_children=list_children,
        seen=set(),
        cache={},
        logger=LOGGER,
    )

    assert [item["id"] for item in result] == ["doc"]
    assert fetched == ["doc"]


async def test_a_folder_shortcut_cycle_terminates() -> None:
    folder = {"id": "folder-a", "name": "A", "mimeType": FOLDER}
    back = _shortcut("back", "into", FOLDER)
    into = _shortcut("into", "folder-a", FOLDER)

    async def get_metadata(file_id: str) -> dict:
        return {"into": into, "folder-a": folder, "back": back}[file_id]

    async def list_children(folder_id: str, seen: set[str]) -> list[dict]:
        children = [back] if folder_id == "folder-a" else []
        return [child for child in children if child["id"] not in seen]

    result = await materialize_drive_shortcuts(
        [into],
        get_metadata=get_metadata,
        list_children=list_children,
        seen=set(),
        cache={},
        logger=LOGGER,
    )

    assert [item["id"] for item in result] == ["folder-a"]
