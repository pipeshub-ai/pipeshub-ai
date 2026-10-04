"""A small, stateful Google Drive + Admin Directory, served through ``FakeGoogleHttp``.

The world holds users, groups, shared drives and files with their sharing, and keeps
a change log so ``changes.list`` behaves like Drive's: every mutation (create, edit,
rename, move, trash, delete, share, unshare) is a change, a change token is a
position in the log, a page of changes carries each file once in its latest state,
and a file the caller can no longer reach comes back as ``removed``.

Visibility is per caller, the way Drive does it: owners and direct or group grants
(inherited down folders) see a file; shared drive members see the drive's files;
``sharedWithMe`` is only the item that carries the grant itself; domain and
anyone-with-link grants let a caller open a file but never list it. ``fields``
masks are honoured, so a connector only gets the fields it asked for.

Knobs a test can turn: ``page_size`` (small by default, so every listing pages),
``empty_page_at`` (Drive may return an empty page that still has a next token),
per-file ``perm_access`` ("all", "empty" or "forbidden") for permissions.list, and
the HTTP layer's ``fail(...)`` for quota errors and outages.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from google_behaviour_fakes import (
    ApiRequest,
    FakeGoogleHttp,
    HttpResult,
    google_error,
    paginate,
)

FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
if TYPE_CHECKING:
    from collections.abc import Callable

DRIVE_API = "/drive/v3"
ADMIN_API = "/admin/directory/v1"


@dataclass
class DriveUser:
    email: str
    name: str
    permission_id: str
    root_id: str
    user_id: str
    suspended: bool = False


@dataclass
class FileState:
    meta: dict[str, Any]
    perms: list[dict[str, Any]] = field(default_factory=list)
    content: bytes = b""
    deleted: bool = False
    perm_access: Optional[str] = None


@dataclass
class _Change:
    seq: int
    file_id: str
    drive_id: Optional[str]
    affected: set[str]


def _project(obj: dict[str, Any], names: Optional[list[str]]) -> dict[str, Any]:
    if names is None:
        return {k: obj[k] for k in ("id", "name", "mimeType") if k in obj}
    out: dict[str, Any] = {}
    for name in names:
        head, _, rest = name.partition("/")
        if head not in obj:
            continue
        if rest and isinstance(obj[head], dict):
            out.setdefault(head, {}).update(_project(obj[head], [rest]))
        else:
            out[head] = obj[head]
    return out


def _mask(fields: Optional[str], container: str) -> Optional[list[str]]:
    """Field names requested inside ``container(...)`` of a Drive ``fields`` mask."""
    if not fields:
        return None
    match = re.search(rf"\b{container}\(([^()]*)\)", fields)
    if match is None:
        return None
    return [f.strip() for f in match.group(1).split(",") if f.strip()]


def _split_and(q: str) -> list[str]:
    parts, depth, current = [], 0, ""
    tokens = re.split(r"(\(|\)|\s+and\s+)", q)
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        if depth == 0 and re.fullmatch(r"\s+and\s+", token or ""):
            parts.append(current)
            current = ""
        else:
            current += token
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


class DriveWorld:
    def __init__(self, http: FakeGoogleHttp, *, domain: str = "example.com", page_size: int = 2) -> None:
        self.http = http
        self.domain = domain
        self.page_size = page_size
        self.change_page_size = page_size
        self.perm_page_size = page_size
        self.admin_page_size = page_size
        self.empty_page_at: Optional[int] = None
        self.users: dict[str, DriveUser] = {}
        self.aliases: dict[str, str] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.drives: dict[str, dict[str, Any]] = {}
        self.files: dict[str, FileState] = {}
        self.log: list[_Change] = []
        self.admin_email: Optional[str] = None
        self._ids = itertools.count(1)
        self._install_routes()

    # --- building the world --------------------------------------------------

    def add_user(self, email: str, name: Optional[str] = None, *, suspended: bool = False) -> DriveUser:
        n = next(self._ids)
        root = f"root-{email.split('@')[0]}"
        user = DriveUser(email, name or email.split("@")[0].title(), f"perm-{n}", root, f"uid-{n}", suspended)
        self.users[email] = user
        self.files[root] = FileState({"id": root, "name": "My Drive", "mimeType": FOLDER, "owners": [{"emailAddress": email}], "parents": [], "trashed": False})
        return user

    def add_group(self, email: str, members: list[str], name: Optional[str] = None) -> None:
        self.groups[email] = {"id": f"gid-{next(self._ids)}", "email": email, "name": name or email.split("@")[0], "members": list(members)}

    def add_drive(self, drive_id: str, name: str, members: dict[str, str]) -> None:
        self.drives[drive_id] = {"id": drive_id, "name": name, "members": dict(members), "createdTime": "2024-01-01T00:00:00.000Z"}
        self.files[drive_id] = FileState({"id": drive_id, "name": name, "mimeType": FOLDER, "driveId": drive_id, "parents": [], "trashed": False})

    def add_item(
        self,
        file_id: str,
        name: str,
        *,
        parent: str,
        owner: Optional[str] = None,
        mime: str = "text/plain",
        drive_id: Optional[str] = None,
        content: bytes = b"hello",
        perms: Optional[list[dict[str, Any]]] = None,
    ) -> FileState:
        meta: dict[str, Any] = {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "parents": [parent],
            "createdTime": "2024-01-01T00:00:00.000Z",
            "modifiedTime": "2024-01-02T00:00:00.000Z",
            "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            "version": "1",
            "trashed": False,
            "shared": bool(perms),
            "capabilities": {"canListChildren": mime == FOLDER},
        }
        if mime != FOLDER:
            meta["headRevisionId"] = f"{file_id}-rev1"
            meta["size"] = str(len(content))
            if "." in name:
                meta["fileExtension"] = name.rsplit(".", 1)[-1]
        drive_id = drive_id or self.files[parent].meta.get("driveId")
        if drive_id:
            meta["driveId"] = drive_id
        else:
            meta["owners"] = [{"emailAddress": owner}]
        state = FileState(meta, [dict(p) for p in (perms or [])], content)
        return self._mutate(file_id, lambda: self.files.__setitem__(file_id, state)) or state

    def folder(self, file_id: str, name: str, **kwargs: object) -> FileState:
        return self.add_item(file_id, name, mime=FOLDER, content=b"", **kwargs)

    # --- mutations (each one is a change) ------------------------------------

    def rename(self, file_id: str, name: str) -> None:
        self._mutate(file_id, lambda: self._bump(file_id, name=name))

    def edit(self, file_id: str, content: bytes) -> None:
        def apply() -> None:
            state = self.files[file_id]
            state.content = content
            rev = int(state.meta["version"]) + 1
            self._bump(file_id, headRevisionId=f"{file_id}-rev{rev}", size=str(len(content)))
        self._mutate(file_id, apply)

    def move(self, file_id: str, new_parent: str) -> None:
        self._mutate(file_id, lambda: self._bump(file_id, parents=[new_parent]))

    def trash(self, file_id: str) -> None:
        self._mutate(file_id, lambda: self._bump(file_id, trashed=True))

    def delete(self, file_id: str) -> None:
        self._mutate(file_id, lambda: setattr(self.files[file_id], "deleted", True))

    def share(self, file_id: str, perm: dict[str, Any]) -> None:
        def apply() -> None:
            self.files[file_id].perms.append(dict(perm))
            self._bump(file_id, shared=True)
        self._mutate(file_id, apply)

    def unshare(self, file_id: str, email: str) -> None:
        def apply() -> None:
            state = self.files[file_id]
            state.perms = [p for p in state.perms if p.get("emailAddress") != email]
            self._bump(file_id)
        self._mutate(file_id, apply)

    def _bump(self, file_id: str, **changes: object) -> None:
        meta = self.files[file_id].meta
        meta.update(changes)
        meta["version"] = str(int(meta["version"]) + 1)
        meta["modifiedTime"] = f"2024-02-{int(meta['version']):02d}T00:00:00.000Z"

    def _mutate(self, file_id: str, apply: Callable[[], None]) -> None:
        before = self._access_set(file_id) if file_id in self.files else set()
        apply()
        after = self._access_set(file_id)
        drive_id = self.files[file_id].meta.get("driveId")
        self.log.append(_Change(len(self.log) + 1, file_id, drive_id, before | after))

    # --- access model --------------------------------------------------------

    def email_of(self, identity: Optional[str]) -> Optional[str]:
        if identity is None:
            return None
        return self.aliases.get(identity, identity)

    def _group_emails_of(self, email: str) -> set[str]:
        return {g for g, data in self.groups.items() if email in data["members"]}

    def _chain(self, file_id: str) -> list[FileState]:
        chain, seen = [], set()
        current: Optional[str] = file_id
        while current and current in self.files and current not in seen:
            seen.add(current)
            chain.append(self.files[current])
            parents = self.files[current].meta.get("parents") or []
            current = parents[0] if parents else None
        return chain

    def _grant_role(self, perms: list[dict[str, Any]], email: str) -> Optional[str]:
        groups = self._group_emails_of(email)
        for perm in perms:
            if perm.get("type") == "user" and perm.get("emailAddress") == email:
                return perm["role"]
            if perm.get("type") == "group" and perm.get("emailAddress") in groups:
                return perm["role"]
        return None

    def direct_role(self, file_id: str, email: str) -> Optional[str]:
        """Role from ownership or a user/group grant on this item or an ancestor."""
        state = self.files.get(file_id)
        if state is None or state.deleted:
            return None
        for node in self._chain(file_id):
            owners = [o.get("emailAddress") for o in node.meta.get("owners", [])]
            if email in owners:
                return "owner"
            role = self._grant_role(node.perms, email)
            if role:
                return role
        return None

    def member_role(self, file_id: str, email: str) -> Optional[str]:
        state = self.files.get(file_id)
        if state is None or state.deleted:
            return None
        drive = self.drives.get(state.meta.get("driveId") or "")
        return drive["members"].get(email) if drive else None

    def can_open(self, file_id: str, email: str) -> bool:
        state = self.files.get(file_id)
        if state is None or state.deleted:
            return False
        if self.direct_role(file_id, email) or self.member_role(file_id, email):
            return True
        for node in self._chain(file_id):
            for perm in node.perms:
                if perm.get("type") == "anyone":
                    return True
                if perm.get("type") == "domain" and email.rpartition("@")[2] == perm.get("domain"):
                    return True
        return False

    def _access_set(self, file_id: str) -> set[str]:
        return {email for email in self.users if self.direct_role(file_id, email) or self.member_role(file_id, email)}

    def _shared_with_me(self, state: FileState, email: str) -> bool:
        owners = [o.get("emailAddress") for o in state.meta.get("owners", [])]
        return email not in owners and self._grant_role(state.perms, email) is not None

    # --- HTTP ----------------------------------------------------------------

    def _install_routes(self) -> None:
        r = self.http.route
        r("GET", f"{DRIVE_API}/about", self._about)
        r("GET", f"{DRIVE_API}/files", self._files_list)
        r("GET", f"{DRIVE_API}/files/([^/]+)", self._files_get)
        r("GET", f"{DRIVE_API}/files/([^/]+)/export", self._files_export)
        r("GET", f"{DRIVE_API}/files/([^/]+)/permissions", self._permissions_list)
        r("GET", f"{DRIVE_API}/changes/startPageToken", self._start_token)
        r("GET", f"{DRIVE_API}/changes", self._changes_list)
        r("GET", f"{DRIVE_API}/drives", self._drives_list)
        r("GET", f"{ADMIN_API}/users", self._admin_users)
        r("GET", f"{ADMIN_API}/groups", self._admin_groups)
        r("GET", f"{ADMIN_API}/groups/([^/]+)/members", self._admin_members)

    def _caller(self, req: ApiRequest) -> str:
        email = self.email_of(req.identity)
        assert email, "request without a caller"
        return email

    def _about(self, req: ApiRequest) -> object:
        user = self.users.get(self._caller(req))
        if user is None:
            return google_error(403, "forbidden")
        return {"user": {"displayName": user.name, "emailAddress": user.email, "permissionId": user.permission_id}, "storageQuota": {}}

    def _file_id(self, req: ApiRequest) -> str:
        file_id = req.path.split("/files/")[1].split("/")[0]
        if file_id == "root":
            return self.users[self._caller(req)].root_id
        return file_id

    def _files_get(self, req: ApiRequest) -> object:
        email = self._caller(req)
        file_id = self._file_id(req)
        if not self.can_open(file_id, email):
            return google_error(404, "notFound", f"File not found: {file_id}.")
        state = self.files[file_id]
        if req.query.get("alt") == "media":
            return self._media(state.content, req)
        names = [f.strip() for f in req.query["fields"].split(",")] if req.query.get("fields") else None
        return _project(state.meta, names)

    def _files_export(self, req: ApiRequest) -> object:
        email = self._caller(req)
        file_id = self._file_id(req)
        if not self.can_open(file_id, email):
            return google_error(404, "notFound")
        return self._media(b"exported:" + self.files[file_id].content, req)

    @staticmethod
    def _media(content: bytes, req: ApiRequest) -> HttpResult:
        match = re.match(r"bytes=(\d+)-(\d+)", req.headers.get("range", ""))
        start, end = (int(match.group(1)), int(match.group(2))) if match else (0, len(content) - 1)
        chunk = content[start:end + 1]
        return HttpResult(206, chunk, {"content-range": f"bytes {start}-{start + len(chunk) - 1}/{len(content)}"})

    def _matches(self, q: str, state: FileState, email: str) -> bool:
        for term in _split_and(q):
            clause = term[1:-1] if term.startswith("(") and term.endswith(")") else term
            if not any(self._atom(atom.strip(), state, email) for atom in clause.split(" or ")):
                return False
        return True

    def _atom(self, atom: str, state: FileState, email: str) -> bool:
        compact = atom.replace(" ", "")
        if compact == "trashed=false":
            return not state.meta.get("trashed")
        if compact == "sharedWithMe=true":
            return self._shared_with_me(state, email)
        if m := re.fullmatch(r"mimeType='([^']+)'", compact):
            return state.meta.get("mimeType") == m.group(1)
        if m := re.fullmatch(r"'([^']+)' in parents", atom):
            return m.group(1) in (state.meta.get("parents") or [])
        raise AssertionError(f"DriveWorld does not understand query term {atom!r}")

    def _files_list(self, req: ApiRequest) -> object:
        email = self._caller(req)
        q = req.query.get("q", "")
        drive_id = req.query.get("driveId") if req.query.get("corpora") == "drive" else None
        if drive_id and drive_id not in self.drives:
            return google_error(404, "notFound", f"Shared drive not found: {drive_id}")
        if drive_id and email not in self.drives[drive_id]["members"]:
            return google_error(403, "teamDriveMembershipRequired")
        all_drives = req.query.get("includeItemsFromAllDrives") == "true"
        roots = {u.root_id for u in self.users.values()} | set(self.drives)
        matches = []
        for file_id, state in self.files.items():
            if state.deleted or file_id in roots:
                continue
            item_drive = state.meta.get("driveId")
            if drive_id:
                if item_drive != drive_id:
                    continue
            elif item_drive and not (all_drives and self.direct_role(file_id, email)):
                continue
            elif not item_drive and not self.direct_role(file_id, email):
                continue
            if q and not self._matches(q, state, email):
                continue
            matches.append(state.meta)
        return self._page_files(matches, req)

    def _page_files(self, items: list[dict[str, Any]], req: ApiRequest) -> dict[str, Any]:
        token = req.query.get("pageToken") or "0"
        served_empty = token.endswith("!")
        start = int(token.rstrip("!"))
        size = min(int(req.query.get("pageSize") or 100), self.page_size)
        if self.empty_page_at is not None and not served_empty and start == self.empty_page_at * size and start < len(items):
            return {"files": [], "nextPageToken": f"{start}!"}
        page = items[start:start + size]
        out: dict[str, Any] = {"files": [_project(m, _mask(req.query.get("fields"), "files")) for m in page]}
        if start + size < len(items):
            out["nextPageToken"] = str(start + size)
        return out

    def _permissions_list(self, req: ApiRequest) -> object:
        email = self._caller(req)
        resource_id = self._file_id(req)
        if resource_id in self.drives and req.query.get("useDomainAdminAccess") == "true":
            perms = [
                {"id": f"p-{m}", "type": "group" if m in self.groups else "user", "role": role, "emailAddress": m}
                for m, role in self.drives[resource_id]["members"].items()
            ]
            return paginate(perms, req.query, default_size=self.perm_page_size, key="permissions")
        if not self.can_open(resource_id, email):
            return google_error(404, "notFound")
        state = self.files[resource_id]
        role = self.direct_role(resource_id, email) or self.member_role(resource_id, email)
        access = state.perm_access or ("all" if role in ("owner", "organizer", "fileOrganizer", "writer") else ("empty" if state.meta.get("driveId") else "all"))
        if access == "forbidden":
            return google_error(403, "insufficientFilePermissions", "The user does not have sufficient permissions for this file.")
        if access == "empty":
            return {"permissions": []}
        perms: list[dict[str, Any]] = []
        for owner in state.meta.get("owners", []):
            perms.append({"id": f"p-{owner['emailAddress']}", "type": "user", "role": "owner", "emailAddress": owner["emailAddress"]})
        for node in self._chain(resource_id):
            for perm in node.perms:
                entry = {"id": perm.get("id", f"p-{perm.get('emailAddress') or perm.get('domain') or perm['type']}"), **perm}
                if state.meta.get("driveId"):
                    entry["permissionDetails"] = [{"permissionType": "file", "role": perm["role"], "inherited": node is not state}]
                perms.append(entry)
        drive = self.drives.get(state.meta.get("driveId") or "")
        if drive:
            for member, member_role in drive["members"].items():
                perms.append({
                    "id": f"p-{member}", "type": "group" if member in self.groups else "user", "role": member_role,
                    "emailAddress": member, "permissionDetails": [{"permissionType": "member", "role": member_role, "inherited": True}],
                })
        return paginate(perms, req.query, default_size=self.perm_page_size, key="permissions")

    def _start_token(self, req: ApiRequest) -> object:
        return {"startPageToken": str(len(self.log) + 1)}

    def _changes_list(self, req: ApiRequest) -> object:
        email = self._caller(req)
        base, _, offset = req.query["pageToken"].partition(":")
        drive_id = req.query.get("driveId")
        if drive_id and email not in self.drives.get(drive_id, {}).get("members", {}):
            return google_error(403, "teamDriveMembershipRequired")
        all_drives = req.query.get("includeItemsFromAllDrives") == "true"
        latest: dict[str, _Change] = {}
        for change in self.log[int(base) - 1:]:
            if drive_id and change.drive_id != drive_id:
                continue
            if not drive_id and change.drive_id and not all_drives:
                continue
            if email not in change.affected:
                continue
            latest.pop(change.file_id, None)
            latest[change.file_id] = change
        entries = list(latest.values())
        start = int(offset or 0)
        size = min(int(req.query.get("pageSize") or 100), self.change_page_size)
        names = _mask(req.query.get("fields"), "file")
        changes = []
        for change in entries[start:start + size]:
            state = self.files[change.file_id]
            reachable = not state.deleted and bool(self.direct_role(change.file_id, email) or self.member_role(change.file_id, email))
            item: dict[str, Any] = {"changeType": "file", "fileId": change.file_id, "removed": not reachable}
            if reachable:
                item["file"] = _project(state.meta, names)
            changes.append(item)
        out: dict[str, Any] = {"changes": changes}
        if start + size < len(entries):
            out["nextPageToken"] = f"{base}:{start + size}"
        else:
            out["newStartPageToken"] = str(len(self.log) + 1)
        return out

    def _drives_list(self, req: ApiRequest) -> object:
        email = self._caller(req)
        admin = req.query.get("useDomainAdminAccess") == "true"
        if admin and email != self.admin_email:
            return google_error(403, "forbidden")
        drives = [
            {"id": d["id"], "name": d["name"], "createdTime": d["createdTime"]}
            for d in self.drives.values() if admin or email in d["members"]
        ]
        if m := re.fullmatch(r"name contains '(.*)'", req.query.get("q", "")):
            drives = [d for d in drives if m.group(1).lower() in d["name"].lower()]
        return paginate(drives, req.query, default_size=self.page_size, key="drives")

    def _admin_guard(self, req: ApiRequest) -> Optional[object]:
        if self._caller(req) != self.admin_email:
            return google_error(403, "forbidden", "Not Authorized to access this resource/api")
        return None

    def _admin_users(self, req: ApiRequest) -> object:
        if denied := self._admin_guard(req):
            return denied
        users = [
            {"id": u.user_id, "primaryEmail": u.email, "name": {"fullName": u.name}, "suspended": u.suspended, "creationTime": "2023-01-01T00:00:00.000Z"}
            for u in sorted(self.users.values(), key=lambda u: u.email)
        ]
        return paginate(users, req.query, default_size=self.admin_page_size, key="users")

    def _admin_groups(self, req: ApiRequest) -> object:
        if denied := self._admin_guard(req):
            return denied
        groups = [
            {"id": g["id"], "email": g["email"], "name": g["name"], "creationTime": "2023-01-01T00:00:00.000Z"}
            for g in self.groups.values()
        ]
        return paginate(groups, req.query, default_size=self.admin_page_size, key="groups")

    def _admin_members(self, req: ApiRequest) -> object:
        if denied := self._admin_guard(req):
            return denied
        group_key = req.path.split("/groups/")[1].split("/")[0]
        group = self.groups.get(group_key)
        if group is None:
            return google_error(404, "notFound")
        members = [
            {"id": self.users[m].user_id if m in self.users else f"ext-{m}", "email": m, "type": "GROUP" if m in self.groups else "USER", "role": "MEMBER"}
            for m in group["members"]
        ]
        return paginate(members, req.query, default_size=self.admin_page_size, key="members")
