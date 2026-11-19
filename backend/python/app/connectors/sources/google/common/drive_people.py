"""Map Drive v3 `owners` / `lastModifyingUser` / permissions to the record's people fields."""

from typing import NamedTuple

from app.connectors.sources.google.drive.team.drive_access import permission_is_direct
from app.models.entities import SourcePerson

_SERVICE_ACCOUNT_EMAIL_SUFFIX = ".gserviceaccount.com"


class DriveFilePeople(NamedTuple):
    authored_by: SourcePerson | None
    last_modified_by: SourcePerson | None
    owners: list[SourcePerson]


ROLE_RANK = {"reader": 1, "commenter": 2, "writer": 3, "fileOrganizer": 4, "organizer": 5, "owner": 6}


def _drive_user_to_person(user: object, emails_by_id: dict[str, str] | None = None) -> SourcePerson | None:
    if not isinstance(user, dict):
        return None
    source_id = user.get("permissionId") or None
    # Drive leaves the address out of `owners` / `lastModifyingUser` for some
    # cross-domain people while the file's own permission with the same id
    # carries it; without it the person is stored apart from their email node.
    email = user.get("emailAddress") or (emails_by_id or {}).get(source_id or "") or None
    if not (email or source_id):
        return None
    return SourcePerson(
        source_id=source_id,
        email=email,
        display_name=user.get("displayName") or None,
        is_service_account=bool(email and email.lower().endswith(_SERVICE_ACCOUNT_EMAIL_SUFFIX)),
    )


def _emails_by_id(permissions: list[dict] | None) -> dict[str, str]:
    return {
        p["id"]: p["emailAddress"]
        for p in permissions or []
        if isinstance(p, dict) and p.get("id") and p.get("emailAddress")
    }


def drive_file_people(metadata: dict, permissions: list[dict] | None = None) -> DriveFilePeople:
    emails = _emails_by_id(permissions)
    owners = [p for p in (_drive_user_to_person(u, emails) for u in metadata.get("owners") or []) if p]
    # Drive has no creator field; in My Drive the single owner is the creator.
    # Shared-drive items are owned by the drive, so they get no author.
    authored_by = owners[0] if len(owners) == 1 and not metadata.get("driveId") else None
    return DriveFilePeople(
        authored_by=authored_by,
        last_modified_by=_drive_user_to_person(metadata.get("lastModifyingUser"), emails),
        owners=owners,
    )


def _person_grants(grants: list[dict]) -> list[dict]:
    return [
        g for g in grants
        if isinstance(g, dict) and g.get("type") == "user" and not g.get("deleted")
        and (g.get("id") or g.get("emailAddress"))
    ]


def drive_shared_with(
    grants: list[dict],
    *,
    owner_ids: set[str],
    parent_grants: list[dict] | None,
    has_details: bool,
) -> list[SourcePerson] | None:
    """The people an item was shared with directly, or None when that cannot be told.

    Shared-drive items say per grant whether it was written on the item. My
    Drive does not, so a grant counts as direct when the parent folder gives
    that person no role as high: ``parent_grants`` is ``[]`` for an item with
    no parent, and ``None`` when the parent's sharing could not be read.
    """
    if not has_details and parent_grants is None:
        return None
    parent_rank: dict[str, int] = {}
    for g in _person_grants(parent_grants or []):
        rank = ROLE_RANK.get(g.get("role", ""), 0)
        for key in (g.get("id"), (g.get("emailAddress") or "").lower()):
            if key:
                parent_rank[key] = max(parent_rank.get(key, 0), rank)
    shared: list[SourcePerson] = []
    for g in _person_grants(grants):
        if g.get("role") == "owner" or g.get("id") in owner_ids:
            continue
        if has_details:
            if permission_is_direct(g) is not True:
                continue
        else:
            held = max(parent_rank.get(g.get("id") or "", 0), parent_rank.get((g.get("emailAddress") or "").lower(), 0))
            if held >= ROLE_RANK.get(g.get("role", ""), 0):
                continue
        shared.append(SourcePerson(
            source_id=g.get("id") or None, email=g.get("emailAddress") or None,
            display_name=g.get("displayName") or None,
        ))
    return shared
