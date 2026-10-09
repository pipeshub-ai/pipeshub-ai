"""Jira issue security levels for the team connectors.

Issue security is not the project permission scheme. A project browse grant
does not show an issue that has a security level. Sub-tasks inherit their
parent issue's level; a story under an epic does not.

Documented responses this code trusts (Cloud REST v3, same paths on Data
Center ``/rest/api/2`` where the server exposes them):

* ``GET /rest/api/3/project/{id}/issuesecuritylevelscheme`` — 200 body is a
  scheme with ``id``. Documented errors are 400, 401, 403 (project visible,
  caller is not an admin) and 404 (project missing or not visible). 404 is
  not "this project has no scheme". A 200 body without ``id`` is not a scheme
  either, and must not be treated as one.
* ``GET /rest/api/3/issuesecurityschemes/project`` — a page of
  ``{projectId, issueSecuritySchemeId}`` for classic projects that use a
  scheme (Administer Jira). A completed 200 page that does not contain the
  project is the positive "no scheme" result for a company-managed project.
  Team-managed projects (``style`` ``next-gen``) are outside this API, so
  their absence is not "no scheme".
* ``GET /rest/api/3/issuesecurityschemes/{id}/members`` — one page of holders
  per scheme, not per issue. Holder ``type`` values seen in the API are
  ``user``, ``group``, ``projectRole``, ``reporter``, ``assignee``, plus
  ``applicationRole``, ``projectLead``, and custom-field holders via
  ``expand=field``.
* Issue field ``security`` is JSON ``null`` when the issue has no level, or
  ``{id, name, ...}`` when it does. A missing key is not ``null``: the field
  was not returned. The scheme's default level is applied when an issue is
  created, not when this field is null, so a null field stays visible to
  everyone who can browse the project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import AccessRule
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.models.entities import Record

# Same org scope the browse-scheme "anyone" holder already uses.
ANYONE_AUTHENTICATED_SCOPE = "anyone_authenticated"
ADDONS_PROJECT_ROLE = "atlassian-addons-project-access"

HOLDER_USER = "user"
HOLDER_GROUP = "group"
HOLDER_PROJECT_ROLE = "projectRole"
HOLDER_REPORTER = "reporter"
HOLDER_ASSIGNEE = "assignee"
HOLDER_PROJECT_LEAD = "projectLead"
HOLDER_APPLICATION_ROLE = "applicationRole"
HOLDER_ANYONE = "anyone"
HOLDER_USER_FIELD = "userCustomField"
HOLDER_GROUP_FIELD = "groupCustomField"
_FIELD_HOLDERS = (HOLDER_USER_FIELD, HOLDER_GROUP_FIELD)


class SchemeKnowledge(str, Enum):
    """Whether this project has an issue security scheme."""

    NONE = "none"
    PRESENT = "present"
    UNKNOWN = "unknown"


@dataclass
class IssueSecurityContext:
    knowledge: SchemeKnowledge
    scheme_id: str | None = None
    members_by_level: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    members_readable: bool = False
    custom_field_ids: list[str] = field(default_factory=list)
    scheme_forbidden: bool = False
    # Personal connectors do not enforce issue security.
    enforce: bool = True
    # Set only for a transient scheme read (429, 5xx, network). A 403 or a
    # team-managed project advances the issue cursor; those issues stay closed.
    defer_checkpoint: bool = False

    def fingerprint(self) -> str:
        if self.knowledge == SchemeKnowledge.NONE:
            return "none"
        if self.knowledge != SchemeKnowledge.PRESENT:
            return "unknown"
        parts = [self.scheme_id or "", "1" if self.members_readable else "0"]
        for level_id in sorted(self.members_by_level):
            bits = sorted(
                _holder_fingerprint(holder)
                for holder in self.members_by_level[level_id]
                if isinstance(holder, dict)
            )
            parts.append(f"{level_id}={','.join(bits)}")
        return "|".join(parts)


def _holder_fingerprint(holder: dict[str, Any]) -> str:
    """Include the user. value alone misses an account change."""
    user = holder.get("user") if isinstance(holder.get("user"), dict) else {}
    account = user.get("accountId") or user.get("key") or user.get("name") or ""
    email = str(user.get("emailAddress") or "").lower()
    return (
        f"{holder.get('type')}:{holder.get('value') or holder.get('parameter')}:"
        f"{account}:{email}"
    )


def http_status_is_transient(status: int | None) -> bool:
    """429, 5xx, and a failed request (status 0). A 401, 403, or 404 is not retried."""
    if status is None:
        return False
    return status == 0 or status == 429 or status >= 500


def resolve_scheme_knowledge(
    direct: tuple[SchemeKnowledge, str | None],
    mapped: tuple[SchemeKnowledge, str | None],
) -> tuple[SchemeKnowledge, str | None]:
    """Prefer a scheme id from the project call. Use the mapping only to prove there is none."""
    if direct[0] == SchemeKnowledge.PRESENT:
        return direct
    if direct[0] == SchemeKnowledge.NONE:
        return SchemeKnowledge.NONE, None
    if mapped[0] == SchemeKnowledge.PRESENT:
        return mapped
    if mapped[0] == SchemeKnowledge.NONE:
        return SchemeKnowledge.NONE, None
    return SchemeKnowledge.UNKNOWN, None


async def collect_paged_values(
    fetch_page: Callable[[int], Awaitable[tuple[int, dict[str, Any] | None]]],
) -> tuple[int, list[dict[str, Any]] | None]:
    """``fetch_page(start_at)`` returns ``(status, body)`` for one page of ``values``."""
    start = 0
    values: list[dict[str, Any]] = []
    while True:
        status, body = await fetch_page(start)
        if status != 200 or not isinstance(body, dict):
            return status, None
        page = body.get("values") or []
        if not isinstance(page, list):
            return status, None
        values.extend(item for item in page if isinstance(item, dict))
        if body.get("isLast", True) or not page:
            return 200, values
        step = body.get("maxResults") or len(page) or 50
        try:
            start += int(step)
        except (TypeError, ValueError):
            return status, None
        if start > 100_000:
            return status, None


def classify_scheme_response(
    status: int,
    body: object,
    *,
    project_style: str | None = None,
) -> tuple[SchemeKnowledge, str | None]:
    """A 200 body with an ``id`` is a scheme. A bare 404 or a 403 is unknown.

    Jira answers "this project has no issue security scheme" with 404 and
    ``Security level for project {id} does not exist``. A 403, or a 404 that
    does not say that, still means the scheme could not be read. Next-gen
    projects are not decided by this endpoint.
    """
    if status == 200 and isinstance(body, dict):
        scheme_id = body.get("id")
        if scheme_id not in (None, ""):
            return SchemeKnowledge.PRESENT, str(scheme_id)
    if (
        status == 404
        and (project_style or "").lower() != "next-gen"
        and isinstance(body, dict)
    ):
        messages = body.get("errorMessages") or []
        if any(
            isinstance(message, str)
            and "security level for project" in message.lower()
            and "does not exist" in message.lower()
            for message in messages
        ):
            return SchemeKnowledge.NONE, None
    return SchemeKnowledge.UNKNOWN, None


def classify_project_mapping(
    project_id: str,
    values: list[dict[str, Any]] | None,
    *,
    complete: bool,
    http_status: int,
    project_style: str | None,
) -> tuple[SchemeKnowledge, str | None]:
    """Positive "no scheme" is a completed 200 mapping that omits a classic project,
    or names it without an ``issueSecuritySchemeId``.

    ``next-gen`` projects are not in this API, so a page that omits them does
    not say they have no ticket-level security.
    """
    if http_status != 200 or not complete or values is None:
        return SchemeKnowledge.UNKNOWN, None
    wanted = str(project_id)
    for row in values:
        if not isinstance(row, dict):
            continue
        if str(row.get("projectId")) != wanted:
            continue
        scheme_id = row.get("issueSecuritySchemeId")
        if scheme_id in (None, ""):
            # A completed row that names the project and carries no scheme id is
            # "no scheme". Next-gen projects are not covered by this API.
            if (project_style or "").lower() == "next-gen":
                return SchemeKnowledge.UNKNOWN, None
            return SchemeKnowledge.NONE, None
        return SchemeKnowledge.PRESENT, str(scheme_id)
    if (project_style or "").lower() == "next-gen":
        return SchemeKnowledge.UNKNOWN, None
    return SchemeKnowledge.NONE, None


def index_members(values: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_level: dict[str, list[dict[str, Any]]] = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        level_id = row.get("issueSecurityLevelId")
        holder = row.get("holder")
        if level_id is None or not isinstance(holder, dict) or not holder.get("type"):
            continue
        by_level.setdefault(str(level_id), []).append(holder)
    return by_level


def custom_field_ids_from_members(members_by_level: dict[str, list[dict[str, Any]]]) -> list[str]:
    found: set[str] = set()
    for holders in members_by_level.values():
        for holder in holders:
            if holder.get("type") not in _FIELD_HOLDERS:
                continue
            raw = holder.get("parameter") or holder.get("value")
            field_obj = holder.get("field")
            if not raw and isinstance(field_obj, dict):
                raw = field_obj.get("id") or field_obj.get("key")
            if raw:
                found.add(_normalize_field_key(str(raw)))
    return sorted(found)


def read_security_level(fields: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Return ``(field_present, level_id)``. A missing key is not a null level."""
    if not isinstance(fields, dict) or "security" not in fields:
        return False, None
    security = fields.get("security")
    if security is None:
        return True, None
    if isinstance(security, dict):
        level_id = security.get("id")
        if level_id not in (None, ""):
            return True, str(level_id)
    return True, None


