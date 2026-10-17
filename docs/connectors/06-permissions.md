# Connector permissions

PipesHub answers a user only from what that user can read in the source. The connector is what makes that true: it writes the hierarchy, the grants and the inheritance, and the platform decides access from them with one check. A mistake here either hides content from people who can see it or shows content to people who cannot. Showing is worse. When you cannot tell which way a choice errs, pick the one that hides, and tell the human.

Code paths are relative to `backend/python/app/`, test paths to `backend/python/`; `integration-tests/` is at the repository root.

## How access is decided

The graph is a tree per connector instance:

```text
App ── RecordGroup ── RecordGroup ... ── Record ── Record ...
```

The processor writes three kinds of edge from what you give it:

- **Hierarchy** (`nodeRelations`, `PARENT_CHILD` or `ATTACHMENT`, parent → child): what the knowledge hub browses. Other relation types (links between records) go to `recordLinks` and play no part in access.
- **Inheritance** (`inheritPermissions`, child → its direct parent): this node takes access from its parent.
- **Grants** (`permission`, user / group / role / org → node).

A user can read a node when all of these hold:

1. **The connector gate.** The user, or a group, role or team they belong to, or their org, holds an edge to the App: a grant, or the user's `USER_APP_RELATION`. `on_new_app_users` writes that relation for every `AppUser` you pass, matched to a PipesHub user by email; when none matches, it creates an inactive PipesHub user and makes it a member of the org. A user who is not registered as an app user sees nothing from the connector, whatever the grants say. Set `is_guest=True` on source guests and external accounts: otherwise they join the org and, once active, receive every `ORG` grant.
2. **The node is live.** Not deleted, not a placeholder, and in the user's org.
3. **The node's access rule passes.** Every node has an `access_rule`:

| `access_rule` | Readable when |
| --- | --- |
| `OPEN` (default) | it inherits from a readable parent, **or** the user has a grant on it. A direct grant opens it even if nothing above it is readable (a file shared with you inside a folder you cannot open). |
| `STRICT` | it inherits from a readable parent, or the user has a grant on it, **and** every ancestor up to the App is readable. A direct grant does not open it on its own. |
| `RESTRICTED` | it inherits from a readable parent **and** the user has a grant on it. |

A missing `access_rule` reads as `OPEN`. An unrecognised value hides the node. A `RESTRICTED` node with `inherit_permissions=False` is never readable. Below a node opened only by a direct grant, only `OPEN` descendants are readable.

Two shortcuts skip the per-node rule:

- An App declared `APP_LEVEL` (and every knowledge base) opens every record that belongs to it to anyone who passes the gate.
- A `RecordGroup` with `permission_model = RECORD_GROUP_LEVEL` opens every group nested under it, and every record that belongs to any of them, to anyone with a direct grant on the group or access to it from the App, whatever those nodes' own `access_rule` or inheritance. A direct grant on the group opens it even if its own rule would not.

The check is `check_access` in `services/graph_db/interface/graph_db_provider.py`, implemented per backend in the Neo4j and Arango providers. It fails closed: on error it raises rather than returning an empty or a full answer. You never call it from a connector.

## What a connector sets

### On the connector: `with_permission_model`

| Model | Use when |
| --- | --- |
| `RECORD_LEVEL` (default) | The source has any per-item or per-container access. Almost every enterprise source. |
| `APP_LEVEL` | Everyone who can reach the connector may read everything in it: a storage bucket, a database, an RSS feed, a personal connector of one user's own data. The App gate is then the only check. |

`RECORD_GROUP_LEVEL` is rejected on the connector; it is set on individual groups.

### On each `RecordGroup`

