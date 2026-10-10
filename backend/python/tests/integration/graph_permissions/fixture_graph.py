"""The acceptance graph for the permission traversal, described once.

Backend-agnostic on purpose: the same node and edge lists are loaded into Neo4j
and ArangoDB so a case can be asserted to return *identical* results on both.

It is built with raw inserts rather than through the connector write path, so a
shape can be described directly — including ones no connector emits — and loaded
byte-identically into both backends. ``test_write_path.py`` is the counterpart
that drives the real ``DataSourceEntitiesProcessor``; between them the traversal
is proven against both a hand-described graph and a synced one.

Every node carries the fields ArangoDB's collection validators require. Those
run at ``level: strict`` with ``additionalProperties: False``, so a document is
rejected both for missing a required field and for carrying an unknown one —
Neo4j has no equivalent and would accept anything, which is exactly why the
fixture is written to the stricter of the two.

Node ids are readable rather than uuids so a failing assertion names the node,
e.g. ``ex1-r4`` is the first example's restricted page.
"""

ORG = "org-1"
TS = 1_700_000_000_000  # fixed so fixtures are byte-identical between runs

USER_U = "user-u"          # the user every case is evaluated for
USER_V = "user-v"          # a second user, for "U must not see V's things"
GROUP_G = "group-g"        # U is a member
ROLE_R = "role-r"          # U holds it
TEAM_T = "team-t"          # U is a member
ORG_NODE = "orgnode-1"     # org-wide grants

HIERARCHY = "PARENT_CHILD"
ATTACHMENT = "ATTACHMENT"

# Connector vocabularies, which the validators bind by enum.
CONFLUENCE, SHAREPOINT, DRIVE, GITLAB, SLACK, KB = (
    "CONFLUENCE", "SHAREPOINT ONLINE", "DRIVE", "GITLAB", "SLACK", "KB",
)


def _node(kind: str, node_id: str, *, with_org: bool = True, **props) -> dict:
    # The organizations validator defines no orgId and forbids unknown fields.
    base = {"id": node_id}
    if with_org:
        base["orgId"] = ORG
    return {"kind": kind, "id": node_id, "props": {**base, **props}}


def app(node_id: str, name: str, *, connector: str, app_group: str,
        scope: str = "team", **props) -> dict:
    return _node(
        "App", node_id, name=name, type=connector, appGroup=app_group,
        scope=scope, createdAtTimestamp=TS, **{"isActive": True, **props},
    )


# One value, not two booleans, so the fixture cannot express the retired
# (non-strict, restricted) state either — the same invariant the storage enum
# enforces, expressed where the test data is written.
def rg(node_id: str, name: str, *, group_type: str, connector: str,
       rule: str = "OPEN", **props) -> dict:
    defaults = {"isInternal": False, "hideChildren": False}
    return _node(
        "RecordGroup", node_id, groupName=name, groupType=group_type,
        connectorName=connector, createdAtTimestamp=TS,
        accessRule=rule,
        **{**defaults, **props},
    )


def rec(node_id: str, name: str, *, record_type: str = "FILE",
        connector_id: str = "conn-1", origin: str = "CONNECTOR",
        rule: str = "OPEN", **props) -> dict:
    defaults = {"isDeleted": False, "isPlaceholder": False, "isInternal": False}
    return _node(
        "Record", node_id, recordName=name, externalRecordId=f"ext-{node_id}",
        recordType=record_type, origin=origin, connectorId=connector_id,
        createdAtTimestamp=TS, accessRule=rule,
        **{**defaults, **props},
    )


def nr(parent: str, child: str, relationship_type: str = HIERARCHY) -> dict:
    """Hierarchy edge, parent -> child."""
    return {"type": "NODE_RELATION", "from": parent, "to": child,
            "props": {"relationshipType": relationship_type,
                      "createdAtTimestamp": TS}}


def ip(child: str, parent: str) -> dict:
    """Inheritance edge, child -> parent."""
    return {"type": "INHERIT_PERMISSIONS", "from": child, "to": parent,
            "props": {"createdAtTimestamp": TS}}


def bt(child: str, parent: str, entity_type: str | None = None) -> dict:
    props = {"createdAtTimestamp": TS}
    if entity_type:
        props["entityType"] = entity_type
    return {"type": "BELONGS_TO", "from": child, "to": parent, "props": props}


def perm(grantee: str, node: str, *, grant_type: str = "USER",
         role: str = "READER") -> dict:
    return {"type": "PERMISSION", "from": grantee, "to": node,
            "props": {"type": grant_type, "role": role,
                      "createdAtTimestamp": TS}}