def _normalize_field_key(raw: str) -> str:
    text = raw.strip()
    if text.startswith("customfield_") or not text.isdigit():
        return text
    return f"customfield_{text}"


def _email_of(user_by_id: dict[str, Any], identifier: str | None) -> str | None:
    if not identifier or identifier not in user_by_id:
        return None
    user = user_by_id[identifier]
    email = getattr(user, "email", None)
    if isinstance(email, str) and email.strip():
        return email
    return None


def _holder_user_email(holder: dict[str, Any], user_by_id: dict[str, Any]) -> tuple[str | None, bool]:
    """Return ``(email, skip_because_inactive)``."""
    user = holder.get("user") if isinstance(holder.get("user"), dict) else {}
    if user.get("active") is False:
        return None, True
    account = user.get("accountId") or user.get("key") or user.get("name") or holder.get("parameter")
    email = _email_of(user_by_id, str(account) if account else None)
    if not email:
        raw = user.get("emailAddress")
        if isinstance(raw, str) and raw.strip():
            email = raw
    return email, False


def _permissions_for_holder(
    holder: dict[str, Any],
    *,
    project_key: str,
    fields: dict[str, Any],
    user_by_id: dict[str, Any],
    app_roles_mapping: dict[str, list[dict[str, Any]]] | None,
    reporter_email: str | None,
    assignee_email: str | None,
    seen: set[str],
) -> tuple[list[Permission], bool, str | None]:
    """Return ``(grants, field_unreadable, skipped_type)``.

    ``field_unreadable`` means a custom-field holder is on the level and that
    field is not in the issue payload. The issue must not fall open.
    """
    holder_type = holder.get("type")
    parameter = holder.get("parameter")
    value = holder.get("value")
    grants: list[Permission] = []

    def add_user(email: str | None) -> None:
        if not email:
            return
        key = f"user:{email.lower()}"
        if key in seen:
            return
        seen.add(key)
        grants.append(Permission(entity_type=EntityType.USER, email=email, type=PermissionType.READ))

    def add_group(external_id: str | None) -> None:
        if not external_id:
            return
        key = f"group:{external_id}"
        if key in seen:
            return
        seen.add(key)
        grants.append(Permission(
            entity_type=EntityType.GROUP, external_id=str(external_id), type=PermissionType.READ,
        ))

    def add_role(external_id: str) -> None:
        key = f"role:{external_id}"
        if key in seen:
            return
        seen.add(key)
        grants.append(Permission(
            entity_type=EntityType.ROLE, external_id=external_id, type=PermissionType.READ,
        ))

    if holder_type == HOLDER_GROUP:
        add_group(value or parameter)
        return grants, False, None

    if holder_type == HOLDER_USER:
        email, inactive = _holder_user_email(holder, user_by_id)
        if inactive:
            return grants, False, None
        add_user(email)
        return grants, False, None

    if holder_type == HOLDER_PROJECT_ROLE:
        role = holder.get("projectRole") if isinstance(holder.get("projectRole"), dict) else {}
        if role.get("name") == ADDONS_PROJECT_ROLE:
            return grants, False, None
        role_id = parameter or role.get("id")
        if role_id not in (None, ""):
            add_role(f"{project_key}_{role_id}")
        return grants, False, None

    if holder_type == HOLDER_REPORTER:
        add_user(reporter_email)
        return grants, False, None

    if holder_type == HOLDER_ASSIGNEE:
        add_user(assignee_email)
        return grants, False, None

    if holder_type == HOLDER_PROJECT_LEAD:
        add_role(f"{project_key}_projectLead")
        return grants, False, None

    if holder_type == HOLDER_APPLICATION_ROLE:
        # A security level must not become an org-wide grant. Resolve the role
        # to its groups, or skip it when those groups cannot be read.
        role_key = parameter or value
        groups = (app_roles_mapping or {}).get(role_key) if role_key else None
        if not groups:
            return grants, False, HOLDER_APPLICATION_ROLE
        for group_info in groups:
            if isinstance(group_info, dict):
                add_group(group_info.get("groupId") or group_info.get("name"))
        return grants, False, None

    if holder_type == HOLDER_ANYONE:
        key = f"org:{ANYONE_AUTHENTICATED_SCOPE}"
        if key not in seen:
            seen.add(key)
            grants.append(Permission(
                entity_type=EntityType.ORG,
                external_id=ANYONE_AUTHENTICATED_SCOPE,
                type=PermissionType.READ,
            ))
        return grants, False, None

    if holder_type in _FIELD_HOLDERS:
        raw = parameter or value
        field_obj = holder.get("field")
        if not raw and isinstance(field_obj, dict):
            raw = field_obj.get("id") or field_obj.get("key")
        if not raw:
            return grants, True, holder_type
        keys = {_normalize_field_key(str(raw)), str(raw)}
        if not any(key in fields for key in keys):
            return grants, True, holder_type
        present_key = next(key for key in keys if key in fields)
        value_at_field = fields.get(present_key)
        for entry in _field_entries(value_at_field):
            if holder_type == HOLDER_GROUP_FIELD:
                add_group(entry.get("groupId") or entry.get("name") or entry.get("value"))
            else:
                email, inactive = _holder_user_email({"user": entry, "parameter": entry.get("accountId")}, user_by_id)
                if not inactive:
                    add_user(email)
        return grants, False, None

    return grants, False, str(holder_type) if holder_type else "unknown"


