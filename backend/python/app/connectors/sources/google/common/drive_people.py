"""Map Drive v3 `owners` / `lastModifyingUser` to the record's authorship fields."""

from typing import NamedTuple

from app.models.entities import SourcePerson

_SERVICE_ACCOUNT_EMAIL_SUFFIX = ".gserviceaccount.com"


class DriveFilePeople(NamedTuple):
    authored_by: SourcePerson | None
    last_modified_by: SourcePerson | None
    owners: list[SourcePerson]


def _drive_user_to_person(user: object) -> SourcePerson | None:
    if not isinstance(user, dict):
        return None
    email = user.get("emailAddress") or None
    source_id = user.get("permissionId") or None
    if not (email or source_id):
        return None
    return SourcePerson(
        source_id=source_id,
        email=email,
        display_name=user.get("displayName") or None,
        is_service_account=bool(email and email.lower().endswith(_SERVICE_ACCOUNT_EMAIL_SUFFIX)),
    )


def drive_file_people(metadata: dict) -> DriveFilePeople:
    owners = [p for p in (_drive_user_to_person(u) for u in metadata.get("owners") or []) if p]
    # Drive has no creator field; in My Drive the single owner is the creator.
    # Shared-drive items are owned by the drive, so they get no author.
    authored_by = owners[0] if len(owners) == 1 and not metadata.get("driveId") else None
    return DriveFilePeople(
        authored_by=authored_by,
        last_modified_by=_drive_user_to_person(metadata.get("lastModifyingUser")),
        owners=owners,
    )