| Field | Meaning |
| --- | --- |
| `parent_external_group_id` | The parent group, for nested containers. Missing parents become `RESTRICTED` stubs until they sync. |
| `inherit_permissions` (default `False`) | Top-level group: take access from the App, so everyone who passes the gate can read it. Nested group: take access from the parent group. |
| `access_rule` | As above. |
| `permission_model = RECORD_GROUP_LEVEL` | Only when every nested group and every record under the group has exactly the group's audience. One record or sub-group with its own ACL under such a group is over-shared. |
| permissions (the second element of the tuple you pass to `on_new_record_groups`) | Who has access to the container itself. `[]`: nobody has a direct grant, and stored grants are cleared. `None`: keep the stored grants. `None` does **not** keep inheritance or `access_rule`: those are still written from the object you pass, so an unreadable group sent as `None` with `inherit_permissions=True` and `OPEN` is open to every user of the connector. See the unreadable-ACL row below. |

Do not set `inherit_permissions=True` on a top-level group unless every user of the connector should see the group, or the group is `RESTRICTED` (so its own grants still decide). Confluence spaces are `RESTRICTED` and inherit from the App: you need the App and a grant on the space.

### On each `Record`

| Field | Meaning |
| --- | --- |
| `external_record_group_id` | The container. An unknown id creates a stub group under the App that inherits nothing; until the real group syncs, records under it open only through their own grants. |
| `parent_external_record_id`, `parent_record_type` | The parent record, if nested. A missing parent becomes a `RESTRICTED` placeholder until it syncs. |
| `inherit_permissions` (default `True`) | Take access from the parent record if there is one in the same group, otherwise from the group. A parent in another group gives no inheritance; the record inherits from its own group instead. |
| `inherit_permissions_from_group` | Inherit from the group even though the record has a parent record, when the browse parent is not the permission parent. |
| `access_rule` | As above. |
| `rewrite_permissions` | This write replaces the record's stored grants instead of adding to them. |
| permissions (the second element of the tuple you pass to `on_new_records`) | Grants written on this item itself. |

### Grants: `Permission`

`models/permission.py`. `type` is `READ`, `WRITE` or `OWNER`; for access they are the same (any grant opens the node). Retired roles (`COMMENTER`, `ORGANIZER`, `FILEORGANIZER`, `OTHERS`) read as `READ`.

| `entity_type` | Identify by | Writes a grant? |
| --- | --- | --- |
| `USER` | `email` | Yes. An email with no PipesHub user gets a person node; the grant applies once a user with that email exists. |
| `GROUP` | `external_id` (the source group id, in this connector) | Yes, if the group was written first with `on_new_user_groups`. Otherwise the grant is skipped with a warning. |
| `ROLE` | `external_id` | Yes, if the role was written first with `on_new_app_roles`. |
| `ORG` | nothing | Yes: everyone in the PipesHub org. Only for shares the source itself makes org-wide ("everyone in the company"). |
| `DOMAIN`, `ANYONE`, `ANYONE_WITH_LINK` | - | **No.** A domain may belong to a partner and a link share is not membership. Name them if you like; they open nothing. |
| `TEAM` | - | No. Dropped silently, with no warning. |

## Mapping source sharing

Decide per object type, from the source's sharing model:

