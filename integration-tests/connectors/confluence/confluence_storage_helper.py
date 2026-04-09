"""Confluence Cloud API wrapper for integration tests."""

from __future__ import annotations

import base64
import logging
from html import escape
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urljoin

import requests


logger = logging.getLogger("confluence-storage-helper")


class ConfluenceStorageHelper:
    """Wrapper around Confluence Cloud REST API for integration test operations."""

    def __init__(self, base_url: str, email: str, api_token: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.timeout = timeout

        credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _v1_url(self, endpoint: str) -> str:
        return f"{self.base_url}/wiki/rest/api{endpoint}"

    def _v2_url(self, endpoint: str) -> str:
        return f"{self.base_url}/wiki/api/v2{endpoint}"

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        resp = self.session.request(method, url, **kwargs)
        return resp

    def _normalize_space_key(self, space_key: str) -> str:
        cleaned = "".join(ch for ch in space_key.upper() if ch.isalnum())[:10]
        if not cleaned:
            raise ValueError("space_key must contain at least one alphanumeric character")
        return cleaned

    def _resolve_next_url(self, next_ref: str | None) -> str | None:
        if not next_ref:
            return None
        if next_ref.startswith("http://") or next_ref.startswith("https://"):
            return next_ref
        return urljoin(self.base_url, next_ref)

    def _text_to_storage(self, text: str) -> str:
        safe = escape(text[:5000])
        lines = safe.splitlines() or [safe]
        paragraphs = [f"<p>{line if line else '&#160;'}</p>" for line in lines]
        return "".join(paragraphs) if paragraphs else "<p></p>"

    def get_space(self, space_key: str, expand: str | None = None) -> Dict[str, Any]:
        valid_space_key = self._normalize_space_key(space_key)
        params = {"expand": expand} if expand else None
        resp = self._request("GET", self._v1_url(f"/space/{valid_space_key}"), params=params)
        resp.raise_for_status()
        return resp.json()

    def _get_space_id(self, space_key: str) -> str:
        space = self.get_space(space_key)
        space_id = space.get("id")
        if space_id is None:
            raise KeyError(f"Space ID missing from response for space {space_key}")
        return str(space_id)

    def create_space(self, space_key: str, space_name: str | None = None) -> Dict[str, Any]:
        valid_space_key = self._normalize_space_key(space_key)

        try:
            existing = self.get_space(valid_space_key)
            logger.info("Space %s already exists, reusing it", valid_space_key)
            return existing
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise

        if space_name is None:
            space_name = space_key

        payload = {
            "key": valid_space_key,
            "name": space_name,
            "description": {
                "plain": {
                    "value": "Integration test space",
                    "representation": "plain",
                }
            },
        }

        resp = self._request("POST", self._v1_url("/space"), json=payload)
        if resp.status_code == 409:
            logger.info("Space %s already exists (conflict)", valid_space_key)
            return self.get_space(valid_space_key)
        resp.raise_for_status()
        return resp.json()

    def _list_pages_in_space(
        self,
        space_id: str,
        *,
        depth: str | None = None,
        title: str | None = None,
        body_format: str | None = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        url = self._v2_url(f"/spaces/{space_id}/pages")
        params: Dict[str, Any] | None = {"limit": limit}
        if depth:
            params["depth"] = depth
        if title:
            params["title"] = title
        if body_format:
            params["body-format"] = body_format

        results: List[Dict[str, Any]] = []

        while url:
            resp = self._request("GET", url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            url = self._resolve_next_url(data.get("_links", {}).get("next"))
            params = None

        return results

    def list_objects(self, space_key: str) -> List[str]:
        space_id = self._get_space_id(space_key)
        pages = self._list_pages_in_space(space_id, limit=100)
        return [str(page["id"]) for page in pages]

    def _find_page_by_title(
        self,
        space_id: str,
        title: str,
        parent_id: str | None = None,
    ) -> Dict[str, Any] | None:
        url = self._v2_url("/pages")
        params: Dict[str, Any] | None = {
            "space-id": space_id,
            "title": title,
            "status": "current",
            "body-format": "storage",
            "limit": 100,
        }

        candidates: List[Dict[str, Any]] = []

        while url:
            resp = self._request("GET", url, params=params)
            resp.raise_for_status()
            data = resp.json()
            candidates.extend(data.get("results", []))
            url = self._resolve_next_url(data.get("_links", {}).get("next"))
            params = None

        exact = [p for p in candidates if p.get("title") == title]
        if parent_id is not None:
            exact = [p for p in exact if str(p.get("parentId")) == str(parent_id)]

        return exact[0] if exact else None

    def upload_directory(self, space_key: str, root: Path) -> int:
        count = 0

        for file_path in root.rglob("*"):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue

            title = file_path.stem
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            storage_body = self._text_to_storage(content)

            self.create_page(space_key, title, storage_body)
            count += 1

        return count

    def create_page(
        self,
        space_key: str,
        title: str,
        body: str,
        parent_id: str | None = None,
    ) -> Dict[str, Any]:
        valid_space_key = self._normalize_space_key(space_key)
        space_id = self._get_space_id(valid_space_key)

        existing = self._find_page_by_title(space_id, title, parent_id)
        if existing:
            return self.update_page(
                page_id=str(existing["id"]),
                title=title,
                body=body,
                version=int(existing["version"]["number"]),
            )

        payload: Dict[str, Any] = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": body,
            },
        }
        params: Dict[str, Any] = {}

        if parent_id:
            payload["parentId"] = str(parent_id)
        else:
            params["root-level"] = "true"

        resp = self._request("POST", self._v2_url("/pages"), json=payload, params=params)
        if resp.status_code in (400, 409):
            existing = self._find_page_by_title(space_id, title, parent_id)
            if existing:
                return self.update_page(
                    page_id=str(existing["id"]),
                    title=title,
                    body=body,
                    version=int(existing["version"]["number"]),
                )

        resp.raise_for_status()
        return resp.json()

    def update_page(
        self,
        page_id: str,
        title: str,
        body: str,
        version: int,
        *,
        parent_id: str | None = None,
        space_id: str | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": str(page_id),
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": body,
            },
            "version": {
                "number": int(version) + 1,
            },
        }

        if parent_id is not None:
            payload["parentId"] = str(parent_id)
        if space_id is not None:
            payload["spaceId"] = str(space_id)

        resp = self._request("PUT", self._v2_url(f"/pages/{page_id}"), json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_page(self, page_id: str) -> Dict[str, Any]:
        resp = self._request(
            "GET",
            self._v2_url(f"/pages/{page_id}"),
            params={
                "body-format": "storage",
                "include-version": "true",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def rename_object(self, space_key: str, page_id: str, new_title: str) -> Dict[str, Any]:
        payload = {
            "status": "current",
            "title": new_title,
        }
        resp = self._request("PUT", self._v2_url(f"/pages/{page_id}/title"), json=payload)
        resp.raise_for_status()
        return resp.json()

    def move_object(self, space_key: str, page_id: str, new_parent_id: str) -> Dict[str, Any]:
        resp = self._request(
            "PUT",
            self._v1_url(f"/content/{page_id}/move/append/{new_parent_id}"),
        )
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return {}

    def overwrite_object(self, page_id: str, new_content: str) -> Dict[str, Any]:
        page = self.get_page(page_id)
        return self.update_page(
            page_id=page_id,
            title=page["title"],
            body=self._text_to_storage(new_content),
            version=int(page["version"]["number"]),
        )

    def _delete_page(self, page_id: str, purge: bool = False) -> None:
        params = {"purge": "true"} if purge else None
        resp = self._request("DELETE", self._v2_url(f"/pages/{page_id}"), params=params)
        if resp.status_code not in (200, 202, 204, 404):
            resp.raise_for_status()

    def clear_objects(self, space_key: str) -> None:
        space_id = self._get_space_id(space_key)
        pages = self._list_pages_in_space(space_id, limit=250)

        if not pages:
            return

        parent_map = {str(p["id"]): str(p.get("parentId")) for p in pages if p.get("id") is not None}
        memo: Dict[str, int] = {}

        def depth(page_id: str) -> int:
            if page_id in memo:
                return memo[page_id]
            parent_id = parent_map.get(page_id)
            if not parent_id or parent_id not in parent_map:
                memo[page_id] = 0
            else:
                memo[page_id] = depth(parent_id) + 1
            return memo[page_id]

        ordered_ids = sorted(parent_map.keys(), key=depth, reverse=True)

        for page_id in ordered_ids:
            try:
                self._delete_page(page_id, purge=False)
            except Exception as exc:
                logger.warning("Error deleting page %s: %s", page_id, exc)

        for page_id in ordered_ids:
            try:
                self._delete_page(page_id, purge=True)
            except Exception as exc:
                logger.warning("Error purging page %s: %s", page_id, exc)

    def delete_space(self, space_key: str) -> None:
        valid_space_key = self._normalize_space_key(space_key)
        resp = self._request("DELETE", self._v1_url(f"/space/{valid_space_key}"))
        if resp.status_code not in (200, 202, 204, 404):
            resp.raise_for_status()