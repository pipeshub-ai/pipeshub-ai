# pyright: ignore-file

"""Seeds a Nextcloud instance with files for the Nextcloud connector to sync.

Nextcloud speaks WebDAV, so seeding is plain HTTP: PUT to upload, MKCOL to make
a folder, PROPFIND to list. That avoids adding a Nextcloud client library to the
integration-test environment for what amounts to four verbs.

The connector authenticates with the same username and password, so a container
started with NEXTCLOUD_ADMIN_USER/NEXTCLOUD_ADMIN_PASSWORD needs no further
setup — no app password to mint, no token to bootstrap.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Sequence, Tuple

import requests

# (relative path, text) pairs.
SeedFile = Tuple[str, str]


class NextcloudSourceHelper:
    """Creates and removes the files a connector test syncs from."""

    def __init__(
        self, base_url: str, username: str, password: str, root: str = "pipeshub-test"
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.auth = (username, password)
        self.root = root

    def _dav(self, path: str = "") -> str:
        suffix = f"/{path.lstrip('/')}" if path else ""
        return f"{self.base_url}/remote.php/dav/files/{self.username}/{self.root}{suffix}"

    def ping(self, timeout: int = 15) -> None:
        """Raise unless WebDAV answers for this user."""
        resp = requests.request(
            "PROPFIND",
            f"{self.base_url}/remote.php/dav/files/{self.username}/",
            auth=self.auth,
            headers={"Depth": "0"},
            timeout=timeout,
        )
        if resp.status_code != 207:
            raise RuntimeError(
                f"Nextcloud WebDAV returned {resp.status_code} for "
                f"{self.username}: {resp.text[:200]}"
            )

    def ensure_root(self, timeout: int = 15) -> None:
        # MKCOL answers 405 when the collection already exists, which is fine.
        resp = requests.request("MKCOL", self._dav(), auth=self.auth, timeout=timeout)
        if resp.status_code not in (201, 405):
            raise RuntimeError(
                f"could not create {self.root}: {resp.status_code} {resp.text[:200]}"
            )

    def write_files(self, files: Sequence[SeedFile], timeout: int = 30) -> int:
        for rel_path, text in files:
            parts = rel_path.split("/")
            for depth in range(1, len(parts)):
                requests.request(
                    "MKCOL",
                    self._dav("/".join(parts[:depth])),
                    auth=self.auth,
                    timeout=timeout,
                )
            resp = requests.put(
                self._dav(rel_path),
                auth=self.auth,
                data=text.encode("utf-8"),
                timeout=timeout,
            )
            if resp.status_code not in (201, 204):
                raise RuntimeError(
                    f"upload of {rel_path} failed: {resp.status_code} {resp.text[:200]}"
                )
        return len(files)

    def overwrite_file(self, rel_path: str, text: str, timeout: int = 30) -> None:
        resp = requests.put(
            self._dav(rel_path), auth=self.auth, data=text.encode("utf-8"), timeout=timeout
        )
        if resp.status_code not in (201, 204):
            raise RuntimeError(
                f"overwrite of {rel_path} failed: {resp.status_code} {resp.text[:200]}"
            )

    def list_objects(self, resource_name: str | None = None, timeout: int = 30) -> List[str]:
        """Paths of every file under the test root, relative to it."""
        del resource_name
        resp = requests.request(
            "PROPFIND", self._dav(), auth=self.auth, headers={"Depth": "infinity"},
            timeout=timeout,
        )
        if resp.status_code == 404:
            return []
        if resp.status_code != 207:
            raise RuntimeError(f"PROPFIND failed: {resp.status_code} {resp.text[:200]}")

        prefix = f"/remote.php/dav/files/{self.username}/{self.root}/"
        found: List[str] = []
        for href in ET.fromstring(resp.content).iter(
            "{DAV:}href"
        ):
            path = (href.text or "")
            if not path.startswith(prefix) or path.endswith("/"):
                continue
            found.append(path[len(prefix):])
        return sorted(found)

    def clear_objects(self, resource_name: str | None = None, timeout: int = 30) -> None:
        """Delete the test root and recreate it empty.

        Named for the protocol the shared destructor expects.
        """
        del resource_name
        requests.delete(self._dav(), auth=self.auth, timeout=timeout)
        self.ensure_root(timeout=timeout)