| The source says | Write |
| --- | --- |
| The item takes the container's access, with no item-level changes possible | `inherit_permissions=True`, no grants on the item, `access_rule=OPEN`. |
| The item can add people on top of what it inherits, and an added person can open it without access to its container | `inherit_permissions=True`, the added people as grants, `OPEN`. |
| The item can add people, but they still need access to the container to open it | `inherit_permissions=True`, the added people as grants, `STRICT`. |
| The item can only narrow what the container allows (a restriction, as in Confluence page restrictions) | `inherit_permissions=True`, the allowed people as grants, `access_rule=RESTRICTED`. |
| The item has its own ACL that replaces inheritance ("unique permissions", broken inheritance) | `inherit_permissions=False`, only the grants on the item itself. Do not copy inherited grants onto it. |
| The item is visible only if its parents are (no direct sharing that bypasses the tree) | `access_rule=STRICT`. |
| Anyone in the connector's org can see it | an `ORG` grant on the node, or `APP_LEVEL` if it holds for everything. |
| You could not read the ACL (403 on the permissions endpoint, missing scope) | Keep what is stored, and open nothing new. **Groups:** hold the group back from `on_new_record_groups` and call `has_stored_permissions("record_group", connector_id, external_id, keep=True)` so the full-sync cleanup keeps its stored access (Jira Cloud does this for projects whose permission scheme is unreadable); or, if you must write it, set `inherit_permissions = await self.data_entities_processor.inheritance_when_unreadable(CollectionNames.RECORD_GROUPS.value, stored_id)`, keep it non-`OPEN`, and pass `None`. **Records:** set `inherit_permissions = await self.data_entities_processor.inheritance_when_unreadable(CollectionNames.RECORDS.value, stored_id)`, which keeps a stored inheritance edge and gives a new record none. Do not send an existing record through `on_new_records` during a full sync (its first write there replaces the grants with the list you pass); write content and metadata with `on_record_content_update` / `on_record_metadata_update`, which leave grants alone, and keep it with `note_kept_records` (Google Drive team does this). Never write `[]` with inheritance on as a stand-in for "unknown". Log the reason. |

Write order: users, then groups and their members, then roles, then containers, then records. A grant to a group or role that does not exist yet is dropped.

### Principals the source makes hard

- **Nested groups.** The access check follows one hop, from a user to a group they are a direct member of; it never follows group-in-group. List members transitively (the source's "transitive members" or "derived membership" option, as SharePoint, Outlook and Google Drive team do), and write every group even when it has no direct user members, so grants to it still land.
- **Hidden emails.** Some sources hide a user's email without an admin scope, so the user cannot be matched. Grant to a per-user pseudo-group instead (an `AppUserGroup` whose `source_user_group_id` is the user's source id), and when a later sync sees the email, call `migrate_group_to_user_by_external_id(group_external_id, user_email, connector_id)`, which moves the pseudo-group's grants to the user and deletes it. Confluence Cloud is the reference (`_create_pseudo_group`, `PSEUDO_USER_GROUP_PREFIX`).
- **Deactivated and suspended users.** The platform does not act on `AppUser.is_active`. A deactivated user loses access only when the connector stops naming them: leave them out of group member lists and record grants (Google Drive team skips suspended users). On a complete full sync, each group's members are replaced with exactly what was read.
- **Group members that could not be read.** Do not re-send the group with a partial or empty member list: that replaces the stored members. Keep the group as stored (`note_kept_user_groups`, or `has_stored_permissions("user_group", ...)`) and log why.

### Children of restricted items

A record with `parent_external_record_id` inherits from its parent record, so attachments, comments and sub-pages of a restricted item are restricted with it. Do not set `inherit_permissions_from_group` on them; that would give them the container's audience instead. A file under a mail, web page or similar container is linked as an `ATTACHMENT` rather than `PARENT_CHILD`, with the same inheritance.

### Scope-specific patterns

- **Team connectors** register every source user with `on_new_app_users` (that is their gate) and write real grants.
- **Storage connectors with no per-object ACL** (S3, GCS, Azure Blob, SMB, databases) declare `APP_LEVEL`, write their groups with `inherit_permissions=True`, and write no per-record grants. In **team scope only**, they call `self.data_entities_processor.ensure_team_app_edge(connector_id)` so the org's "All" team passes the gate. In personal scope they gate the creator only; calling `ensure_team_app_edge` there opens one user's data to the whole org.
- **Personal connectors** call `ensure_creator_user_app_relation()` so the creator passes the gate, and let records inherit up to the App. `ensure_connector_group_permission()` is legacy; do not use it.

## Changes after the first sync

