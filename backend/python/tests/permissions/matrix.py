"""The connector permission matrix: which connectors combine permissions how, and which change kinds are tested.

`CONNECTORS` classifies every connector under `app/connectors/sources/` by what its writer does, citing the
line. `GROUPS` lists, per family of connectors, the static shapes and change kinds that apply. A scenario
covers a cell by carrying the tag `cell:<group>/<kind>`. `render` writes scenarios/MATRIX.md.
Gaps are reported, not failed, until the corpus is complete (see test_matrix_coverage.py).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .scenario import Scenario

if TYPE_CHECKING:
    from collections.abc import Iterable

# combining rules
UNION, OVERRIDE, INTERSECTION, CONTAINER = "union", "override", "intersection", "container"


@dataclass(frozen=True)
class Connector:
    path: str  # under app/connectors/sources/
    group: str
    rule: str
    evidence: str  # file:line of the writer line that decides the rule
    note: str = ""
    read: bool = True  # False: classified from a grep of the writer, to be confirmed in a deeper read


@dataclass(frozen=True)
class Group:
    statics: tuple[str, ...]
    changes: tuple[str, ...]


# kind -> column of the connector x change-kind table
CATEGORY = {
    "grant": ("space-grant", "site-grant", "scheme-change", "folder-share", "sub-folder-share", "direct-file-share",
              "share-removed", "unique-grant-change", "file-shared", "member-grant", "gate-change"),
    "member": ("member-change", "role-actor-change", "level-members-change", "shared-drive-member"),
    "restriction": ("restriction-change", "edit-only", "level-set", "level-cleared", "reporter-assignee-change"),
    "inherit": ("break-restore-inheritance", "limited-toggle", "limited-folder"),
    "move": ("move-in-space", "move-cross-space", "move-project", "move"),
    "delete": ("delete",),
    "user": ("user-removed", "user-deactivated"),
    "link": ("org-link", "link-redeemed", "anyone-link", "link-discoverability"),
    "visibility": ("visibility-change",),
    "closed": ("scheme-unreadable",),
    "event": ("audit-event",),
}
KIND_CATEGORY = {kind: cat for cat, kinds in CATEGORY.items() for kind in kinds}

GROUPS: dict[str, Group] = {
    "confluence": Group(
        ("space-only", "restricted-page", "nested-restrictions", "named-no-space", "edit-only", "attachment-comment",
         "depth-over-50", "access-class-fallback", "restricted-away-from-sync-account"),
        ("space-grant", "restriction-change", "member-change", "move-in-space", "move-cross-space", "delete",
         "user-removed", "audit-event")),
    "jira": Group(
        ("no-level", "level-holder", "browse-less-holder", "reporter-holder", "sub-task", "story-under-epic",
         "attachment", "service-desk", "personal-token", "bare-application-role", "placeholder-parent"),
        ("scheme-change", "role-actor-change", "level-members-change", "level-set", "level-cleared",
         "reporter-assignee-change", "move-project", "scheme-unreadable", "user-deactivated")),
    "sharepoint": Group(
        ("inheriting", "unique-folder", "nested-unique", "specific-people-share", "org-link", "anyone-link",
         "limited-access"),
        ("site-grant", "member-change", "break-restore-inheritance", "unique-grant-change", "file-shared",
         "org-link", "link-redeemed", "move")),
    "onedrive": Group(
        ("inheriting", "unique-folder", "specific-people-share", "org-link"),
        ("member-change", "break-restore-inheritance", "file-shared", "org-link", "move")),
    "drive": Group(
        ("folder-share", "sub-folder-share", "direct-file-share", "limited-folder", "shared-drive-member", "multi-parent",
         "link-only"),
        ("folder-share", "sub-folder-share", "direct-file-share", "share-removed", "limited-toggle",
         "shared-drive-member", "move", "link-discoverability")),
    "linear": Group(
        ("public-team", "private-team", "guest"),
        ("visibility-change", "member-change", "move", "user-removed")),
    "container": Group(
        ("public-channel", "private-channel", "thread-under-channel", "non-member", "mailbox-other-denied",
         "kb-team-grant", "gated", "not-gated", "mixed-world", "deleted-group", "restricted-member-item"),
        ("member-change", "visibility-change", "delete", "gate-change")),
    "item-acl": Group(
        ("owner", "shared", "not-shared"),
        ()),
}

SRC = "app/connectors/sources/"
CONNECTORS: tuple[Connector, ...] = (
    Connector("atlassian/confluence_cloud", "confluence", INTERSECTION, "atlassian/confluence_cloud/connector.py:4841",
              "space RESTRICTED + inherit; page STRICT or RESTRICTED (:1786)"),
    Connector("atlassian/confluence_datacenter", "confluence", INTERSECTION, "atlassian/confluence_datacenter/connector.py:1074",
              "same writer shape as Cloud"),
    Connector("atlassian/confluence_datacenter_personal", "container", CONTAINER, "atlassian/confluence_datacenter_personal/connector.py:129"),
    Connector("atlassian/jira_cloud", "jira", INTERSECTION, "atlassian/jira_cloud/connector.py:5226",
              "project OPEN + Browse grants; an issue inherits where it is filed; a leveled one keeps a copy of the holders"),
    Connector("atlassian/jira_data_center", "jira", INTERSECTION, "atlassian/jira_data_center/connector.py:4047",
              "same writer shape as Cloud: issues inherit with no grants", read=False),
    Connector("atlassian/jira_cloud_personal", "container", CONTAINER, "atlassian/jira_cloud_personal/connector.py:97"),
    Connector("atlassian/jira_data_center_personal", "container", CONTAINER, "atlassian/jira_data_center_personal/connector.py:66"),
    Connector("microsoft/sharepoint_online", "sharepoint", OVERRIDE, "microsoft/sharepoint_online/connector.py:1680",
              "unique item inherit=False + own role assignments"),
    Connector("microsoft/onedrive", "onedrive", OVERRIDE, "microsoft/onedrive/connector.py:1060",
              "every item inherit=False with the full effective list"),
    Connector("google/drive/team", "drive", UNION, "google/drive/team/drive_access.py:64",
              "My Drive: full list, no inherit; shared drive: inherit unless limited"),
    Connector("google/drive/individual", "container", CONTAINER, "google/drive/individual/connector.py:185"),
    Connector("linear", "linear", CONTAINER, "linear/connector.py:1174",
              "team group RECORD_GROUP_LEVEL, ORG grant (public) or group grant (private); the source doc calls it override"),
    Connector("slack/team", "container", CONTAINER, "slack/team/connector.py:1625"),
    Connector("slack/individual", "container", CONTAINER, "slack/individual/connector.py:332"),
    Connector("google/gmail/team", "container", CONTAINER, "google/gmail/team/connector.py:1787"),
    Connector("google/gmail/individual", "container", CONTAINER, "google/gmail/individual/connector.py:131"),
    Connector("microsoft/outlook", "container", CONTAINER, "microsoft/outlook/connector.py:1119"),
    Connector("microsoft/outlook_individual", "container", CONTAINER, "microsoft/outlook_individual/connector.py:166"),
    Connector("github_teams", "container", CONTAINER, "github_teams/projects.py:526"),
    Connector("github", "container", CONTAINER, "github/connector.py:178"),
    Connector("gitlab", "container", CONTAINER, "gitlab/projects.py:517"),
    Connector("gitlab_personal", "container", CONTAINER, "gitlab_personal/connector.py:107"),
    Connector("notion", "container", CONTAINER, "notion/connector.py:2289", "workspace/team groups RECORD_GROUP_LEVEL, inherit"),
    Connector("notion_personal", "container", CONTAINER, "notion_personal/connector.py:55"),
    Connector("localKB", "container", CONTAINER, "localKB/connector.py:57", "knowledge bases"),
    Connector("local_fs", "container", CONTAINER, "local_fs/connector.py:467"),
    Connector("cifs", "container", CONTAINER, "cifs/connector.py:84"),
    Connector("smb", "container", CONTAINER, "smb/connector.py:86"),
    Connector("azure_blob", "container", CONTAINER, "azure_blob/connector.py:352"),
    Connector("azure_files", "container", CONTAINER, "azure_files/connector.py:327"),
    Connector("dropbox_individual", "container", CONTAINER, "dropbox_individual/connector.py:188"),
    Connector("google_cloud_storage", "container", CONTAINER, "google_cloud_storage/connector.py:312"),
    Connector("minio", "container", CONTAINER, "minio/connector.py:70"),
    Connector("s3", "container", CONTAINER, "s3/connector.py:62"),
    Connector("nextcloud", "container", CONTAINER, "nextcloud/connector.py:532"),
    Connector("mariadb", "container", CONTAINER, "mariadb/connector.py:221"),
    Connector("postgres", "container", CONTAINER, "postgres/connector.py:421"),
    Connector("snowflake", "container", CONTAINER, "snowflake/connector.py:399"),
    Connector("rss", "container", CONTAINER, "rss/connector.py:106"),
    Connector("web", "container", CONTAINER, "web/connector.py:351"),
    Connector("box", "item-acl", OVERRIDE, "box/connector.py:494", "per-item API permissions; a company shared link adds an ORG grant", read=False),
    Connector("dropbox", "item-acl", OVERRIDE, "dropbox/shared_folders.py:115", "shared-folder members as user and group grants", read=False),
    Connector("salesforce", "item-acl", OVERRIDE, "salesforce/connector.py:3722", "records inherit=False; org grant on product groups (:2536)", read=False),
    Connector("servicenow/servicenow", "item-acl", OVERRIDE, "servicenow/servicenow/connector.py:3737", "an article inherits unless it has read criteria: Can Read only is inherit and a grant, Cannot Read or an ownership group computes the users", read=False),
    Connector("zendesk", "item-acl", OVERRIDE, "zendesk/connector.py:2056", "ticket folders inherit=False, opened to the requester", read=False),
    Connector("zammad", "item-acl", OVERRIDE, "zammad/connector.py:1876", "PUBLIC/ARCHIVED/DRAFT explicit, INTERNAL inherits the category", read=False),
    Connector("zoom", "item-acl", OVERRIDE, "zoom/connector.py:907", "per-user groups, inherit=False", read=False),
    Connector("drupal_wiki", "item-acl", OVERRIDE, "drupal_wiki/connector.py:769", "spaces inherit=False with org, user and group grants", read=False),
    Connector("bookstack", "item-acl", OVERRIDE, "bookstack/connector.py:1224", "each chapter and page carries its own access (inherit=False): BookStack decides it apart from its book's", read=False),
    Connector("demo", "item-acl", OVERRIDE, "demo/connector.py:202", "RECORD_LEVEL demo connector", read=False),
)


# Where one access list covers a whole record group, so the record group is the permission scope (rule Container).
# A connector whose rule is Container is that everywhere ("the whole app" for APP_LEVEL apps and KBs); the others
# are Container for the records that match their group, and use their own rule for the exceptions.
CONTAINER_WHERE: dict[str, str] = {
    "atlassian/confluence_cloud": "spaces without page restrictions",
    "atlassian/confluence_datacenter": "spaces without page restrictions",
    "atlassian/jira_cloud": "projects without levels",
    "atlassian/jira_data_center": "projects without levels",
    "linear": "teams",
    "slack/team": "channels (threads inherit the channel)",
    "google/gmail/team": "mailboxes (labels inherit the mailbox)",
    "microsoft/outlook": "mailboxes",
    "github_teams": "repositories and projects",
    "gitlab": "projects",
    "notion": "workspace and team groups",
    "google/drive/team": "shared drives whose items are neither shared nor limited",
}


def container_scope(c: Connector) -> str:
    return CONTAINER_WHERE.get(c.path) or ("the whole app" if c.rule == CONTAINER else "")


# A fragment of the cited line, which must still appear within 3 lines of it: a deleted or moved writer line fails
# test_matrix_coverage.py instead of leaving the table citing nothing.
ANCHORS: dict[str, str] = {
    'atlassian/confluence_cloud': 'inherit_permissions=True,',
    'atlassian/confluence_datacenter': 'webpage_record.access_rule = (',
    'atlassian/confluence_datacenter_personal': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'atlassian/jira_cloud': 'inherit_permissions=True,',
    'atlassian/jira_data_center': 'inherit_permissions=True,',
    'atlassian/jira_cloud_personal': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'atlassian/jira_data_center_personal': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'microsoft/sharepoint_online': 'return False, unique_file_grants(role_grants, graph_permissions)',
    'microsoft/onedrive': 'file_record.inherit_permissions = False',
    'google/drive/team': 'def item_inherits_parent(*, saw_permission_details',
    'google/drive/individual': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'linear': 'permission_model=(',
    'slack/team': 'permission_model=PermissionModel.RECORD_GROUP_LEVE',
    'slack/individual': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'google/gmail/team': 'permission_model=PermissionModel.RECORD_GROUP_LEVE',
    'google/gmail/individual': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'microsoft/outlook': 'permission_model=PermissionModel.RECORD_GROUP_LEVE',
    'microsoft/outlook_individual': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'github_teams': 'permission_model=PermissionModel.RECORD_GROUP_LEVE',
    'github': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'gitlab': 'permission_model=PermissionModel.RECORD_GROUP_LEVE',
    'gitlab_personal': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'notion': 'permission_model=PermissionModel.RECORD_GROUP_LEVE',
    'notion_personal': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'localKB': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'local_fs': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'cifs': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'smb': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'azure_blob': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'azure_files': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'dropbox_individual': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'google_cloud_storage': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'minio': '.with_permission_model(PermissionModel.APP_LEVEL)',
    's3': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'nextcloud': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'mariadb': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'postgres': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'snowflake': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'rss': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'web': '.with_permission_model(PermissionModel.APP_LEVEL)',
    'box': 'entity_type=EntityType.ORG,',
    'dropbox': 'grants.append(Permission(external_id=user.account_',
    'salesforce': 'inherit_permissions=False,',
    'servicenow/servicenow': 'article_record.inherit_permissions = inherit',
    'zendesk': 'inherit_permissions=False,',
    'zammad': '# PUBLIC/ARCHIVED/DRAFT: inherit_permissions=False',
    'zoom': 'inherit_permissions=False,',
    'drupal_wiki': 'inherit_permissions=False,',
    'bookstack': 'inherit_permissions=False,',
    'demo': '.with_permission_model(PermissionModel.RECORD_LEVE',
}


def evidence_problems(sources: Path, connectors: Iterable[Connector] | None = None, window: int = 3) -> list[str]:
    """Citations that no longer hold: the file is gone, or the connector's anchor text is not within `window`
    lines of the cited line."""
    problems: list[str] = []
    for c in connectors if connectors is not None else CONNECTORS:
        file, _, line = c.evidence.rpartition(":")
        path = sources / file
        if not path.is_file():
            problems.append(f"{c.path}: {file} is missing")
            continue
        lines = path.read_text().splitlines()
        at = int(line)
        near = lines[max(0, at - 1 - window):at + window]
        if not any(ANCHORS[c.path] in text for text in near):
            problems.append(f"{c.path}: {file}:{line} no longer holds {ANCHORS[c.path]!r}")
    return problems


def cell_ids(group: str) -> list[tuple[str, str]]:
    g = GROUPS[group]
    return [("static", k) for k in g.statics] + [("change", k) for k in g.changes]


def coverage(scenarios: list[Scenario]) -> dict[tuple[str, str], list[str]]:
    """(group, kind) -> ids of the scenarios tagged `cell:<group>/<kind>`."""
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in scenarios:
        for tag in s.tags:
            if tag.startswith("cell:"):
                group, _, kind = tag.removeprefix("cell:").partition("/")
                found[(group, kind)].append(s.id)
    return found


def gaps(scenarios: list[Scenario]) -> list[tuple[str, str, str]]:
    found = coverage(scenarios)
    return [(group, how, kind) for group in GROUPS for how, kind in cell_ids(group) if (group, kind) not in found]


def unknown_cells(scenarios: list[Scenario]) -> list[str]:
    """Tags that name a cell the matrix does not list (a typo, or a missing row)."""
    known = {(g, k) for g in GROUPS for _, k in cell_ids(g)}
    return sorted({f"{s.id}:{t}" for s in scenarios for t in s.tags if t.startswith("cell:")
                   and tuple(t.removeprefix("cell:").split("/", 1)) not in known})


def render(scenarios: list[Scenario]) -> str:
    found = coverage(scenarios)
    out = ["# Permission coverage matrix",
           "",
           "Generated by `tests/permissions/matrix.py` (run `python -m tests.permissions.matrix` from `backend/python`). "
           "Do not edit.",
           "A cell is covered when a scenario carries the tag `cell:<group>/<kind>`. Gaps are reported, not failed, until the corpus is complete.",
           "",
           "## Connectors and their combining rule (from the writer code)",
           "",
           "| Connector | Group | Rule | Container where | Writer evidence | Classified by |",
           "|---|---|---|---|---|---|"]
    for c in CONNECTORS:
        how = "writer read" if c.read else "grep, to confirm"
        out.append(f"| `{c.path}` | {c.group} | {c.rule} | {container_scope(c) or '-'} | `{SRC}{c.evidence}` | {how} |")
    out += ["", "## Connector x change kind", "",
            "`n` = scenarios covering the category, `gap` = applies and has none, `-` = not applicable.", ""]
    cats = list(CATEGORY)
    out += ["| Connector | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
    for c in CONNECTORS:
        cells = []
        for cat in cats:
            kinds = [k for _, k in cell_ids(c.group) if KIND_CATEGORY.get(k) == cat]
            if not kinds:
                cells.append("-")
                continue
            n = sum(len(found.get((c.group, k), [])) for k in set(kinds))
            cells.append(str(n) if n else "gap")
        out.append(f"| `{c.path}` | " + " | ".join(cells) + " |")
    out += ["", "## Cells by group", "", "| Group | Kind | Type | Scenarios |", "|---|---|---|---|"]
    for group in GROUPS:
        for how, kind in cell_ids(group):
            ids = found.get((group, kind), [])
            out.append(f"| {group} | {kind} | {how} | {', '.join(ids) if ids else '**gap**'} |")
    missing = gaps(scenarios)
    out += ["", f"{len(missing)} gaps of {sum(len(cell_ids(g)) for g in GROUPS)} cells.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    from pathlib import Path

    from .scenario import SCENARIOS_ROOT, load_all

    Path(SCENARIOS_ROOT / "MATRIX.md").write_text(render(load_all()))
