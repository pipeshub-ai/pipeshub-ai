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

import json
import os
import posixpath
import re
import subprocess
import time
from urllib.parse import quote
from xml.etree import ElementTree

import requests

_DAV = "{DAV:}"


def _request_token(page: str) -> str:
    found = re.search(r'data-requesttoken="([^"]+)"', page)
    if not found:
        raise RuntimeError("Nextcloud page has no request token; the sign-in failed")
    return found.group(1)


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

    def share_with_sync_user(self, owner: tuple[str, str], path: str, download: bool = True) -> str:
        """``owner`` shares their ``path`` with the sync user (``download=False``: "Allow download"
        unticked); returns the share id."""
        data = {"path": f"/{path.strip('/')}", "shareType": "0", "shareWith": self.user}
        if not download:
            data["attributes"] = '[{"scope":"permissions","key":"download","value":false}]'
        response = requests.post(
            f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            params={"format": "json"},
            data=data,
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
        from helper.compose_control import ComposeUnavailable

        container = self._container()
        try:
            done = subprocess.run(
                ["docker", "exec", "-u", "www-data", container, "php", "occ", *args],
                capture_output=True, text=True, timeout=120, check=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise ComposeUnavailable(f"could not run occ in {container}: {e}") from e
        return done.stdout

    def _settings_session(self) -> tuple[requests.Session, dict[str, str]]:
        """A browser session of the sync user and the CSRF header its settings calls need."""
        session = requests.Session()
        session.headers["Origin"] = self.base_url
        page = session.get(f"{self.base_url}/index.php/login", timeout=self.timeout)
        page = session.post(
            f"{self.base_url}/index.php/login",
            data={"user": self.user, "password": self.password, "requesttoken": _request_token(page.text),
                  "timezone": "UTC", "timezone_offset": "0"},
            headers={"Referer": f"{self.base_url}/index.php/login"}, timeout=self.timeout,
        )
        page.raise_for_status()
        return session, {"requesttoken": _request_token(page.text)}

    def create_app_password(self, name: str) -> tuple[str, str]:
        """A new app password of the sync user, as Personal settings > Security makes one: (password, token id)."""
        session, csrf = self._settings_session()
        response = session.post(
            f"{self.base_url}/index.php/settings/personal/authtokens", json={"name": name},
            headers=csrf, timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["token"], str(response.json()["deviceToken"]["id"])

    def set_filesystem_access(self, token_id: str, name: str, allowed: bool) -> None:
        """Tick or untick "Allow filesystem access" for one of the sync user's app passwords."""
        session, csrf = self._settings_session()
        session.put(
            f"{self.base_url}/index.php/settings/personal/authtokens/{token_id}",
            json={"name": name, "scope": {"filesystem": allowed}}, headers=csrf, timeout=self.timeout,
        ).raise_for_status()

    def delete_app_password(self, token_id: str) -> None:
        session, csrf = self._settings_session()
        session.delete(
            f"{self.base_url}/index.php/settings/personal/authtokens/{token_id}", headers=csrf, timeout=self.timeout,
        )

    def mount_other_users_files(self, mount_point: str, owner: tuple[str, str]) -> str:
        """An external WebDAV storage at ``mount_point`` in the sync user's drive, showing ``owner``'s
        files from this same server; returns the mount id. Needs ``occ``."""
        self.occ("app:enable", "files_external")
        created = self.occ(
            "files_external:create", f"/{mount_point}", "dav", "password::password",
            "-c", "host=http://localhost", "-c", f"root=remote.php/dav/files/{owner[0]}",
            "-c", "secure=false", "-c", f"user={owner[0]}", "-c", f"password={owner[1]}",
        )
        mount_id = created.strip().rsplit(" ", 1)[-1]
        self.occ("files_external:applicable", "--add-user", self.user, mount_id)
        return mount_id

    def wait_until_listed(self, path: str, timeout: float = 60) -> None:
        """Wait until the sync user's WebDAV listing shows ``path`` (a new mount can take a moment)."""
        parent = posixpath.dirname(path.strip("/"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self._dav("PROPFIND", parent, headers={"Depth": "1"})
            if response.ok and quote(path.strip("/")) in response.text:
                return
            time.sleep(2)
        raise RuntimeError(f"{path} never showed up in the sync user's files")

    def _container(self) -> str:
        from helper.compose_control import ComposeStack

        return os.getenv("NEXTCLOUD_CONTAINER") or ComposeStack.from_env().container_id("nextcloud-source")

    def connector_address(self, service: str = "pipeshub-ai") -> str:
        """The address the connector service reaches Nextcloud from, as Nextcloud sees it. Needs ``occ``'s container."""
        from helper.compose_control import ComposeUnavailable

        try:
            done = subprocess.run(
                ["docker", "exec", self._container(), "getent", "hosts", service],
                capture_output=True, text=True, timeout=30, check=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise ComposeUnavailable(f"could not resolve {service} from Nextcloud: {e}") from e
        return done.stdout.split()[0]

    def failed_logins(self, address: str) -> int:
        """Failed sign-ins Nextcloud's brute-force protection counts for ``address``."""
        return int(json.loads(self.occ("security:bruteforce:attempts", address, "--output=json"))["attempts"])

    def reset_failed_logins(self, address: str) -> None:
        self.occ("security:bruteforce:reset", address)

    def take_mount_offline(self, mount_id: str) -> None:
        """Point the mount at a port nothing listens on, as when the server behind it is down."""
        self.occ("files_external:config", mount_id, "host", "http://127.0.0.1:1")

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

    def put(self, path: str, content: str, auth: tuple[str, str] | None = None, ctime: int | None = None) -> None:
        """Write ``path`` as the sync user, or as ``auth``'s user (folders above it too).

        ``ctime`` is the creation time a desktop client sends, in epoch seconds."""
        user, password = auth or (self.user, self.password)
        parts = [p for p in posixpath.dirname(path.strip("/")).split("/") if p]
        for i in range(1, len(parts) + 1):
            response = requests.request(
                "MKCOL", self._url("/".join(parts[:i]), user), auth=(user, password), timeout=self.timeout
            )
            if response.status_code not in (201, 405):  # 405: it already exists
                response.raise_for_status()
        requests.put(
            self._url(path, user), data=content.encode("utf-8"), auth=(user, password), timeout=self.timeout,
            headers={"X-OC-CTime": str(ctime)} if ctime else None,
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
