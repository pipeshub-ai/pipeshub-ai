"""Jira issue security levels for the team connectors.

Issue security is not the project permission scheme. A project browse grant
does not show an issue that has a security level. Jira judges every issue by its
own level: it copies a parent's level onto its sub-tasks, so a sub-task's own
``security`` field carries it, but an epic's level does not reach the epic's
children, which keep their own. A record inherits the record it is filed under,
so the connector files a sub-task under its parent, and a story under its epic only
when everyone who can read the story can read the epic (``level_reads_alike``);
any other issue inherits its project.

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

import hashlib
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
# Holders that name the same people on every issue of a project. Reporter, assignee and
# the field holders name people on each issue, so two issues in one level can differ.
_SAME_ON_EVERY_ISSUE = frozenset({
    HOLDER_USER, HOLDER_GROUP, HOLDER_PROJECT_ROLE, HOLDER_PROJECT_LEAD,
    HOLDER_APPLICATION_ROLE, HOLDER_ANYONE,
})


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
    # The HTTP status of the scheme or members read that failed, so the warning can tell
    # a token without the scope (401) from an account without the permission (403).
    read_status: int | None = None
    # Jira said the token lacks a scope. Any other 401 is a token Jira no longer accepts.
    scope_refused: bool = False

    def level_fingerprints(
        self, app_roles_mapping: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, str]:
        """Level id -> a digest of its holders, so a change names the levels it touched.

        An application-role holder is granted as that role's groups, so the groups are part
        of its digest: a group leaving the role re-reads the level's issues.
        """
        return {
            level_id: hashlib.sha256(",".join(sorted(
                _holder_fingerprint(holder, app_roles_mapping) for holder in holders if isinstance(holder, dict)
            )).encode()).hexdigest()[:16]
            for level_id, holders in self.members_by_level.items()
        }

    def fingerprint(self, app_roles_mapping: dict[str, list[dict[str, Any]]] | None = None) -> str:
        if self.knowledge == SchemeKnowledge.NONE:
            return "none"
        if self.knowledge != SchemeKnowledge.PRESENT:
            return "unknown"
        levels = self.level_fingerprints(app_roles_mapping)
        return "|".join([self.scheme_id or "", "1" if self.members_readable else "0",
                         *(f"{lid}={levels[lid]}" for lid in sorted(levels))])


def _holder_fingerprint(
    holder: dict[str, Any], app_roles_mapping: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """Include the user. value alone misses an account change."""
    user = holder.get("user") if isinstance(holder.get("user"), dict) else {}
    account = user.get("accountId") or user.get("key") or user.get("name") or ""
    email = str(user.get("emailAddress") or "").lower()
    role_key = holder.get("parameter") or holder.get("value")
    # A deactivated user is not granted, so deactivation must change the digest too.
    active = "inactive" if user.get("active") is False else ""
    digest = f"{holder.get('type')}:{role_key}:{account}:{email}:{active}"
    if holder.get("type") == HOLDER_APPLICATION_ROLE:
        groups = application_role_groups(role_key, app_roles_mapping)
        digest += ":" + "+".join(sorted(str(_group_ref(g)) for g in groups))
    return digest


def application_role_groups(
    role_key: str | None, app_roles_mapping: dict[str, list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    """The groups an application-role holder stands for.

    A bare holder (no role key) is "Any logged in user": an account with access to any
    Jira product, which is the union of every role's groups, not the whole org.
    """
    mapping = app_roles_mapping or {}
    if role_key:
        groups = mapping.get(role_key) or []
    else:
        groups = [g for role_groups in mapping.values() for g in role_groups or []]
    return [g for g in groups if isinstance(g, dict) and _group_ref(g)]


def _group_ref(group_info: dict[str, Any]) -> str | None:
    return group_info.get("groupId") or group_info.get("name")


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


def refuses_a_scope(body: object) -> bool:
    """Whether a Jira 401 body says the token lacks a scope ("Unauthorized; scope does not match")."""
    return isinstance(body, dict) and "scope does not match" in str(body.get("message") or "").lower()


def scheme_has_no_levels(body: object) -> bool:
    """A scheme body that lists no level. No issue can carry one, so its members need not
    be read. The "Simplified" schemes of team-managed projects are like this."""
    return isinstance(body, dict) and isinstance(body.get("levels"), list) and not body["levels"]


UNREADABLE_SCOPE = "scope"
UNREADABLE_AUTH = "auth"
UNREADABLE_PERMISSION = "permission"
UNREADABLE_ROLES = "roles"
UNREADABLE_TRANSIENT = "transient"
UNREADABLE_OTHER = "other"

UNREADABLE_REASONS = {
    UNREADABLE_SCOPE: (
        "Jira refused the connector's authorisation (HTTP 401). Add read:issue-security-level:jira "
        "and read:issue-security-scheme:jira to the Atlassian app, then reconnect the connector"
    ),
    UNREADABLE_AUTH: "Jira refused the connector's authorisation (HTTP 401). Reconnect the connector",
    UNREADABLE_PERMISSION: "the sync account lacks the Administer Jira permission (HTTP 403)",
    UNREADABLE_ROLES: "a level names an application role whose members could not be read this sync",
    UNREADABLE_TRANSIENT: "Jira did not answer this sync (rate limit, server or network error)",
    UNREADABLE_OTHER: "the read failed",
}

# Reasons after which issues keep the grants they were stored with, until a sync reads the
# levels; after any other reason an issue that carries a level is closed to everyone.
UNREADABLE_KEEPS_STORED_ACCESS = frozenset({UNREADABLE_ROLES, UNREADABLE_TRANSIENT})


def unreadable_reason(context: IssueSecurityContext) -> str | None:
    """Why a project's issue security levels cannot be applied, or None when they can (or
    the project has no scheme). One of the ``UNREADABLE_*`` keys."""
    if context.knowledge == SchemeKnowledge.NONE:
        return None
    if context.knowledge == SchemeKnowledge.PRESENT and context.members_readable:
        return None
    if context.read_status == 401:
        return UNREADABLE_SCOPE if context.scope_refused else UNREADABLE_AUTH
    if context.read_status == 403 or context.scheme_forbidden:
        return UNREADABLE_PERMISSION
    if context.knowledge == SchemeKnowledge.PRESENT and context.read_status is None and context.members_by_level:
        return UNREADABLE_ROLES
    if context.defer_checkpoint:
        return UNREADABLE_TRANSIENT
    return UNREADABLE_OTHER


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


def level_reads_alike(context: IssueSecurityContext, level_id: str) -> bool:
    """Whether two issues of one project in this level are read by the same people.

    True only when the level's members were read in full and none of them depends on
    the issue. False for a level with no members listed.
    """
    if not context.enforce or not context.members_readable:
        return False
    holders = context.members_by_level.get(level_id)
    return bool(holders) and all(holder.get("type") in _SAME_ON_EVERY_ISSUE for holder in holders)


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
        groups = application_role_groups(parameter or value, app_roles_mapping)
        if not groups:
            return grants, False, HOLDER_APPLICATION_ROLE
        for group_info in groups:
            add_group(_group_ref(group_info))
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


def apply_issue_access_settled(
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
) -> tuple[list[Permission], list[str], bool]:
    """Set the issue's access rule and return the grants to write, the holder types
    skipped, and whether the result is settled. Settled means the level (or its
    absence) was read in full; a closed issue whose level could not be read is not,
    though both write no grants.

    An issue inherits the record it is filed under. A sub-task is filed under its parent
    issue, whose level Jira copies onto it; a story under its epic only when everyone who
    reads the story reads the epic; any other issue under its project. A parent in another
    project is handled by the processor, which keeps the child on its own project.
    Attachments are handled separately and stay empty.
    """
    if not context.enforce:
        # A personal account has no issue security. A nested issue inherits its
        # parent issue. One with no parent inherits the project, which inherits the app.
        record.inherit_permissions = True
        record.rewrite_permissions = True
        record.inherit_permissions_from_group = False
        return [], [], True
    record.inherit_permissions = True
    record.inherit_permissions_from_group = False
    users = user_by_account_id or {}
    present, level_id = read_security_level(fields)
    security = fields.get("security") if isinstance(fields, dict) else None

    def close() -> tuple[list[Permission], list[str], bool]:
        # RESTRICTED with no grant hides the issue. A transient failure is
        # retried, so the grants already stored stay until that retry.
        record.access_rule = AccessRule.RESTRICTED
        record.rewrite_permissions = not context.defer_checkpoint
        return [], [], False

    def project_open() -> tuple[list[Permission], list[str], bool]:
        record.access_rule = AccessRule.STRICT
        record.rewrite_permissions = True
        return [], [], True

    if present and security is None:
        # A null level is a positive "no level", whatever the scheme state: replace any stale grants.
        return project_open()

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
    return grants, skipped, True


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
    """``apply_issue_access_settled`` without the settled flag: the grants to write
    and the holder types skipped."""
    grants, skipped, _settled = apply_issue_access_settled(
        record, fields, is_subtask=is_subtask, project_key=project_key, context=context,
        user_by_account_id=user_by_account_id, app_roles_mapping=app_roles_mapping,
        reporter_email=reporter_email, assignee_email=assignee_email,
    )
    return grants, skipped


def apply_attachment_access(record: Record) -> list[Permission]:
    """An attachment has no security level of its own. It follows its issue and is not a seed."""
    record.inherit_permissions = True
    record.inherit_permissions_from_group = False
    record.access_rule = AccessRule.STRICT
    record.rewrite_permissions = True
    return []