def authenticated_as(user: str, source_user: str, connector_id: str) -> dict:
    """The user authenticated this connector as another source account (shadow user)."""
    return {"type": "AUTHENTICATED_AS", "from": user, "to": source_user,
            "props": {"connectorId": connector_id, "orgId": ORG, "createdAtTimestamp": TS}}


def user_app(user: str, app_id: str) -> dict:
    # syncState and lastSyncUpdate are required by the edge validator.
    return {"type": "USER_APP_RELATION", "from": user, "to": app_id,
            "props": {"syncState": "COMPLETED", "lastSyncUpdate": TS}}


def _principals() -> tuple[list, list]:
    nodes = [
        _node("User", USER_U, email="u@example.com", fullName="U"),
        _node("User", USER_V, email="v@example.com", fullName="V"),
        _node("Group", GROUP_G, name="Engineering"),
        _node("Role", ROLE_R, name="Reader role", externalRoleId="ext-role-r",
              connectorName=DRIVE, connectorId="drive-conn",
              createdAtTimestamp=TS),
        _node("Teams", TEAM_T, name="Team T"),
        _node("Organization", ORG_NODE, with_org=False, name="Acme",
              accountType="enterprise", isActive=True),
    ]
    # U's memberships, as production stores them: a USER permission onto a
    # group, role or team, and belongsTo onto the organization. Every other test
    # passes U's grantees as a constant; the access context derives them from
    # these edges.
    edges = [
        perm(USER_U, GROUP_G), perm(USER_U, ROLE_R), perm(USER_U, TEAM_T),
        bt(USER_U, ORG_NODE, entity_type="ORGANIZATION"),
    ]
    return nodes, edges


def example_one_confluence() -> tuple[list, list]:
    """Example 1 of the permission model doc: every node needs all of its parents.

    Accessible for U: 1, 2, 3, 5. Hidden: 4 (restricted, no grant) and 6 (below 4).
    """
    a, conn = "ex1-app", "confluence-conn"
    space = dict(group_type="CONFLUENCE_SPACES", connector=CONFLUENCE)
    page = dict(record_type="CONFLUENCE_PAGE", connector_id=conn)
    nodes = [
        app(a, "Confluence", connector=CONFLUENCE, app_group="Atlassian"),
        # Every space carries its own permission list, so every space is
        # restricted — the flag means "my own grants are required, inheriting
        # from my parent is not enough". A space open to the whole org is no
        # exception: it still has a grant, just to a broad group.
        rg("ex1-rg1", "Space 1", rule="RESTRICTED", **space),
        rg("ex1-rg2", "Space 2", rule="RESTRICTED", **space),
        rec("ex1-r3", "Page 3", rule="STRICT", **page),
        rec("ex1-r4", "Page 4 (restricted)", rule="RESTRICTED", **page),
        rec("ex1-r5", "Page 5", rule="STRICT", **page),
        rec("ex1-r6", "Page 6", rule="STRICT", **page),
    ]
    edges = [
        user_app(USER_U, a),
        # V has Confluence access but no space permission anywhere: the spaces
        # inherit from the App, so without the restriction flag requiring a
        # grant as well, V would see every space.
        user_app(USER_V, a),
        nr(a, "ex1-rg1"), nr(a, "ex1-rg2"),
        nr("ex1-rg1", "ex1-r3"), nr("ex1-rg1", "ex1-r4"),
        nr("ex1-r4", "ex1-r6"), nr("ex1-rg2", "ex1-r5"),
        # Spaces DO inherit from the App, as Example 1 states. That leaks
        # nothing: because a space is restricted, inheritance alone never
        # admits it — a user with app access but no space grant still sees
        # nothing. The leak is counting the app relation itself as a grant.
        ip("ex1-rg1", a), ip("ex1-rg2", a),
        ip("ex1-r3", "ex1-rg1"), ip("ex1-r4", "ex1-rg1"),
        ip("ex1-r6", "ex1-r4"), ip("ex1-r5", "ex1-rg2"),
        perm(USER_U, "ex1-rg1"), perm(USER_U, "ex1-rg2"),
        bt("ex1-r3", "ex1-rg1"), bt("ex1-r4", "ex1-rg1"),
        bt("ex1-r6", "ex1-rg1"), bt("ex1-r5", "ex1-rg2"),
    ]
    return nodes, edges


