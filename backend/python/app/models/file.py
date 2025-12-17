from dataclasses import dataclass

from .permission import AccessControl, Permission


@dataclass
class FileMetadata:
    file_id: str
    name: str
    mime_type: str
    parents: list[str]
    modified_time: str
    created_time: str | None = None
    md5_checksum: str | None = None
    description: str | None = None
    starred: bool = False
    trashed: bool = False
    owners: list[dict[str, str]] = None
    last_modifying_user: dict[str, str] | None = None
    permissions: list[Permission] = None
    access_control: AccessControl | None = None
    lastUpdatedTimestampAtSource: str | None = None


@dataclass
class FileContent:
    file_id: str
    content: bytes
    version_time: str
