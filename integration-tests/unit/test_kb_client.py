"""Tests for KBClient folder creation behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from helper.clients.kb_client import KBClient

pytestmark = pytest.mark.unit


def test_create_folder_root_endpoint() -> None:
    """Root folder creation targets /{kb_id}/folder without a parentId."""
    mock_http_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "f1", "name": "Docs"}
    mock_resp.raise_for_status = MagicMock()
    mock_http_client.request.return_value = mock_resp

    client = KBClient(mock_http_client)
    res = client.create_folder("kb-123", "Docs")

    assert res == {"id": "f1", "name": "Docs"}
    mock_http_client.request.assert_called_once_with(
        "POST",
        "/api/v1/knowledgeBase/kb-123/folder",
        auth=True,
        json={"folderName": "Docs"},
    )


def test_create_folder_subfolder_endpoint() -> None:
    """Nested folder creation targets /{kb_id}/folder/{parent_id}/subfolder."""
    mock_http_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "f2", "name": "SubDocs"}
    mock_resp.raise_for_status = MagicMock()
    mock_http_client.request.return_value = mock_resp

    client = KBClient(mock_http_client)
    res = client.create_folder("kb-123", "SubDocs", parent_id="f1")

    assert res == {"id": "f2", "name": "SubDocs"}
    mock_http_client.request.assert_called_once_with(
        "POST",
        "/api/v1/knowledgeBase/kb-123/folder/f1/subfolder",
        auth=True,
        json={"folderName": "SubDocs"},
    )


def test_create_folder_subfolder_endpoint_encodes_parent_id() -> None:
    """Nested folder creation URL-encodes special characters in parent_id."""
    mock_http_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "f3", "name": "Nested"}
    mock_resp.raise_for_status = MagicMock()
    mock_http_client.request.return_value = mock_resp

    client = KBClient(mock_http_client)
    res = client.create_folder("kb-123", "Nested", parent_id="folder/sub#1")

    assert res == {"id": "f3", "name": "Nested"}
    mock_http_client.request.assert_called_once_with(
        "POST",
        "/api/v1/knowledgeBase/kb-123/folder/folder%2Fsub%231/subfolder",
        auth=True,
        json={"folderName": "Nested"},
    )


def test_create_folder_empty_parent_id_raises_value_error() -> None:
    """Empty string parent_id is rejected and does not create a root folder."""
    mock_http_client = MagicMock()
    client = KBClient(mock_http_client)

    with pytest.raises(ValueError, match="parent_id cannot be"):
        client.create_folder("kb-123", "Docs", parent_id="")

    mock_http_client.request.assert_not_called()


def test_create_folder_legacy_query_param() -> None:
    """Legacy folder nesting targets /{kb_id}/folder with ?folderId= query param."""
    mock_http_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "f4", "name": "LegacySub"}
    mock_resp.raise_for_status = MagicMock()
    mock_http_client.request.return_value = mock_resp

    client = KBClient(mock_http_client)
    res = client.create_folder(
        "kb-123", "LegacySub", parent_id="f1", use_subfolder_route=False
    )

    assert res == {"id": "f4", "name": "LegacySub"}
    mock_http_client.request.assert_called_once_with(
        "POST",
        "/api/v1/knowledgeBase/kb-123/folder",
        auth=True,
        json={"folderName": "LegacySub"},
        params={"folderId": "f1"},
    )