def example_two_sharepoint() -> tuple[list, list]:
    """Example 2 of the permission model doc: parent permission not required.

    Accessible: 1, 2, 4, 5, 6, 7, 8. Hidden: 3 (no grant, no inheritance).
    Node 6 is the below-a-gap case: granted directly under an inaccessible 3.
    """
    a, conn = "ex2-app", "sharepoint-conn"
    site = dict(group_type="SHAREPOINT_SITE", connector=SHAREPOINT)
    item = dict(record_type="FILE", connector_id=conn)
    nodes = [
        app(a, "SharePoint", connector=SHAREPOINT, app_group="Microsoft"),
        rg("ex2-rg1", "Site 1", **site), rg("ex2-rg2", "Site 2", **site),
        rec("ex2-r3", "Folder 3", **item), rec("ex2-r4", "File 4", **item),
        rec("ex2-r5", "File 5", **item), rec("ex2-r6", "File 6", **item),
        rec("ex2-r7", "File 7", **item), rec("ex2-r8", "File 8", **item),
    ]
    edges = [
        user_app(USER_U, a),
        nr(a, "ex2-rg1"), nr(a, "ex2-rg2"),
        nr("ex2-rg1", "ex2-r3"), nr("ex2-rg1", "ex2-r4"),
        nr("ex2-r3", "ex2-r6"), nr("ex2-r6", "ex2-r7"), nr("ex2-r6", "ex2-r8"),
        nr("ex2-rg2", "ex2-r5"),
        ip("ex2-rg1", a), ip("ex2-rg2", a),
        ip("ex2-r5", "ex2-rg2"), ip("ex2-r7", "ex2-r6"), ip("ex2-r8", "ex2-r6"),
        perm(USER_U, "ex2-r4"), perm(USER_U, "ex2-r6"),
        bt("ex2-r3", "ex2-rg1"), bt("ex2-r4", "ex2-rg1"), bt("ex2-r6", "ex2-rg1"),
        bt("ex2-r7", "ex2-rg1"), bt("ex2-r8", "ex2-rg1"), bt("ex2-r5", "ex2-rg2"),
    ]
    return nodes, edges


def declarations() -> tuple[list, list]:
    """APP_LEVEL and RECORD_GROUP_LEVEL declarations.

    Everything under a declaration is reachable even though the nodes
    themselves neither inherit nor hold a grant. APP_LEVEL is only valid on an
    App; the record-group validator excludes it deliberately.
    """
    app_level, rgl, conn = "dec-app", "dec-rgl-app", "gitlab-conn"
    repo = dict(group_type="REPOSITORY", connector=GITLAB)
    project = dict(group_type="PROJECT", connector=GITLAB)
    issue = dict(record_type="TICKET", connector_id=conn)
    nodes = [
        app(app_level, "GitLab (app level)", connector=GITLAB,
            app_group="GitLab", permissionModel="APP_LEVEL"),
        rg("dec-rg1", "Repo 1", rule="RESTRICTED", **repo),
        rec("dec-r1", "Issue 1", rule="RESTRICTED", **issue),
        app(rgl, "GitLab (group level)", connector=GITLAB, app_group="GitLab"),
        rg("dec-rg2", "Project", permissionModel="RECORD_GROUP_LEVEL", **project),
        rec("dec-r2", "Work item", **issue),
        rg("dec-rg3", "Nested group", **project),
        rec("dec-r3", "Nested item", **issue),
        rg("dec-rg4", "Undeclared sibling", **project),
        # Declared but granted to nobody: a declaration opens only a group the
        # user can already open, so dec-r5 must stay out of reach.
        rg("dec-rg5", "Declared, ungranted", permissionModel="RECORD_GROUP_LEVEL", **project),
        rec("dec-r5", "Item in an ungranted declared group", **issue),
    ]
    edges = [
        user_app(USER_U, app_level), user_app(USER_U, rgl),
        nr(app_level, "dec-rg1"), nr("dec-rg1", "dec-r1"),
        bt("dec-r1", "dec-rg1"),
        nr(rgl, "dec-rg2"), nr("dec-rg2", "dec-r2"),
        nr("dec-rg2", "dec-rg3"), nr("dec-rg3", "dec-r3"),
        nr(rgl, "dec-rg4"),
        perm(USER_U, "dec-rg2"),
        bt("dec-r2", "dec-rg2"), bt("dec-r3", "dec-rg3"),
        nr(rgl, "dec-rg5"), nr("dec-rg5", "dec-r5"), bt("dec-r5", "dec-rg5"),
    ]
    return nodes, edges


