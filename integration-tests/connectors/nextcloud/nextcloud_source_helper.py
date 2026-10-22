"""Seeds and inspects the ``nextcloud-source`` service the Nextcloud connector syncs from.

The integration stack runs Nextcloud with an admin account. Tests sync a
separate, ordinary user that this helper creates through the provisioning API:
the installer already signed the admin in, so the admin has Nextcloud's sample
files, while a user created after the ``post-installation`` hook turned sample
files off starts empty. Files are written over WebDAV, as a desktop client would.

Everything a run writes lives under one top-level folder, so teardown removes
exactly that folder.
"""

from __future__ import annotations

import os
import posixpath
import subprocess
from urllib.parse import quote
from xml.etree import ElementTree

import requests

_DAV = "{DAV:}"


class NextcloudSourceHelper:
    def __init__(
        self,
        base_url: str,
        admin_user: str,
        admin_password: str,
        user: str,
        password: str,
        email: str,
        timeout: float = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin = (admin_user, admin_password)
        self.user = user
        self.password = password
        self.email = email
        self.timeout = timeout

    # -- account -----------------------------------------------------------

    def _ocs(self, method: str, path: str, auth: tuple[str, str], **data: str) -> dict:
        response = requests.request(
            method,
            f"{self.base_url}/ocs/v1.php/cloud/{path}",
            params={"format": "json"},
            data=data or None,
            auth=auth,
            headers={"OCS-APIRequest": "true"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["ocs"]

    def ping(self) -> None:
        response = requests.get(f"{self.base_url}/status.php", timeout=self.timeout)
        response.raise_for_status()
        if not response.json().get("installed"):
            raise RuntimeError("Nextcloud is not installed yet")

    def set_email(self, email: str) -> None:
        """Change the sync user's profile email, as the user may in Personal settings."""
        self._ocs("PUT", f"users/{quote(self.user)}", self.admin, key="email", value=email)

    def ensure_other_user(self, user: str, password: str) -> None:
        """A second account, to share files with the sync user."""
        found = self._ocs("GET", f"users?search={quote(user)}", self.admin)
        if user not in (found.get("data") or {}).get("users", []):
            meta = self._ocs("POST", "users", self.admin, userid=user, password=password)["meta"]
            if meta.get("statuscode") != 100:
                raise RuntimeError(f"Could not create Nextcloud user {user}: {meta}")

    def delete_user(self, user: str) -> None:
        requests.delete(
            f"{self.base_url}/ocs/v1.php/cloud/users/{quote(user)}",
            params={"format": "json"}, auth=self.admin, headers={"OCS-APIRequest": "true"}, timeout=self.timeout,
        )

    def share_with_sync_user(self, owner: tuple[str, str], path: str) -> str:
        """``owner`` shares their ``path`` with the sync user; returns the share id."""
        response = requests.post(
            f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            params={"format": "json"},
            data={"path": f"/{path.strip('/')}", "shareType": "0", "shareWith": self.user},
            auth=owner, headers={"OCS-APIRequest": "true"}, timeout=self.timeout,
        )
        response.raise_for_status()
        return str(response.json()["ocs"]["data"]["id"])

    def unshare(self, owner: tuple[str, str], share_id: str) -> None:
        requests.delete(
            f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}",
            params={"format": "json"}, auth=owner, headers={"OCS-APIRequest": "true"}, timeout=self.timeout,
        ).raise_for_status()

    def occ(self, *args: str) -> str:
        """Run ``occ`` on the server: the ``NEXTCLOUD_CONTAINER`` container, or the stack's nextcloud-source.

        Raises ``ComposeUnavailable`` when neither can be reached, which tests turn into a skip.
        """
        from helper.compose_control import ComposeStack, ComposeUnavailable

        container = os.getenv("NEXTCLOUD_CONTAINER")
        if not container:
            container = ComposeStack.from_env().container_id("nextcloud-source")
        try:
            done = subprocess.run(
                ["docker", "exec", "-u", "www-data", container, "php", "occ", *args],
                capture_output=True, text=True, timeout=120, check=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise ComposeUnavailable(f"could not run occ in {container}: {e}") from e
        return done.stdout

    def ensure_user(self) -> None:
        """Create the sync user if needed, and give it the configured email."""
        found = self._ocs("GET", f"users?search={quote(self.user)}", self.admin)
        if self.user not in (found.get("data") or {}).get("users", []):
            meta = self._ocs(
                "POST", "users", self.admin, userid=self.user, password=self.password
            )["meta"]
            if meta.get("statuscode") != 100:
                raise RuntimeError(f"Could not create Nextcloud user {self.user}: {meta}")
        # The connector owns records by this email, so it must be the account's.
        self._ocs("PUT", f"users/{quote(self.user)}", self.admin, key="email", value=self.email)

    # -- files -------------------------------------------------------------

    def _url(self, path: str, user: str | None = None) -> str:
        return f"{self.base_url}/remote.php/dav/files/{quote(user or self.user)}/{quote(path.strip('/'))}"

    def _dav(self, method: str, path: str, **kwargs: object) -> requests.Response:
        return requests.request(
            method,
            self._url(path),
            auth=(self.user, self.password),
            timeout=self.timeout,
            **kwargs,
        )

    def mkdir(self, path: str) -> None:
        """Create ``path`` and any missing parents."""
        parts = [p for p in path.strip("/").split("/") if p]
        for i in range(1, len(parts) + 1):
            response = self._dav("MKCOL", "/".join(parts[:i]))
            if response.status_code not in (201, 405):  # 405: it already exists
                response.raise_for_status()

    def put(self, path: str, content: str, auth: tuple[str, str] | None = None) -> None:
        """Write ``path`` as the sync user, or as ``auth``'s user (folders above it too)."""
        user, password = auth or (self.user, self.password)
        parts = [p for p in posixpath.dirname(path.strip("/")).split("/") if p]
        for i in range(1, len(parts) + 1):
            response = requests.request(
                "MKCOL", self._url("/".join(parts[:i]), user), auth=(user, password), timeout=self.timeout
            )
            if response.status_code not in (201, 405):  # 405: it already exists
                response.raise_for_status()
        requests.put(
            self._url(path, user), data=content.encode("utf-8"), auth=(user, password), timeout=self.timeout
        ).raise_for_status()

    def copy(self, path: str, new_path: str) -> None:
        """A server-side copy; Nextcloud logs one activity for the top folder only."""
        self._dav(
            "COPY", path, headers={"Destination": self._url(new_path), "Overwrite": "F"}
        ).raise_for_status()

    def delete(self, path: str) -> None:
        response = self._dav("DELETE", path)
        if response.status_code != 404:
            response.raise_for_status()

    def move(self, path: str, new_path: str) -> None:
        """Rename or move ``path``; Nextcloud keeps its file id, so it stays the same record."""
        self._dav(
            "MOVE", path, headers={"Destination": self._url(new_path), "Overwrite": "F"}
        ).raise_for_status()

    def list(self, path: str = "") -> list[str]:
        """Paths under ``path``, relative to the user's root, folders ending in "/"."""
        response = self._dav("PROPFIND", path, headers={"Depth": "infinity"})
        if response.status_code == 404:
            return []
        response.raise_for_status()
        root = f"/remote.php/dav/files/{self.user}/"
        found = []
        for href in ElementTree.fromstring(response.content).iter(f"{_DAV}href"):
            relative = requests.utils.unquote(href.text or "").split(root, 1)[-1]
            if relative and relative.rstrip("/") != path.strip("/"):
                found.append(relative)
        return sorted(found)

    def file_id(self, path: str) -> str:
        """Nextcloud's id for ``path``: the connector's external record id for it."""
        body = (
            '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            "<d:prop><oc:fileid/></d:prop></d:propfind>"
        )
        response = self._dav("PROPFIND", path, headers={"Depth": "0"}, data=body)
        response.raise_for_status()
        found = ElementTree.fromstring(response.content).find(".//{http://owncloud.org/ns}fileid")
        if found is None or not found.text:
            raise RuntimeError(f"No file id for {path}")
        return found.text

    def clear_objects(self, _resource_name: str, folder: str) -> None:
        """Teardown hook for ``connector_lifecycle.destructor``: remove the run's folder."""
        self.delete(folder)