| Change | Call |
| --- | --- |
| An item's sharing changed | `on_updated_record_permissions(record, permissions)`: replaces its grants and re-sets inheritance and `access_rule`. Or pass the record to `on_new_records` with `rewrite_permissions=True`. A plain `on_new_records` in an incremental sync adds grants and does not remove revoked ones. |
| A container's sharing changed | `on_new_record_groups` with the new list: an existing group's grants are replaced. |
| Group membership changed | `on_new_user_groups` replaces the members; `on_user_group_member_added` / `_removed` for single changes. |
| An item moved, same external id | Re-send it with `on_new_records` carrying the new parent and group ids. Stale hierarchy and inheritance edges are removed. |
| An item moved and its external id changed (path-keyed sources) | `on_records_moved([(old_external_id, new_record, permissions)], permissions_fetched=True)` when the list is complete; without `permissions_fetched` the grants are added to, not replaced. |
| An item was deleted | `delete_record_by_external_id(connector_id, external_id)`: its attachments go with it, its other children are re-pointed at what it hung under. |
| A container was deleted | `on_record_group_deleted`: the group's whole subtree goes. |

In a full sync, the first write of an existing record replaces its grants. At the end, what the run did not write is cleaned up according to `full_sync_coverage()`: with the default, it loses its links; with a date window (`window_start_ms`), unseen records created before the window, unseen windowed groups created before it, and the records in those groups keep hierarchy and inheritance but lose their direct grants (so a `RESTRICTED` one is hidden until re-read), and every other unseen node, including record groups of other types, user groups and roles, loses its links; with `complete=False` (part of the source skipped after an error), nothing is cleaned up. Use `complete=False` only for an error skip, never for a window (`01-high-level-design.md`). To keep particular stored nodes through the cleanup without rewriting them, use the processor's `note_kept_records`, `note_kept_user_groups` and `note_kept_record_group`.

## Rules

1. Unknown is not empty. An ACL you could not read is never written as "no grants, inherit".
2. Never write a grant edge yourself, never pass another org's id, never call the graph provider to change access. Go through the processor.
3. `APP_LEVEL` and `RECORD_GROUP_LEVEL` only where they are true for every record.
4. An item whose ACL replaces its container's ("unique permissions", broken inheritance) does not inherit. An item whose sharing only adds people keeps inheritance.
5. If the source's model cannot be expressed without over-sharing, stop and ask the human (`04-intake-questions.md`).

## Reference connectors

| Pattern | Where |
| --- | --- |
| Wiki: `RESTRICTED` spaces inheriting from the App; pages `STRICT`, or `RESTRICTED` when they carry a read restriction | Confluence Cloud, `connectors/sources/atlassian/confluence_cloud/connector.py` (space and page builders) |
| Storage with per-file ACLs and broken inheritance | Google Drive team, `connectors/sources/google/drive/team/drive_access.py`; Box, `connectors/sources/box/connector.py` |
| Ticketing: project groups with grants from the permission scheme; projects whose scheme is unreadable held back with their stored access kept; issues inherit | Jira Cloud, `connectors/sources/atlassian/jira_cloud/connector.py` |
| Groups before record groups before records; `ORG` grant for a public team | Linear, `connectors/sources/linear/connector.py` |
| `APP_LEVEL`, inherit up to the App | S3, `connectors/sources/s3/base_connector.py` |

## What to test

In behaviour tests, assert on the edges and fields the connector produced, against the fake source's known sharing, not against another connector or another backend:

- An item shared with one user carries that user's grant and nobody else's.
- An item with a unique ACL has `inherit_permissions=False`.
- A restricted item is `RESTRICTED` with the allowed people as grants.
- An unreadable ACL leaves stored grants alone and does not open a new node.
- A group grant names a group that the same sync wrote.

The platform's own tests of the model are in `tests/unit/connectors/sources/test_connector_hierarchy_access.py`, `tests/unit/services/graph_db/test_check_access.py` and `tests/integration/graph_permissions/`. Integration validation schemas for records, groups and edges are in `integration-tests/validation/schemas/`.