def collection() -> tuple[list, list]:
    """A knowledge base.

    Items neither inherit nor hold grants of their own; access and role come
    from the explicit grant on the collection.
    """
    kb, kb_no_grant = "kb-1", "kb-2"
    item = dict(record_type="FILE", origin="UPLOAD", connector_id=kb)
    nodes = [
        app(kb, "My Collection", connector=KB, app_group="Local Storage",
            scope="personal", hideConnector=True),
        rec("kb-f1", "Folder 1", **item), rec("kb-f2", "Folder 2", **item),
        # The status-filter test needs a status list spanning partition kinds,
        # so the collection carries two of them. kb-f1 deliberately carries
        # none: the filter also gates on nodeType == "record", so a genuinely
        # folder-typed node would be excluded twice over and the test would
        # pass for the wrong reason.
        rec("kb-r3", "Doc 3", indexingStatus="QUEUED", **item),
        rec("kb-r4", "Doc 4", indexingStatus="FAILED", **item),
        app(kb_no_grant, "Someone else's collection", connector=KB,
            app_group="Local Storage", scope="personal", hideConnector=True),
        rec("kb2-r1", "Unreachable doc", **{**item, "connector_id": kb_no_grant}),
    ]
    # The shape the real writer produces, verified against a live instance:
    # every item gets BELONGS_TO the App tagged `entityType: KB`, and hierarchy
    # edges exist only *between records*. There is no NODE_RELATION from the
    # App to its root items -- `kb_service` writes "records+files+isOfType,
    # belongsTo->apps/<kbId>".
    #
    # Do not add `nr(kb, "kb-f1")`: no connector emits it, and with that one
    # invented edge a collection browses correctly here while returning
    # nothing at all on a real instance.
    edges = [
        perm(USER_U, kb, role="WRITER"),
        nr("kb-f1", "kb-f2"), nr("kb-f2", "kb-r3"),
        bt("kb-f1", kb, "KB"), bt("kb-f2", kb, "KB"),
        bt("kb-r3", kb, "KB"), bt("kb-r4", kb, "KB"),
        bt("kb2-r1", kb_no_grant, "KB"),
    ]
    return nodes, edges


def placement() -> tuple[list, list]:
    """Where a node below a gap appears: three worked cases.

    pl-r6 -> under its own group (rg1); pl-r9 -> under the App, its own group
    being unreachable; pl-r11 -> under rg1, not under the nearest accessible
    ancestor record.

    pl-r12 -> under pl-rg3, never under the App: its group is reachable only
    through a *different* grantee (group-g) than the one granting the record
    (user-u), which is what a grant check bound to the record's grantee gets
    wrong.
    """
    a, conn = "pl-app", "drive-conn"
    group = dict(group_type="DRIVE", connector=DRIVE)
    item = dict(record_type="FILE", connector_id=conn)
    nodes = [
        app(a, "Placement", connector=DRIVE, app_group="Google Workspace"),
        rg("pl-rg1", "Group 1", **group),
        rg("pl-rg2", "Group 2 (unreachable)", **group),
        rec("pl-r3", "Gap folder", **item),
        # FAILED here and COMPLETED on pl-r11: a status list must admit one and
        # exclude the other from the same partition, so a filter that ignored
        # the list and returned every status would fail.
        rec("pl-r6", "Granted below gap", indexingStatus="FAILED", **item),
        rec("pl-r9", "Granted in unreachable group", **item),
        rec("pl-r10", "Second gap", **item),
        # COMPLETED, so a FAILED+QUEUED status list must leave it out. Without a
        # record excluded *by the list* in this partition, a filter that ignored
        # the list entirely would still pass.
        rec("pl-r11", "Granted below second gap", indexingStatus="COMPLETED", **item),
        rec("pl-r5", "Reachable child", **item),
        rg("pl-rg3", "Group 3 (granted to a group)", **group),
        rec("pl-r12", "Granted in group-granted group", **item),
        # STRICT under the chain-top pl-r6: the gap pl-r3 above makes it
        # inadmissible however it inherits, since STRICT needs every ancestor.
        rec("pl-r13", "Strict child of a chain-top", rule="STRICT", **item),
        # Granted groups and records below the unreachable pl-rg2: pl-rg4 is
        # open on its own, so pl-r14 belongs under it, not under the App.
        rg("pl-rg4", "Group 4 (granted, below a gap)", **group),
        rec("pl-r14", "Granted in a granted group below a gap", **item),
        # A chain-top whose own group pl-rg4 opens only through its own grant:
        # it lists under pl-rg4, never under the App.
        rec("pl-f5", "Closed folder in a granted group", **item),
        rec("pl-r15", "Granted below a closed folder", **item),
    ]
    edges = [
        user_app(USER_U, a),
        nr(a, "pl-rg1"), nr(a, "pl-rg2"), nr(a, "pl-rg3"),
        nr("pl-rg1", "pl-r3"), nr("pl-r3", "pl-r6"),
        nr("pl-rg2", "pl-r9"),
        nr("pl-rg1", "pl-r5"), nr("pl-r5", "pl-r10"), nr("pl-r10", "pl-r11"),
        nr("pl-rg3", "pl-r12"),
        ip("pl-rg1", a), ip("pl-r5", "pl-rg1"),
        perm(USER_U, "pl-r6"), perm(USER_U, "pl-r9"), perm(USER_U, "pl-r11"),
        perm(GROUP_G, "pl-rg3", grant_type="GROUP"), perm(USER_U, "pl-r12"),
        bt("pl-r3", "pl-rg1"), bt("pl-r6", "pl-rg1"), bt("pl-r5", "pl-rg1"),
        bt("pl-r10", "pl-rg1"), bt("pl-r11", "pl-rg1"), bt("pl-r9", "pl-rg2"),
        bt("pl-r12", "pl-rg3"),
        nr("pl-r6", "pl-r13"), ip("pl-r13", "pl-r6"), bt("pl-r13", "pl-rg1"),
        nr("pl-rg2", "pl-rg4"), nr("pl-rg4", "pl-r14"),
        perm(USER_U, "pl-rg4"), perm(USER_U, "pl-r14"),
        bt("pl-rg4", "pl-rg2"), bt("pl-r14", "pl-rg4"),
        nr("pl-rg4", "pl-f5"), nr("pl-f5", "pl-r15"), perm(USER_U, "pl-r15"),
        bt("pl-f5", "pl-rg4"), bt("pl-r15", "pl-rg4"),
    ]
    return nodes, edges