def _field_entries(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def level_grants(
    holders: list[dict[str, Any]],
    *,
    project_key: str,
    fields: dict[str, Any],
    user_by_id: dict[str, Any],
    app_roles_mapping: dict[str, list[dict[str, Any]]] | None,
    reporter_email: str | None,
    assignee_email: str | None,
) -> tuple[list[Permission], bool, list[str]]:
    grants: list[Permission] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        portion, unreadable, skipped_type = _permissions_for_holder(
            holder,
            project_key=project_key,
            fields=fields,
            user_by_id=user_by_id,
            app_roles_mapping=app_roles_mapping,
            reporter_email=reporter_email,
            assignee_email=assignee_email,
            seen=seen,
        )
        if unreadable:
            return [], True, skipped
        grants.extend(portion)
        if skipped_type and skipped_type not in skipped:
            skipped.append(skipped_type)
    return grants, False, skipped


def apply_issue_access(
    record: Record,
    fields: dict[str, Any] | None,
    *,
    is_subtask: bool,
    project_key: str,
    context: IssueSecurityContext,
    user_by_account_id: dict[str, Any] | None = None,
    app_roles_mapping: dict[str, list[dict[str, Any]]] | None = None,
    reporter_email: str | None = None,
    assignee_email: str | None = None,
) -> tuple[list[Permission], list[str]]:
    """Set the issue's access rule and return the grants to write.

    A story under an epic in the same project inherits that epic. The epic's
    level then hides the story from people who cannot open the epic. A parent
    in another project is handled by the processor, which keeps the story on
    its own project. Attachments are handled separately and stay empty.
    """
    if not context.enforce:
        # A personal account has no issue security. A nested issue inherits its
        # parent issue. One with no parent inherits the project, which inherits the app.
        record.inherit_permissions = True
        record.rewrite_permissions = True
        record.inherit_permissions_from_group = False
        return [], []
    record.inherit_permissions = True
    record.inherit_permissions_from_group = False
    users = user_by_account_id or {}
    present, level_id = read_security_level(fields)
    security = fields.get("security") if isinstance(fields, dict) else None

    def close() -> tuple[list[Permission], list[str]]:
        # RESTRICTED with no grant hides the issue. A transient failure is
        # retried, so the grants already stored stay until that retry.
        record.access_rule = AccessRule.RESTRICTED
        record.rewrite_permissions = not context.defer_checkpoint
        return [], []

    def project_open() -> tuple[list[Permission], list[str]]:
        record.access_rule = AccessRule.STRICT
        record.rewrite_permissions = True
        return [], []

    if context.knowledge == SchemeKnowledge.UNKNOWN:
        return close()

    if context.knowledge == SchemeKnowledge.NONE:
        # A level id with no scheme to resolve is not opened to the project.
        if level_id or (present and security is not None):
            return close()
        return project_open()

    if not present or (security is not None and level_id is None):
        return close()
    if level_id is None:
        return project_open()

    if not context.members_readable:
        # Includes a Data Center that has no member route. A leveled issue stays
        # closed rather than opening to everyone who can browse the project.
        return close()

    holders = context.members_by_level.get(level_id, [])
    grants, unreadable, skipped = level_grants(
        holders,
        project_key=project_key,
        fields=fields or {},
        user_by_id=users,
        app_roles_mapping=app_roles_mapping,
        reporter_email=reporter_email,
        assignee_email=assignee_email,
    )
    if unreadable:
        return close()
    record.access_rule = AccessRule.RESTRICTED
    record.rewrite_permissions = True
    return grants, skipped


def apply_attachment_access(record: Record) -> list[Permission]:
    """An attachment has no security level of its own. It follows its issue and is not a seed."""
    record.inherit_permissions = True
    record.inherit_permissions_from_group = False
    record.access_rule = AccessRule.STRICT
    record.rewrite_permissions = True
    return []