def grant_paths() -> tuple[list, list]:
    """All five grant paths.

    The org grant must match a real org node, and the team must be resolved
    even though the grant is on the team, not the user.
    """
    a, conn = "gp-app", "drive-conn"
    item = dict(record_type="FILE", connector_id=conn)
    granted = ("gp-user", "gp-group", "gp-role", "gp-team", "gp-org", "gp-other-org")
    nodes = [
        app(a, "Grant paths", connector=DRIVE, app_group="Google Workspace"),
        rg("gp-rg1", "Group", group_type="DRIVE", connector=DRIVE),
        # Sizes chosen so numeric and lexicographic order disagree completely:
        # numeric ascending is 9, 10, 100, 2000 while string ascending is "10",
        # "100", "2000", "9". A size sort that compared text could not pass by
        # accident. gp-org keeps no size, so nulls-last is exercised with them.
        rec("gp-user", "Granted to user", sizeInBytes=2000, **item),
        rec("gp-group", "Granted to group", sizeInBytes=9, **item),
        rec("gp-role", "Granted to role", sizeInBytes=100, **item),
        rec("gp-team", "Granted to team", sizeInBytes=10, **item),
        rec("gp-org", "Granted to org", **item),
        rec("gp-other-org", "Granted to another org", **item),
        # Hangs straight off the App with no record group: the App-direct
        # partition's only member here.
        rec("gp-direct", "Directly under the App", **item),
    ]
    edges = [
        user_app(USER_U, a),
        nr(a, "gp-direct"), ip("gp-direct", a),
        nr(a, "gp-rg1"),
        *[nr("gp-rg1", n) for n in granted],
        *[bt(n, "gp-rg1") for n in granted],
        perm(USER_U, "gp-user", grant_type="USER"),
        perm(GROUP_G, "gp-group", grant_type="GROUP"),
        perm(ROLE_R, "gp-role", grant_type="ROLE"),
        perm(TEAM_T, "gp-team", grant_type="TEAM"),
        perm(ORG_NODE, "gp-org", grant_type="ORG"),
    ]
    return nodes, edges


def exclusions() -> tuple[list, list]:
    """Deleted, placeholder and hideChildren.

    ex-hmsg is granted but sits in ex-hrg, an unreachable group beneath the
    hidden channel: the App fallback must not surface it one level up.
    """
    a, conn = "ex-app", "slack-conn"
    channel = dict(group_type="SLACK_CHANNEL", connector=SLACK)
    msg = dict(record_type="MESSAGE", connector_id=conn)
    nodes = [
        app(a, "Slack", connector=SLACK, app_group="Slack"),
        rg("ex-rg1", "Open channel", **channel),
        rec("ex-deleted", "Deleted record", isDeleted=True, **msg),
        rec("ex-live-child", "Child of deleted", **msg),
        rec("ex-stub", "Placeholder ancestor", isPlaceholder=True, **msg),
        rec("ex-under-stub", "Real child under stub", **msg),
        rg("ex-hidden", "Private channel", hideChildren=True, **channel),
        rec("ex-message", "Message", **msg),
        rg("ex-hrg", "Thread group under hidden channel", **channel),
        rec("ex-hmsg", "Granted message under hidden channel", **msg),
    ]
    edges = [
        user_app(USER_U, a),
        nr(a, "ex-rg1"), nr(a, "ex-hidden"),
        nr("ex-rg1", "ex-deleted"), nr("ex-deleted", "ex-live-child"),
        nr("ex-rg1", "ex-stub"), nr("ex-stub", "ex-under-stub"),
        nr("ex-hidden", "ex-message"),
        nr("ex-hidden", "ex-hrg"), nr("ex-hrg", "ex-hmsg"),
        perm(USER_U, "ex-hmsg"), bt("ex-hmsg", "ex-hrg"),
        ip("ex-rg1", a),
        ip("ex-deleted", "ex-rg1"), ip("ex-live-child", "ex-deleted"),
        # A stub inherits, or the read rule would hide a placeholder listings show.
        ip("ex-stub", "ex-rg1"), ip("ex-under-stub", "ex-stub"),
        ip("ex-hidden", a), ip("ex-message", "ex-hidden"),
        bt("ex-deleted", "ex-rg1"), bt("ex-live-child", "ex-rg1"),
        bt("ex-stub", "ex-rg1"), bt("ex-under-stub", "ex-rg1"),
        bt("ex-message", "ex-hidden"),
    ]
    return nodes, edges


def shared_with_me() -> tuple[list, list]:
    """Drive's second hierarchy parent.

    swm-x is reachable by both parents; swm-y only through Shared with Me,
    its drive folder being unreachable.
    """
    a, conn = "swm-app", "drive-conn"
    group = dict(group_type="DRIVE", connector=DRIVE)
    item = dict(record_type="FILE", connector_id=conn)
    nodes = [
        app(a, "Drive", connector=DRIVE, app_group="Google Workspace"),
        rg("swm-drive", "Shared Drive", **group),
        rg("swm-inbox", "U's Shared with Me", isInternal=True, **group),
        rg("swm-ainbox", "A's Shared with Me", isInternal=True, **group),
        rec("swm-f1", "Folder", **item),
        rec("swm-f2", "Unreachable folder", **item),
        rec("swm-x", "File in both places", **item),
        rec("swm-y", "File only in Shared with Me", rule="STRICT", **item),
        rec("swm-z", "Shared into another user's inbox", **item),
    ]
    edges = [
        user_app(USER_U, a),
        nr(a, "swm-drive"), nr(a, "swm-inbox"),
        nr("swm-drive", "swm-f1"), nr("swm-f1", "swm-x"),
        nr("swm-drive", "swm-f2"), nr("swm-f2", "swm-y"),
        nr("swm-inbox", "swm-x"), nr("swm-inbox", "swm-y"),
        ip("swm-drive", a), ip("swm-f1", "swm-drive"), ip("swm-x", "swm-f1"),
        ip("swm-y", "swm-inbox"),
        perm(USER_U, "swm-drive"), perm(USER_U, "swm-inbox"),
        # Shared with U, so swm-x lists under the inbox as well as its folder,
        # which is what gives it two navigable trails.
        perm(USER_U, "swm-x"),
        bt("swm-f1", "swm-drive"), bt("swm-f2", "swm-drive"),
        bt("swm-x", "swm-drive"), bt("swm-y", "swm-drive"),
        # _link_record_to_group writes a BELONGS_TO beside the hierarchy edge
        # for every shared-with-me group, so a shared record really has two own
        # groups. Without these the fixture was the only place it had one.
        bt("swm-x", "swm-inbox"), bt("swm-y", "swm-inbox"),
        # Another user's inbox, so U holds no grant on it, and its id sorts
        # before swm-drive. swm-z therefore has two own groups of which only
        # swm-drive is openable, and both hierarchy parents are unreachable --
        # the one arrangement that forces placement onto the own group.
        nr(a, "swm-ainbox"), nr("swm-ainbox", "swm-z"), nr("swm-f2", "swm-z"),
        perm(USER_U, "swm-z"),
        bt("swm-z", "swm-ainbox"), bt("swm-z", "swm-drive"),
    ]
    return nodes, edges


def no_app_access() -> tuple[list, list]:
    """The connector gate: grants inside an app U cannot reach."""
    a, conn = "gate-app", "drive-conn"
    nodes = [
        app(a, "Unreachable connector", connector=DRIVE,
            app_group="Google Workspace"),
        rg("gate-rg1", "Group", group_type="DRIVE", connector=DRIVE),
        rec("gate-r3", "Granted but gated", record_type="FILE", connector_id=conn),
    ]
    edges = [
        nr(a, "gate-rg1"), nr("gate-rg1", "gate-r3"),
        perm(USER_U, "gate-rg1"), perm(USER_U, "gate-r3"),
        bt("gate-r3", "gate-rg1"),
    ]
    return nodes, edges


def flag_branches() -> tuple[list, list]:
    """The rule branches no other scenario decides.

    Nothing else is STRICT, granted and non-inheriting, so the grant disjunct
    of the STRICT branch is never the deciding term elsewhere; and flag-open
    proves an OPEN node is admitted by inheritance alone. flag-restricted
    differs from flag-granted only in accessRule, which makes them a pair: same
    grant, same missing inheritance edge, opposite outcome. All four could
    break without failing a single other assertion.
    """
    a, conn = "flag-app", "confluence-conn"
    space = dict(group_type="CONFLUENCE_SPACES", connector=CONFLUENCE)
    page = dict(record_type="CONFLUENCE_PAGE", connector_id=conn)
    nodes = [
        app(a, "Flags", connector=CONFLUENCE, app_group="Atlassian"),
        rg("flag-rg", "Space", **space),
        # OPEN: ancestors are irrelevant and inheritance alone admits it with no
        # grant of its own.
        rec("flag-open", "Open, inheriting, ungranted", **page),
        # STRICT and granted with no inheritance edge at all — admitted on the
        # grant alone.
        rec("flag-granted", "Strict, granted, not inheriting", rule="STRICT", **page),
        # Neither inheriting nor granted: the control that proves the STRICT
        # branch can still reject.
        rec("flag-neither", "Strict with nothing", rule="STRICT", **page),
        # The pair to flag-granted: identical but for accessRule.
        # RESTRICTED demands inheritance AND a grant, so the grant alone must
        # not admit it. Every other RESTRICTED node in the fixture either
        # inherits or holds no grant, so this is the only place that conjunct
        # is what decides.
        rec("flag-restricted", "Restricted, granted, not inheriting",
            rule="RESTRICTED", **page),
    ]
    edges = [
        user_app(USER_U, a),
        nr(a, "flag-rg"),
        nr("flag-rg", "flag-open"), nr("flag-rg", "flag-granted"),
        nr("flag-rg", "flag-neither"), nr("flag-rg", "flag-restricted"),
        ip("flag-rg", a),
        ip("flag-open", "flag-rg"),
        perm(USER_U, "flag-granted"), perm(USER_U, "flag-restricted"),
        bt("flag-open", "flag-rg"), bt("flag-granted", "flag-rg"),
        bt("flag-neither", "flag-rg"), bt("flag-restricted", "flag-rg"),
    ]
    return nodes, edges


def deleted_content() -> tuple[list, list]:
    """A deleted record leaves every part of the response.

    Both would be returned if they were live — one inherits inside an open
    connector group, the other sits in the collection whose grant opens it — so
    their absence is the exclusion working rather than the rule hiding them for
    some unrelated reason. Neither is granted, so no seed set moves, and every
    exact listing already pinned elsewhere stays correct only while the
    exclusion holds.
    """
    nodes = [
        rec("del-in-group", "Deleted below group", record_type="FILE",
            connector_id="drive-conn", isDeleted=True),
        rec("del-in-kb", "Deleted doc", record_type="FILE", origin="UPLOAD",
            connector_id="kb-1", isDeleted=True),
    ]
    edges = [
        nr("pl-rg1", "del-in-group"), ip("del-in-group", "pl-rg1"),
        bt("del-in-group", "pl-rg1"),
        # Tagged like every other collection item, or the KB membership test
        # would exclude it on the tag rather than on isDeleted, and the
        # deleted-record tests would pass for the wrong reason.
        bt("del-in-kb", "kb-1", "KB"),
    ]
    return nodes, edges


DEEP_CHAIN_LENGTH = 25


def deep_chain() -> tuple[list, list]:
    """A chain deeper than 20 hops, which the upward walk must reach.

    ``_KH_V2_MAX_UP_DEPTH`` is 50 for exactly this case. A bound set too low
    does not raise: the walk simply
    finds no path, ``admitted`` comes back false, and a node the user may open
    answers 404 — no error and no leak, just content that vanishes past a depth.

    It gets its own App. Hanging it off an existing one changes that App's exact
    traversal set, and several of those are pinned deliberately — the flag
    branches especially, whose whole point is that they "could break without
    failing a single other assertion". Twenty-six extra ids in such a set would
    dilute the very tightness that makes it useful. The chain inherits from the
    App rather than holding a grant, so it adds no seed.
    """
    page = dict(record_type="CONFLUENCE_PAGE", connector_id="confluence-conn")
    nodes = [
        app("deep-app", "Deep", connector=CONFLUENCE, app_group="Atlassian"),
        rg("deep-rg", "Deep space", group_type="CONFLUENCE_SPACES",
           connector=CONFLUENCE),
    ]
    edges = [
        user_app(USER_U, "deep-app"),
        nr("deep-app", "deep-rg"), ip("deep-rg", "deep-app"),
    ]
    parent = "deep-rg"
    for level in range(1, DEEP_CHAIN_LENGTH + 1):
        node_id = f"deep-{level}"
        nodes.append(rec(node_id, f"Deep page {level}", **page))
        edges += [nr(parent, node_id), ip(node_id, parent), bt(node_id, "deep-rg")]
        parent = node_id
    return nodes, edges


ORG_B = "org-2"
USER_B = "user-b"


def other_org() -> tuple[list, list]:
    """Another org's content, reachable to its own user and nobody else.

    The org boundary is not the membership graph, so every path that could leak
    is present: user-b holds grants and an app relation, the shapes match the
    other scenarios exactly, and only ``orgId`` differs. Orgs already share graph
    state for taxonomy and people, which is what makes an org-blind lookup here
    a live risk rather than a hypothetical one.

    ``group-g`` — a group U belongs to — holds a grant on the org-B App itself.
    That is the shape the ``orgId`` predicate actually defends: a shared
    principal reaching across orgs. Without it the scenario is untestable, since
    a grant held only by user-b never reaches U's gate by any route, and the
    isolation tests pass whether or not the predicate is there. Measured: with
    the grant removed, dropping the gate's ``orgId`` check fails nothing. The
    grant must be on the App, not on a group inside it, because the gate matches
    permissions onto Apps.
    """
    nodes = [
        _node("User", USER_B, orgId=ORG_B, email="b@example.com", fullName="B"),
        app("orgb-app", "Org B connector", connector=DRIVE,
            app_group="Google Workspace", orgId=ORG_B),
        rg("orgb-rg", "Org B group", group_type="DRIVE", connector=DRIVE,
           orgId=ORG_B),
        rec("orgb-r1", "Org B confidential doc", record_type="FILE",
            connector_id="drive-conn", orgId=ORG_B),
    ]
    edges = [
        user_app(USER_B, "orgb-app"),
        perm(GROUP_G, "orgb-app"),
        nr("orgb-app", "orgb-rg"), nr("orgb-rg", "orgb-r1"),
        ip("orgb-rg", "orgb-app"), ip("orgb-r1", "orgb-rg"),
        perm(USER_B, "orgb-rg"), perm(USER_B, "orgb-r1"),
        bt("orgb-r1", "orgb-rg"),
    ]
    return nodes, edges


SCENARIOS = (
    _principals,
    example_one_confluence,
    example_two_sharepoint,
    declarations,
    collection,
    placement,
    grant_paths,
    exclusions,
    shared_with_me,
    no_app_access,
    flag_branches,
    deleted_content,
    deep_chain,
    other_org,
)


def build_fixture() -> tuple[list[dict], list[dict]]:
    """All scenarios merged into one node list and one edge list."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()
    for scenario in SCENARIOS:
        scenario_nodes, scenario_edges = scenario()
        for node in scenario_nodes:
            if node["id"] in seen:
                raise ValueError(f"duplicate fixture node id: {node['id']}")
            seen.add(node["id"])
            nodes.append(node)
        edges.extend(scenario_edges)

    for edge in edges:
        for end in ("from", "to"):
            if edge[end] not in seen:
                raise ValueError(f"edge {edge['type']} references unknown node {edge[end]!r}")
    return nodes, edges
