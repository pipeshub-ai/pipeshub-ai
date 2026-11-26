"""Confluence Cloud: reading who may see what from Confluence.

Space permissions, page and blog post restrictions, the audit-log refresh of changed restrictions, and groups
with their members. The free connector keeps listing and syncing spaces and content and calls these methods;
without a licence key a company-wide connector reads none of this (the processor's licence gate).
"""

import json
import time
from datetime import datetime, timezone
from typing import (
    Any,
    Optional,
)

from app.config.constants.arangodb import (
    AccessRule,
    Connectors,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.sources.atlassian.core.confluence_access import (
    _is_folder,
    apply_page_access_to_dependents,
    unresolved_principal_permission,
    v1_next_start,
)
from app.connectors.sources.atlassian.core.quota import (
    AtlassianQuotaExhausted,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    Record,
    RecordGroup,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType

# Ids per CQL search when checking which stored items a sync filter leaves out
ID_SEARCH_CHUNK = 50

# Each item's own read restriction, for finding changed restrictions without the audit log
RESTRICTION_EXPAND_PARAMS = "restrictions.read.restrictions.user,restrictions.read.restrictions.group"

RESTRICTION_LISTING_LIMIT = 100


def read_restriction_fingerprint(item: dict[str, Any]) -> str | None:
    """Who a listed item's own read restriction names; None when it has none."""
    read = ((item.get("restrictions") or {}).get("read") or {}).get("restrictions") or {}
    users = read.get("user") or {}
    groups = read.get("group") or {}
    names = sorted(str(u.get("accountId")) for u in users.get("results") or [] if isinstance(u, dict))
    names += sorted(
        f"group:{g.get('id') or g.get('name')}" for g in groups.get("results") or [] if isinstance(g, dict)
    )
    totals = [
        listing.get("totalSize") if isinstance(listing.get("totalSize"), int) else listing.get("size")
        for listing in (users, groups)
    ]
    if not names and not any(totals):
        return None
    listed = len(users.get("results") or []) + len(groups.get("results") or [])
    if listed < sum(t for t in totals if isinstance(t, int)):
        # Names past the listing's first page can't be compared: always check such an item again.
        return f"unlisted@{time.time()}"
    return f"{totals[0]}/{totals[1]}:" + ",".join(names)


# Constant for pseudo-user group prefix
PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"

# Confluence v2 space permission access-class principals (ROLES-mode / default ACLs), as
# normalised by _access_class_id: Cloud spells them "access-class"/"ALL_LICENSED_USERS" or
# "ACCESS_CLASS"/"all-licensed-users".
ACCESS_CLASS_LICENSED_USERS = "ALL_LICENSED_USERS"

ACCESS_CLASS_PRODUCT_ADMINS = "ALL_PRODUCT_ADMINS"

ACCESS_CLASS_AUTHENTICATED_USERS = "AUTHENTICATED_USERS"

ACCESS_CLASS_ANONYMOUS_USERS = "ANONYMOUS_USERS"

# Synced groups an access class stands for: exact names, then name prefixes. Cloud sites
# name them per site, e.g. "confluence-users-acme" and "confluence-admins-acme".
LICENSED_USER_GROUP_NAMES = ("confluence-users",)

LICENSED_USER_GROUP_PREFIXES = ("confluence-users-",)

PRODUCT_ADMIN_GROUP_NAMES = ("org-admins", "confluence-administrators", "site-admins", "system-administrators")

PRODUCT_ADMIN_GROUP_PREFIXES = ("confluence-admins-",)


class ConfluenceCloudPermissionsMixin:
    """The permission-reading half of ``ConfluenceConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Confluence. The processor then keeps every
        stored grant, group and role whatever is handed to it (``skips_permission_writes``), so the gated answers
        below are empty, not None: None is this connector's "could not read", which leaves the project, space or
        page unsaved or skipped, as unreadable, on every run."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _sync_user_groups(self) -> None:
        """
        Sync user groups and their memberships from Confluence.

        Steps:
        1. Fetch all groups with pagination
        2. For each group, fetch all members with pagination
        3. Create group and membership records
        """
        try:
            self.logger.info("Starting user group synchronization...")

            # Groups may change between sync runs on a long-lived connector instance.
            self._group_name_to_external_id = None
            self._group_read_complete = False
            self._pending_inactive_ids = set()
            self._pending_active_emails = set()
            if await self._copies_no_source_access():
                return
            every_member_list_read = True
            # (group, member emails, member accountIds) of each group read in full.
            read_groups: list[tuple[dict, list[str], list[str]]] = []
            # Every group id the listing returned, and whether it was read to the end.
            listed_group_ids: set[str] = set()
            group_list_complete = True

            # Pagination variables for groups
            batch_size = 50
            start = 0
            total_groups_synced = 0
            total_memberships_synced = 0

            # Paginate through all groups
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_groups(
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch groups: {response.status if response else 'No response'}")
                    self._note_problem("Couldn't read the Confluence group list (HTTP %s)" % (response.status if response else "no response"))
                    every_member_list_read = False
                    group_list_complete = False
                    break

                response_data = response.json()
                groups_data = response_data.get("results", [])

                if not groups_data:
                    if (response_data.get("_links") or {}).get("next"):
                        # Only a missing next link ends the list; removal must not see a gap as deletions.
                        group_list_complete = False
                        self._note_problem("The Confluence group list stopped early")
                    break

                for group_data in groups_data:
                    try:
                        group_id = group_data.get("id")
                        group_name = group_data.get("name")

                        if group_id:
                            listed_group_ids.add(str(group_id))
                        if not group_id or not group_name:
                            continue

                        self.logger.debug(f"  Reading group: {group_name} ({group_id})")

                        members = await self._fetch_group_members(group_id, group_name)
                        if members is None:
                            # Saving the group now would replace its members with an empty list.
                            self.logger.warning(
                                f"Keeping the stored members of group {group_name}: its member list could not be read"
                            )
                            self._note_problem(f"Couldn't read the members of group {group_name}; its stored members were kept")
                            every_member_list_read = False
                            continue
                        read_groups.append((group_data, *members))

                    except AtlassianQuotaExhausted:
                        raise
                    except Exception as group_error:
                        self.logger.error(f"❌ Failed to read group {group_data.get('name')}: {group_error}")
                        self._note_problem(f"Couldn't read group {group_data.get('name')}: {str(group_error)[:200]}")
                        every_member_list_read = False
                        continue

                try:
                    next_start = v1_next_start(response_data, start, len(groups_data), batch_size, use_link_offset=True)
                except ValueError as e:
                    # Groups are saved one by one, so the ones not reached keep what is stored.
                    self.logger.error(f"❌ Stopping the group list: {e}")
                    self._note_problem("Couldn't follow the Confluence group list to its next page")
                    every_member_list_read = False
                    group_list_complete = False
                    break
                if next_start is None:
                    break
                start = next_start

            if every_member_list_read:
                self._inactive_account_ids = self._pending_inactive_ids
                self._active_member_emails = self._pending_active_emails
                self._group_read_complete = True
                self._account_statuses_known = True
            # Members resolve only to users linked to the connector, so a reactivated
            # account has to be linked again before its groups are saved.
            await self._restore_reactivated_users()

            for group_data, member_emails, member_account_ids in read_groups:
                group_name = group_data.get("name")
                try:
                    user_group = self._transform_to_user_group(group_data)
                    if not user_group:
                        continue

                    try:
                        app_users = await self._resolve_group_app_users(
                            member_emails, member_account_ids
                        )
                    except Exception as lookup_error:
                        # Saving the group now would replace its members with an empty list.
                        self.logger.warning(
                            f"Keeping the stored members of group {group_name}: "
                            f"its members could not be looked up ({lookup_error})"
                        )
                        self._note_problem(f"Couldn't look up the members of group {group_name}; its stored members were kept")
                        continue

                    await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                    total_groups_synced += 1
                    total_memberships_synced += len(app_users)
                    self.logger.debug(f"Group {group_name}: {len(app_users)} members")

                except Exception as group_error:
                    self.logger.error(f"❌ Failed to process group {group_name}: {group_error}")
                    self._note_problem(f"Couldn't save group {group_name}: {str(group_error)[:200]}")
                    continue

            if group_list_complete:
                await self._remove_groups_gone_from_source(listed_group_ids)

            self.logger.info(f"✅ Group sync complete. Groups: {total_groups_synced}, Memberships: {total_memberships_synced}")

        except Exception as e:
            self.logger.error(f"❌ Group sync failed: {e}", exc_info=True)
            raise

    async def _sync_permission_changes_from_audit_log(self, spaces: list[RecordGroup] | None = None) -> None:
        """
        Sync permission changes for pages/blogs using Confluence Audit Log API.

        This method tracks permission changes that don't update the content's
        lastModified timestamp, ensuring we capture:
        - Content restriction added (user/group gets access)
        - Content restriction removed (user/group loses access)

        Flow:
        1. Get last audit sync time
        2. If first run (no checkpoint): Initialize with current time and skip (permissions already synced)
        3. If subsequent run: Fetch audit logs since last sync
        4. Extract unique content titles from audit records
        5. Search content by titles and check if exists in DB
        6. For each existing content: Fetch current permissions and update
        7. Update audit log sync point with current timestamp

        Note: First run initializes checkpoint but skips permission sync because
        the initial content sync (_sync_content) already synced all permissions.
        """
        if await self._copies_no_source_access():
            return
        try:
            self.logger.info("🔍 Starting permission sync from audit log...")

            # Sync point key for audit log
            audit_sync_key = self._audit_sync_key()

            # Get last audit sync timestamp
            last_audit_sync = await self.audit_log_sync_point.read_sync_point(audit_sync_key)
            last_sync_time_ms = last_audit_sync.get("last_sync_time_ms") if last_audit_sync else None

            # Current time as checkpoint
            current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            # First run: Initialize checkpoint and skip (permissions already synced during content sync)
            if last_sync_time_ms is None:
                self.logger.info(
                    "🆕 First audit log sync - initializing checkpoint to current time and skipping. "
                    "Permissions already synced during content sync."
                )

                # Save initial checkpoint
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            # Events can reach the log a little late; reading them twice is harmless.
            window_start_ms = last_sync_time_ms - self._AUDIT_OVERLAP_MS
            self.logger.info(f"🔄 Fetching audit logs from {window_start_ms} to {current_time_ms}")

            # Fetch audit logs and extract content titles that had permission changes
            content_titles = await self._fetch_permission_audit_logs(window_start_ms, current_time_ms)

            if content_titles is None:
                if spaces is not None:
                    # Find restriction changes from the listings instead, so they still apply, also when
                    # the audit log keeps failing. Only a refusal moves the window: other failures read it again.
                    if await self._recheck_restrictions(spaces) and self._audit_log_refused:
                        await self.audit_log_sync_point.update_sync_point(
                            audit_sync_key, {"last_sync_time_ms": current_time_ms}
                        )
                    return
                self.logger.warning(
                    "Keeping the audit log checkpoint: the audit log could not be read in full, "
                    "so the next sync reads this window again"
                )
                return

            if not content_titles:
                self.logger.info("✅ No permission changes found in audit log")
                # Update sync point even if no changes
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            self.logger.info(f"📋 Found {len(content_titles)} content items with permission changes")

            # Search for content by titles and sync their permissions (only if exists in DB)
            try:
                await self._sync_content_permissions_by_titles(content_titles)
            except AtlassianQuotaExhausted:
                raise
            except Exception as e:
                self.logger.warning(f"Keeping the audit log window: some restriction changes could not be applied: {e}")
                self._note_problem(
                    "Couldn't apply some restriction changes from the audit log; the next sync tries again"
                )
                return

            # Update audit log sync point with current time
            await self.audit_log_sync_point.update_sync_point(
                audit_sync_key,
                {"last_sync_time_ms": current_time_ms}
            )

            self.logger.info("✅ Permission sync from audit log completed")

        except Exception as e:
            self.logger.error(f"❌ Permission sync from audit log failed: {e}", exc_info=True)
            raise

    async def _fetch_permission_audit_logs(
        self,
        start_date_ms: int,
        end_date_ms: int
    ) -> Optional[list[str]]:
        """
        Fetch audit logs and extract content titles that had permission changes.

        Filters for:
        - category = "Permissions"
        - scope = content (pages/blogs, not global or space-level)

        Args:
            start_date_ms: Start timestamp in milliseconds
            end_date_ms: End timestamp in milliseconds

        Returns:
            List of unique content titles (pages/blogs) that had permission changes, or None
            when a page of the audit log could not be read.
        """
        content_titles_set: set[str] = set()
        batch_size = 100
        start = 0
        self._audit_log_refused = None

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_audit_logs(
                start_date=start_date_ms,
                end_date=end_date_ms,
                start=start,
                limit=batch_size
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"⚠️ Failed to fetch audit logs: {response.status if response else 'No response'}")
                # Any 4xx but 429 repeats every run (no permission, a plan or site without the endpoint).
                refused = response is not None and 400 <= response.status < 500 and response.status != 429
                if refused:
                    self._audit_log_refused = response.status
                    problem = (
                        f"Couldn't read the Confluence audit log (HTTP {response.status}): it needs a Confluence "
                        "administrator account (and the read:audit-log:confluence scope for OAuth). Instead, each "
                        "sync re-reads every page's read restriction (one request per 100 pages in a space)"
                    )
                else:
                    problem = (
                        "Couldn't read the Confluence audit log (HTTP %s); restriction changes are read again "
                        "next sync" % (response.status if response else "no response")
                    )
                self._note_problem(problem, unread=False, persistent=refused)
                return None

            response_data = response.json()
            audit_records = response_data.get("results", [])

            if not audit_records:
                break

            # Process each audit record
            for record in audit_records:
                content_title = self._extract_content_title_from_audit_record(record)
                if content_title:
                    content_titles_set.add(content_title)

            try:
                next_start = v1_next_start(response_data, start, len(audit_records), batch_size, use_link_offset=True)
            except ValueError as e:
                self.logger.warning(f"⚠️ Failed to follow the audit log: {e}")
                self._note_problem(
                    "Couldn't follow the Confluence audit log to its next page; restriction changes are read again next sync",
                    unread=False,
                )
                return None
            if next_start is None:
                break
            start = next_start

        return list(content_titles_set)

    async def _recheck_restrictions(self, spaces: list[RecordGroup]) -> bool:
        """Sync again the stored pages, blog posts and folders whose own read restriction changed.

        For when the audit log is refused. Placeholder ancestors and folders only get their
        access read again; in-scope pages and blog posts are synced again. One listing per space and type carries each
        item's read restriction. An item is synced again when that restriction differs
        from the one the previous check saw, or when it has none and is stored as
        restricted; the first check therefore syncs every restricted item once. Returns
        False when a listing, the stored records or a sync could not be read in full.
        """
        complete = True
        for space in spaces:
            for record_type in (RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST, RecordType.FILE):
                listed = await self._list_read_restrictions(space.short_name, record_type)
                if listed is None:
                    complete = False
                    continue
                try:
                    stored = await self._stored_content(
                        space.external_group_id, record_type, with_placeholders=True
                    )
                    if record_type == RecordType.FILE:
                        stored = [r for r in stored if _is_folder(r)]
                except Exception as e:
                    self.logger.warning(f"Could not read the stored records of space {space.short_name}: {e}")
                    complete = False
                    continue
                key = generate_record_sync_point_key(
                    RecordType.WEBPAGE.value, "restrictions", f"{record_type.value}/{space.short_name}"
                )
                seen = await self.audit_log_sync_point.read_sync_point(key)
                before: dict[str, str] = json.loads(seen.get("restricted") or "{}") if seen else {}
                stored_rule = {r.external_record_id: r.access_rule for r in stored}
                changed = sorted(
                    item_id for item_id, fingerprint in listed.items()
                    if item_id in stored_rule and (
                        fingerprint != before.get(item_id) if fingerprint
                        else stored_rule[item_id] == AccessRule.RESTRICTED
                    )
                )
                by_id = {r.external_record_id: r for r in stored}
                access_only = [i for i in changed if record_type == RecordType.FILE or by_id[i].is_placeholder]
                failed = await self._refresh_access_by_ids(space.short_name, record_type, access_only, by_id)
                changed_content = [i for i in changed if i not in set(access_only)]
                for start in range(0, len(changed_content), ID_SEARCH_CHUNK):
                    chunk = changed_content[start:start + ID_SEARCH_CHUNK]
                    result = await self._sync_content(
                        space.short_name, record_type, defer_checkpoint=True, within_ids=chunk,
                    )
                    # One the search did not return (its index behind) is checked again next sync.
                    failed.update(chunk if not result.complete else set(chunk) - set(result.seen))
                if changed:
                    self.logger.info(
                        f"Synced {len(changed)} {record_type.value} items in space {space.short_name} again: "
                        "their read restriction changed"
                    )
                complete = complete and not failed
                # A failed item keeps the restriction last seen, so the next check retries it.
                restricted = {
                    item_id: (before.get(item_id) if item_id in failed else fingerprint)
                    for item_id, fingerprint in listed.items()
                }
                await self.audit_log_sync_point.update_sync_point(
                    key, {"restricted": json.dumps({k: v for k, v in restricted.items() if v}, sort_keys=True)}
                )
        if not complete:
            self._note_problem("Couldn't re-check every page's restrictions; the next sync tries again")
        return complete

    async def _with_current_access(
        self, item_data: dict[str, Any], record_type: RecordType, existing_record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """A stored page, blog post or folder with its access read again.

        None when the item could not be read into a record; raises when its restrictions could not be read.
        """
        item_id = str(item_data.get("id"))
        # Transform to WebpageRecord (a folder is a FileRecord)
        record = (
            self._transform_to_folder_file_record(item_data, existing_record)
            if record_type == RecordType.FILE
            else self._transform_to_webpage_record(item_data, record_type, existing_record)
        )
        if not record:
            return None
        # A stub of an item the filters leave out stays a stub: only its access changes.
        record.is_placeholder = bool(getattr(existing_record, "is_placeholder", False))
        permissions = await self._fetch_page_permissions(item_id)
        if permissions is None:
            raise ValueError(f"Restrictions for {item_id} could not be read; keeping what is stored")
        # A READ restriction keeps inheriting and is recorded as RESTRICTED instead; EDIT-only stays STRICT.
        record.access_rule = (
            AccessRule.RESTRICTED if any(p.type == PermissionType.READ for p in permissions) else AccessRule.STRICT
        )
        record.rewrite_permissions = True
        return record, permissions

    async def _save_with_access(self, records_with_permissions: list[tuple[Record, list[Permission]]]) -> None:
        """Save records whose access was read again, and pass it to their stored files and comments."""
        await self.data_entities_processor.on_new_records(records_with_permissions)
        self.logger.info(f"Updated permissions for {len(records_with_permissions)} content items")
        await self._drop_legacy_comment_records(self._saved_as_blocks(r for r, _ in records_with_permissions))
        # Their stored files and comments would otherwise keep the page's old access.
        for record, permissions in records_with_permissions:
            await apply_page_access_to_dependents(
                self.data_entities_processor,
                self.connector_id,
                record.external_record_id,
                permissions,
                inherits_space=record.inherit_permissions,
            )

    async def _refresh_access_by_ids(
        self, space_key: str, record_type: RecordType, ids: list[str], stored: dict[str, Record]
    ) -> set[str]:
        """Read again the access of stored folders and placeholder ancestors; returns the ids that failed.

        Their content is not synced: a folder has none, and a placeholder stays one.
        """
        datasource = await self._get_fresh_datasource()
        failed = set(ids)
        for start in range(0, len(ids), ID_SEARCH_CHUNK):
            chunk = ids[start:start + ID_SEARCH_CHUNK]
            if record_type == RecordType.FILE:
                listing, by_ids = datasource.get_folders_v1, {"folder_ids": chunk, "folder_ids_operator": "in"}
            elif record_type == RecordType.CONFLUENCE_PAGE:
                listing, by_ids = datasource.get_pages_v1, {"within_ids": chunk}
            else:
                listing, by_ids = datasource.get_blogposts_v1, {"within_ids": chunk}
            token: str | None = None
            followed: set[str] = set()
            while True:
                offset, cursor = self._split_pagination_token(token)
                try:
                    response = await listing(
                        space_key=space_key, expand="version,space,history.lastUpdated,ancestors",
                        start=offset, cursor=cursor, limit=ID_SEARCH_CHUNK, **by_ids,
                    )
                    data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
                except AtlassianQuotaExhausted:
                    raise
                except Exception as e:
                    self.logger.warning(f"Could not read items of space {space_key} to update their access: {e}")
                    break
                results = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results, list):
                    break
                batch: list[tuple[Record, list[Permission]]] = []
                for item in results:
                    item_id = str(item.get("id")) if isinstance(item, dict) else None
                    if item_id in stored:
                        try:
                            refreshed = await self._with_current_access(item, record_type, stored[item_id])
                        except AtlassianQuotaExhausted:
                            raise
                        except Exception as e:
                            self.logger.warning(f"Could not update the access of {item_id}: {e}")
                            continue
                        if refreshed is not None:
                            batch.append(refreshed)
                            failed.discard(item_id)
                if batch:
                    try:
                        await self._save_with_access(batch)
                    except Exception as e:
                        self.logger.warning(f"Could not save the access of items in space {space_key}: {e}")
                        failed.update(str(r.external_record_id) for r, _ in batch)
                next_url = (data.get("_links") or {}).get("next")
                token = self._extract_cursor_from_next_link(next_url) if next_url else None
                if not token or not results or token in followed:
                    break
                followed.add(token)
        return failed

    async def _list_read_restrictions(self, space_key: str, record_type: RecordType) -> dict[str, str | None] | None:
        """Read restriction fingerprint by id of every page, blog post or folder (FILE) in a space; None unless read to the end."""
        datasource = await self._get_fresh_datasource()
        listing = {
            RecordType.CONFLUENCE_PAGE: datasource.get_pages_v1,
            RecordType.CONFLUENCE_BLOGPOST: datasource.get_blogposts_v1,
            RecordType.FILE: datasource.get_folders_v1,
        }[record_type]
        found: dict[str, str | None] = {}
        token: str | None = None
        followed: set[str] = set()
        while True:
            offset, cursor = self._split_pagination_token(token)
            try:
                response = await listing(
                    space_key=space_key, expand=RESTRICTION_EXPAND_PARAMS, start=offset, cursor=cursor,
                    limit=RESTRICTION_LISTING_LIMIT,
                )
                data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
            except AtlassianQuotaExhausted:
                raise
            except Exception as e:
                self.logger.warning(f"Could not list the restrictions in space {space_key}: {e}")
                return None
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                self.logger.warning(f"Could not list the restrictions in space {space_key}")
                return None
            for item in results:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if "read" not in (item.get("restrictions") or {}):
                    self.logger.warning(f"The listing of space {space_key} did not carry read restrictions")
                    return None
                found[str(item["id"])] = read_restriction_fingerprint(item)
            next_url = (data.get("_links") or {}).get("next")
            if not next_url:
                return found
            token = self._extract_cursor_from_next_link(next_url)
            if not results or not token or token in followed:
                self.logger.warning(f"Can't follow the restriction listing of space {space_key}")
                return None
            followed.add(token)

    def _extract_content_title_from_audit_record(self, record: dict[str, Any]) -> str | None:
        """
        Extract content title from an audit record if it's a content permission change.

        Filters for:
        - category = "Permissions"
        - Has a Page, Blog or Folder in associatedObjects (content-level permission)
        - Has a Space in associatedObjects (confirms it's content, not global)

        Args:
            record: Raw audit log record

        Returns:
            Content title (page/blog) or None if not a content permission change
        """
        # Must be a permission-related event
        if record.get("category") != "Permissions":
            return None

        associated_objects = record.get("associatedObjects", [])

        # Check for content-level permission (must have both content AND space)
        has_space = any(obj.get("objectType") == "Space" for obj in associated_objects)
        content_obj = next(
            # shortcut: "Folder" is assumed from the page and blog names; no real folder entry was seen yet.
            (obj for obj in associated_objects if obj.get("objectType") in ["Page", "Blog", "Folder"]),
            None
        )

        # Content restriction must have both page/blog AND space
        if not has_space or not content_obj:
            return None

        return content_obj.get("name")

    async def _sync_content_permissions_by_titles(self, titles: list[str]) -> None:
        """
        Search for content by titles and sync their current permissions.

        IMPORTANT: This method ONLY updates permissions for records that already exist in the database.
        It will NOT create new records, ensuring sync filters are respected.

        For each found content item:
        1. Check if record exists in DB (by external_record_id)
        2. If exists: Fetch current permissions and update
        3. If not exists: Skip (record was filtered out during initial sync)

        Args:
            titles: List of content titles to search for
        """
        if not titles:
            return

        # Batch titles to avoid CQL query size limits (process 50 at a time)
        batch_size = 50
        total_synced = 0
        total_skipped = 0
        total_permissions = 0
        has_failures = False

        for i in range(0, len(titles), batch_size):
            batch_titles = titles[i:i + batch_size]

            try:
                # One shared title can fill a page, so the search is read to its last page.
                pagination_token: Optional[str] = None
                followed: set[str] = set()
                while True:
                    datasource = await self._get_fresh_datasource()
                    start_offset, cursor_token = self._split_pagination_token(pagination_token)
                    response = await datasource.search_content_by_titles(
                        titles=batch_titles,
                        expand="version,space,history.lastUpdated,ancestors",
                        start=start_offset,
                        cursor=cursor_token,
                    )

                    if not response or response.status != HttpStatusCode.SUCCESS.value:
                        self.logger.warning(f"⚠️ Failed to search content by titles: {response.status if response else 'No response'}")
                        has_failures = True
                        break

                    response_data = response.json()
                    content_items = response_data.get("results", [])

                    if not content_items:
                        self.logger.debug(f"No content found for titles batch {i // batch_size + 1}")
                        break

                    # Process each content item
                    records_with_permissions = []
                    for item_data in content_items:
                        try:
                            item_id = item_data.get("id")
                            item_title = item_data.get("title")
                            item_type = item_data.get("type", "").lower()

                            if not item_id or not item_title:
                                continue

                            # Determine record type
                            if item_type == "page":
                                record_type = RecordType.CONFLUENCE_PAGE
                            elif item_type == "blogpost":
                                record_type = RecordType.CONFLUENCE_BLOGPOST
                            elif item_type == "folder":
                                record_type = RecordType.FILE
                            else:
                                self.logger.debug(f"Skipping unknown content type: {item_type}")
                                continue

                            # Check if record exists in database (respects sync filters)
                            existing_record = await self.data_entities_processor.get_record_by_external_id(
                                connector_id=self.connector_id,
                                external_record_id=item_id
                            )

                            if not existing_record:
                                # Record doesn't exist - it was filtered out during initial sync
                                self.logger.debug(
                                    f"Skipping {item_type} '{item_title}' ({item_id}) - "
                                    f"not in database (filtered out during sync)"
                                )
                                total_skipped += 1
                                continue

                            self.logger.debug(f"Updating permissions for {item_type}: {item_title} ({item_id})")
                            refreshed = await self._with_current_access(item_data, record_type, existing_record)
                            if refreshed is None:
                                continue
                            total_permissions += len(refreshed[1])
                            records_with_permissions.append(refreshed)
                            total_synced += 1

                        except Exception as item_error:
                            self.logger.error(f"❌ Failed to sync permissions for {item_data.get('title')}: {item_error}")
                            has_failures = True
                            continue

                    # Update batch in database
                    if records_with_permissions:
                        await self._save_with_access(records_with_permissions)

                    next_url = response_data.get("_links", {}).get("next")
                    if not next_url:
                        break
                    pagination_token = self._extract_cursor_from_next_link(next_url)
                    if not pagination_token or pagination_token in followed:
                        self.logger.warning(f"⚠️ Title search has a next page that could not be followed: {next_url}")
                        has_failures = True
                        break
                    followed.add(pagination_token)

            except Exception as batch_error:
                self.logger.error(f"❌ Failed to process titles batch: {batch_error}")
                has_failures = True
                continue

        if has_failures:
            raise ValueError("Failed to sync permissions for some content items")

        if total_skipped > 0:
            self.logger.info(f"🔍 Skipped {total_skipped} items not in database (filtered during sync)")

        self.logger.info(f"✅ Permission sync complete. Items updated: {total_synced}, Permissions: {total_permissions}")

    async def _fetch_space_permissions(self, space_id: str, space_name: str) -> Optional[list[Permission]]:
        """
        Fetch all permissions for a space with cursor-based pagination.

        Args:
            space_id: The space ID
            space_name: The space name (for logging)

        Returns:
            List of Permission objects, or None when they could not be read in full.
        """
        if await self._copies_no_source_access():
            return []
        try:
            permissions = []
            batch_size = 100
            cursor = None
            followed: set[str] = set()

            # Paginate through space permissions
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_space_permissions_assignments(
                    id=space_id,
                    limit=batch_size,
                    cursor=cursor
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to fetch permissions for space {space_name}: {response.status if response else 'No response'}")
                    return None

                response_data = response.json()
                permissions_data = response_data.get("results", [])
                next_url = (response_data.get("_links") or {}).get("next")

                if not permissions_data:
                    if next_url:
                        # Saving a list read only in part would take the rest of the grants away.
                        self.logger.warning(f"⚠️ Permissions of space {space_name} stopped early")
                        return None
                    break

                # Transform permissions and add to list
                for perm_data in permissions_data:
                    if self._access_class_id(perm_data):
                        permissions.extend(await self._access_class_permissions(perm_data, space_name))
                        continue
                    permission = await self._transform_space_permission(perm_data)
                    if permission:
                        permissions.append(permission)

                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor or cursor in followed:
                    self.logger.warning(f"⚠️ Couldn't follow the permissions of space {space_name}: {next_url}")
                    return None
                followed.add(cursor)

            deduped = self._dedupe_space_permissions(permissions)
            self.logger.debug(
                "Space %s: %s permissions after dedupe (from %s API rows)",
                space_name,
                len(deduped),
                len(permissions),
            )
            return deduped

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for space {space_name}: {e}")
            return None

    async def _read_all_restrictions(self, page_id: str) -> Optional[list[dict[str, Any]]]:
        """Every user and group of each of a content item's restrictions; None when it can't be read.

        Cloud returns at most ``limit`` users and ``limit`` groups per operation in one
        response, so a longer list is read page by page and merged per operation.
        """
        merged: dict[str, dict[str, Any]] = {}
        seen: set[tuple[str, str, str]] = set()
        # How many users / groups each operation has in all, when Confluence says (totalSize).
        expected: dict[tuple[str, str], int] = {}
        start = 0
        for _ in range(self._RESTRICTION_MAX_PAGES):
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_permissions_v1(
                page_id=page_id, start=start, limit=self._RESTRICTION_PAGE
            )
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"⚠️ Failed to fetch permissions for page {page_id}: {response.status if response else 'No response'}")
                return None
            more = False
            new = False
            step = self._RESTRICTION_PAGE
            for restriction in response.json().get("results", []) or []:
                operation = restriction.get("operation")
                target = merged.setdefault(operation, {
                    "operation": operation,
                    "restrictions": {"user": {"results": []}, "group": {"results": []}},
                })
                for kind in ("user", "group"):
                    listing = (restriction.get("restrictions") or {}).get(kind) or {}
                    entries = listing.get("results") or []
                    if isinstance(listing.get("totalSize"), int):
                        expected[(str(operation), kind)] = listing["totalSize"]
                    for entry in entries:
                        key = (str(operation), kind, str(entry.get("accountId") or entry.get("id") or entry.get("name")))
                        if key not in seen:
                            seen.add(key)
                            new = True
                            target["restrictions"][kind]["results"].append(entry)
                    # The page size Confluence used, which a system limit may set below ours.
                    page_size = listing.get("limit") if isinstance(listing.get("limit"), int) else self._RESTRICTION_PAGE
                    more = more or (page_size > 0 and len(entries) >= page_size)
                    if 0 < page_size < step:
                        step = page_size
            # The next page starts after what this one could hold, at Confluence's page size.
            start += step
            # A full page may be followed by more; a page that adds nothing new ends it.
            if not more or not new:
                read = {
                    (str(op), kind): len(data["restrictions"][kind]["results"])
                    for op, data in merged.items() for kind in ("user", "group")
                }
                if any(read.get(key, 0) < total for key, total in expected.items()):
                    self.logger.warning(f"⚠️ Restrictions of {page_id} could not be read in full")
                    return None
                return list(merged.values())
        self.logger.warning(f"⚠️ Restrictions of {page_id} have more than {self._RESTRICTION_MAX_PAGES} pages")
        return None

    async def _fetch_page_permissions(self, page_id: str) -> Optional[list[Permission]]:
        """
        Fetch permissions for a Confluence page using v1 API.

        Args:
            page_id: The page ID

        Returns:
            List of Permission objects, or None when the restrictions could not be
            read (callers must not treat that as unrestricted).
        """
        if await self._copies_no_source_access():
            return []
        permissions = []

        try:
            self.logger.debug(f"Fetching permissions for page: {page_id}")

            restrictions = await self._read_all_restrictions(page_id)
            if restrictions is None:
                return None

            # Process each restriction (read and update operations)
            for restriction_data in restrictions:
                operation_permissions = await self._transform_page_restriction_to_permissions(restriction_data)
                permissions.extend(operation_permissions)

            self.logger.debug(f"Found {len(permissions)} permissions for page {page_id}")
            return permissions

        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for page {page_id}: {e}")
            return None

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        create_pseudo_group_if_missing: bool = False
    ) -> Permission | None:
        """
        Create Permission object from principal data (user or group).

        This is a common function used by both space and page permission processing.

        Args:
            principal_type: "user" or "group"
            principal_id: accountId for users, groupId for groups
            permission_type: Mapped PermissionType enum
            create_pseudo_group_if_missing: If True and user not found, create a
                pseudo-group to preserve the permission. Used for record-level

        Returns:
            Permission object or None if principal not found in DB
        """
        try:
            if principal_type == "user":
                entity_type = EntityType.USER
                # Lookup user by source_user_id (accountId)
                user = await self.data_entities_processor.get_user_by_source_id(
                    source_user_id=principal_id,
                    connector_id=self.connector_id,
                )
                if user:
                    return Permission(
                        email=user.email,
                        type=permission_type,
                        entity_type=entity_type
                    )

                # User not found - check if pseudo-group exists or should be created
                if create_pseudo_group_if_missing:
                    # Check for existing pseudo-group
                    pseudo_group = await self.data_entities_processor.get_user_group_by_external_id(
                        connector_id=self.connector_id,
                        external_id=principal_id,
                    )

                    if pseudo_group:
                        # Reused, not rewritten: without this the full sync unlinks it with its grants.
                        self.data_entities_processor.note_kept_user_groups(self.connector_id, [pseudo_group])
                    else:
                        # Create pseudo-group on-the-fly
                        pseudo_group = await self._create_pseudo_group(principal_id)

                    if pseudo_group:
                        self.logger.debug(
                            f"Using pseudo-group for user {principal_id} (no email available)"
                        )
                        return Permission(
                            external_id=pseudo_group.source_user_group_id,
                            type=permission_type,
                            entity_type=EntityType.GROUP
                        )

                self.logger.debug(f"  ⚠️ User {principal_id} not found in DB, skipping permission")
                return None

            elif principal_type == "group":
                entity_type = EntityType.GROUP
                # Lookup group by source_user_group_id
                group = await self.data_entities_processor.get_user_group_by_external_id(
                    connector_id=self.connector_id,
                    external_id=principal_id,
                )
                if not group:
                    self.logger.debug(f"  ⚠️ Group {principal_id} not found in DB, skipping permission")
                    return None

                return Permission(
                    external_id=group.source_user_group_id,
                    type=permission_type,
                    entity_type=entity_type
                )

            return None

        except Exception as e:
            # A failed lookup is not "not found": callers keep what is stored instead.
            self.logger.error(f"❌ Failed to create permission from principal: {e}")
            raise

    async def _create_pseudo_group(self, account_id: str) -> AppUserGroup | None:
        """
        Create a pseudo-group for a user without email.

        This preserves permissions for users who don't have email addresses yet.
        The pseudo-group uses the user's accountId as source_user_group_id.

        Args:
            account_id: Confluence user accountId

        Returns:
            Created AppUserGroup or None if creation fails
        """
        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX} {account_id}",
                org_id=self.data_entities_processor.org_id,
            )

            # Save to database (empty members list)
            await self.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info(f"Created pseudo-group for user without email: {account_id}")

            return pseudo_group

        except Exception as e:
            self.logger.error(f"Failed to create pseudo-group for {account_id}: {e}")
            return None

    async def _ensure_group_name_cache(self) -> dict[str, str]:
        """Build name -> external group id map from synced Confluence groups."""
        if self._group_name_to_external_id is not None:
            return self._group_name_to_external_id

        name_map: dict[str, str] = {}
        # Raised, not read as no groups: every access-class grant would map to nothing for the whole sync.
        groups = await self.data_entities_processor.get_all_user_groups(
            connector_id=self.connector_id, raise_on_error=True,
        )
        for group in groups:
            if group.name and group.source_user_group_id:
                name_map[group.name.lower()] = group.source_user_group_id

        self._group_name_to_external_id = name_map
        self.logger.debug(
            "Loaded %s Confluence group name(s) for access-class resolution",
            len(name_map),
        )
        return name_map

    @staticmethod
    def _access_class_id(perm_data: dict[str, Any]) -> str | None:
        """The access class a space permission row grants, normalised (e.g. ALL_LICENSED_USERS); None for a user or group."""
        principal = perm_data.get("principal") or {}
        kind = str(principal.get("type") or "").lower().replace("_", "-")
        if kind != "access-class" or not principal.get("id"):
            return None
        return str(principal["id"]).upper().replace("-", "_")

    async def _groups_matching(self, names: tuple[str, ...], prefixes: tuple[str, ...]) -> list[str]:
        """External ids of every synced group named one of ``names`` or starting with one of ``prefixes``."""
        name_map = await self._ensure_group_name_cache()
        wanted = {n.lower() for n in names}
        return sorted({
            external_id for name, external_id in name_map.items()
            if name in wanted or any(name.startswith(prefix) for prefix in prefixes)
        })

    async def _access_class_permissions(
        self, perm_data: dict[str, Any], space_name: str = ""
    ) -> list[Permission]:
        """Map an access-class space grant to the synced groups it stands for.

        Never to the whole PipesHub org, except anonymous access (a public space), which
        is open to everyone. A class with no synced group grants nothing and is reported.
        """
        access_class = self._access_class_id(perm_data)
        operation = perm_data.get("operation") or {}
        operation_key, target_type = operation.get("key"), operation.get("targetType")
        if target_type != "space":
            return []
        if access_class == ACCESS_CLASS_ANONYMOUS_USERS and operation_key == "read":
            return [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        if access_class in (ACCESS_CLASS_LICENSED_USERS, ACCESS_CLASS_AUTHENTICATED_USERS) and operation_key == "read":
            names, prefixes, permission_type = LICENSED_USER_GROUP_NAMES, LICENSED_USER_GROUP_PREFIXES, PermissionType.READ
        elif access_class == ACCESS_CLASS_PRODUCT_ADMINS and operation_key == "administer":
            names, prefixes, permission_type = PRODUCT_ADMIN_GROUP_NAMES, PRODUCT_ADMIN_GROUP_PREFIXES, PermissionType.OWNER
        else:
            self.logger.debug("Skipping access-class %s for operation %s/%s", access_class, operation_key, target_type)
            return []
        group_ids = await self._groups_matching(names, prefixes)
        if not group_ids:
            self.logger.warning(f"Space {space_name}: no synced group stands for access class {access_class}; it grants nothing")
            self._note_problem(
                f"Space {space_name}: its {access_class.lower().replace('_', ' ')} grant matches no synced Confluence "
                "group, so it gives no one access in PipesHub",
                unread=False,
                persistent=True,
            )
            return []
        return [Permission(entity_type=EntityType.GROUP, external_id=g, type=permission_type) for g in group_ids]

    @staticmethod
    def _dedupe_space_permissions(permissions: list[Permission]) -> list[Permission]:
        """Collapse duplicate space permission grants (common with access-class ACLs)."""
        seen: set[tuple[Any, ...]] = set()
        deduped: list[Permission] = []
        for permission in permissions:
            key = (
                permission.entity_type,
                permission.external_id or "",
                permission.email or "",
                permission.type,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(permission)
        return deduped

    async def _transform_space_permission(self, perm_data: dict[str, Any]) -> Optional[Permission]:
        """
        Transform Confluence space permission to Permission object.

        Maps Confluence operations to PermissionType:
        - administer → OWNER
        - read → READ
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - delete (space) → OWNER

        Args:
            perm_data: Raw permission data from Confluence API

        Returns:
            Permission object or None if invalid or user/group not found in DB
        """
        try:
            principal = perm_data.get("principal", {})
            operation = perm_data.get("operation", {})

            principal_type = principal.get("type")  # "user" or "group"
            principal_id = principal.get("id")  # accountId or groupId
            operation_key = operation.get("key")  # e.g., "read", "administer"
            target_type = operation.get("targetType")  # e.g., "space", "page"

            if not principal_type or not principal_id or not operation_key:
                return None

            if self._access_class_id(perm_data):
                # Callers that keep every grant use _access_class_permissions; this keeps the first.
                granted = await self._access_class_permissions(perm_data)
                return granted[0] if granted else None

            # Map Confluence permission to PermissionType
            permission_type = self._map_confluence_permission(operation_key, target_type)

            # Use common function to create permission
            # A user whose email is hidden keeps their space access through a pseudo-group, as on pages.
            return await self._create_permission_from_principal(
                principal_type,
                principal_id,
                permission_type,
                create_pseudo_group_if_missing=True,
            )

        except Exception as e:
            # Raised, not dropped: saving the space without this grant would take it away.
            self.logger.error(f"❌ Failed to transform space permission: {e}")
            raise

    def _map_confluence_permission(self, operation_key: str, target_type: str) -> PermissionType:
        """
        Map Confluence operation to PermissionType enum.

        Mapping logic:
        - administer → OWNER
        - read → READ
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - delete (space) → OWNER
        - any other operation → READ (default)

        Args:
            operation_key: Operation key (e.g., "read", "create", "delete")
            target_type: Target type (e.g., "space", "page")

        Returns:
            PermissionType enum value
        """
        # Administer = OWNER
        if operation_key == "administer":
            return PermissionType.OWNER

        # Read = READ
        if operation_key == "read":
            return PermissionType.READ

        # Delete space = OWNER
        if operation_key == "delete" and target_type == "space":
            return PermissionType.OWNER

        # Page/blogpost/attachment operations = WRITE
        if (
            target_type in ["page", "blogpost", "attachment"]
            and operation_key in ["create", "delete", "archive"]
        ):
            return PermissionType.WRITE

        # Unrecognized operations default to READ
        return PermissionType.READ

    def _map_page_permission(self, operation: str) -> PermissionType:
        """
        Map page restriction operation to PermissionType enum.

        Page restrictions only have two operations:
        - read → READ
        - update → WRITE
        Any other value defaults to READ.

        Args:
            operation: Operation string ("read" or "update")

        Returns:
            PermissionType enum value
        """
        if operation == "read":
            return PermissionType.READ
        elif operation == "update":
            return PermissionType.WRITE
        else:
            return PermissionType.READ

    async def _transform_page_restriction_to_permissions(
        self,
        restriction_data: dict[str, Any]
    ) -> list[Permission]:
        """
        Transform page restriction data (from v1 API) to Permission objects.
        Creates pseudo-groups for users without email to preserve permissions.

        The v1 API returns restrictions in this format:
        {
            "operation": "read" | "update",
            "restrictions": {
                "user": {
                    "results": [{"type": "known", "accountId": "...", "displayName": "..."}]
                },
                "group": {
                    "results": [{"type": "group", "name": "...", "id": "..."}]
                }
            }
        }

        Args:
            restriction_data: Single restriction object with operation and restrictions

        Returns:
            List of Permission objects
        """
        permissions = []

        try:
            operation = restriction_data.get("operation")
            if operation != "read":
                return permissions

            # Map operation to PermissionType
            permission_type = self._map_page_permission(operation)

            restrictions = restriction_data.get("restrictions", {})

            # Process user restrictions - create pseudo-group if user not found
            user_restrictions = restrictions.get("user", {})
            user_results = user_restrictions.get("results", [])

            for user_data in user_results:
                # Extract accountId (could be under different keys)
                principal_id = user_data.get("accountId") or user_data.get("id")
                if not principal_id:
                    # Dropping it could leave the restriction with no grants, which reads as open.
                    permissions.append(unresolved_principal_permission("unidentified-user", permission_type))
                    continue
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "user",
                        principal_id,
                        permission_type,
                        create_pseudo_group_if_missing=True  # Enable pseudo-group creation for record-level permissions
                    )
                    permissions.append(permission or unresolved_principal_permission(principal_id, permission_type))

            # Process group restrictions
            group_restrictions = restrictions.get("group", {})
            group_results = group_restrictions.get("results", [])

            for group_data in group_results:
                principal_id = group_data.get("id")
                if not principal_id and group_data.get("name"):
                    principal_id = (await self._ensure_group_name_cache()).get(str(group_data["name"]).lower())
                if not principal_id:
                    permissions.append(unresolved_principal_permission("unidentified-group", permission_type))
                    continue
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "group",
                        principal_id,
                        permission_type,
                        create_pseudo_group_if_missing=False  # Groups don't need pseudo-groups
                    )
                    permissions.append(permission or unresolved_principal_permission(principal_id, permission_type))

        except Exception as e:
            self.logger.error(f"❌ Failed to transform page restriction: {e}")
            raise

        return permissions

    async def _fetch_group_members(
        self, group_id: str, group_name: str
    ) -> Optional[tuple[list[str], list[str]]]:
        """
        Fetch all members of a group with pagination.

        Args:
            group_id: The group ID
            group_name: The group name (for logging)

        Returns:
            Tuple of (member emails, accountIds for members without email in API response),
            or None when the member list could not be read in full.
        """
        try:
            member_emails: list[str] = []
            member_account_ids: list[str] = []
            batch_size = 100
            start = 0

            # Paginate through group members
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_group_members(
                    group_id=group_id,
                    start=start,
                    limit=batch_size
                )

                if response and response.status == HttpStatusCode.NOT_FOUND.value:
                    # The group no longer exists, so it has no members to keep (not even earlier pages).
                    self.logger.warning(f"Group {group_name} was not found while reading its members")
                    return [], []

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to fetch members for group {group_name}: {response.status if response else 'No response'}")
                    return None

                response_data = response.json()
                members_data = response_data.get("results", [])

                if not members_data:
                    break

                for member_data in members_data:
                    email = (member_data.get("email") or "").strip()
                    account_id = (member_data.get("accountId") or member_data.get("id") or "").strip()
                    # Deactivated accounts keep their memberships in Confluence; the product
                    # API reports the status only here (search/user leaves it out).
                    if member_data.get("accountStatus", "active") != "active":
                        if account_id:
                            self._pending_inactive_ids.add(account_id)
                        continue
                    if email:
                        self._pending_active_emails.add(email.lower())
                        member_emails.append(email)
                    elif account_id:
                        member_account_ids.append(account_id)
                    else:
                        self.logger.debug(
                            "Skipping group member %s: no email or accountId",
                            member_data.get("displayName"),
                        )

                next_start = v1_next_start(response_data, start, len(members_data), batch_size, use_link_offset=True)
                if next_start is None:
                    break
                start = next_start

            return member_emails, member_account_ids

        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch members for group {group_name}: {e}")
            return None

    async def _remove_groups_gone_from_source(self, listed_group_ids: set[str]) -> None:
        """Delete the stored groups a complete group listing no longer has, with their access.

        Space grants are re-read every sync, but a page restricted to a deleted group is not,
        so the group's former members kept that page until it was edited. Pseudo-groups are
        the connector's own and stay. An empty listing removes nothing: that is the account
        failing, not every group going.
        """
        if not listed_group_ids:
            return
        try:
            stored = await self.data_entities_processor.get_all_user_groups(self.connector_id, raise_on_error=True)
        except Exception as e:
            self.logger.warning(f"Could not read the stored groups; none removed: {e}")
            self._note_problem("Couldn't check for deleted Confluence groups; the next sync tries again", unread=False)
            return
        for group in stored:
            if (group.name or "").startswith(PSEUDO_USER_GROUP_PREFIX):
                continue
            if str(group.source_user_group_id) in listed_group_ids:
                continue
            # An offset list can skip a group that shifted between pages: only a 404 says it is gone.
            if not await self._group_is_gone(group.source_user_group_id):
                continue
            try:
                await self.data_entities_processor.on_user_group_deleted(group.source_user_group_id, self.connector_id)
                self.logger.info(f"Removed group {group.name}: Confluence no longer has it")
            except Exception as e:
                self.logger.warning(f"Could not remove group {group.name}: {e}")
                self._note_problem(
                    f"Couldn't remove group {group.name}, which Confluence no longer has; the next sync tries again",
                    unread=False,
                )

    async def _group_is_gone(self, group_id: str) -> bool:
        """True only when Confluence answers 404 for the group; any other answer keeps it."""
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_group_members(group_id=group_id, start=0, limit=1)
        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.debug(f"Could not look up group {group_id}: {e}")
            return False
        return response is not None and response.status == HttpStatusCode.NOT_FOUND.value

    async def _restore_reactivated_users(self) -> None:
        """Save the users _sync_users held back whose account this sync's full group read
        reports active again. Statuses from a partial group read are not trusted."""
        held_back, self._held_back_users = self._held_back_users, []
        if not self._group_read_complete:
            if held_back:
                self.logger.warning(
                    f"Not saving {len(held_back)} Confluence user(s) this sync: their account status is unknown"
                )
                self._note_problem(
                    f"Didn't save {len(held_back)} Confluence user(s): their account status is unknown "
                    "until every group's member list can be read"
                )
            return
        active = [u for u in held_back if u.source_user_id not in self._inactive_account_ids]
        if active:
            await self._save_app_users(active)
            self.logger.info(f"Saved {len(active)} held-back Confluence user(s) the group read reports active")

    async def _app_user_from_linked_source_id(self, account_id: str) -> Optional[AppUser]:
        """Build AppUser for a Confluence accountId linked to this connector.

        Returns None when no user is linked to the accountId. A failed read
        raises instead: a group saved from it would lose its members.
        """
        user = await self.data_entities_processor.get_user_by_source_id(
            source_user_id=account_id,
            connector_id=self.connector_id,
            raise_on_error=True,
        )
        if not user or not user.email:
            return None
        return AppUser(
            app_name=Connectors.CONFLUENCE,
            connector_id=self.connector_id,
            source_user_id=account_id,
            org_id=self.data_entities_processor.org_id,
            email=user.email,
            full_name=user.full_name or user.email,
            is_active=False,
        )

    async def _resolve_group_app_users(
        self,
        member_emails: list[str],
        member_account_ids: list[str],
    ) -> list[AppUser]:
        """Resolve group members by email and by linked accountId."""
        app_users = await self._get_app_users_by_emails(member_emails)
        seen_emails = {u.email for u in app_users if u.email}

        for account_id in member_account_ids:
            app_user = await self._app_user_from_linked_source_id(account_id)
            if app_user and app_user.email not in seen_emails:
                app_users.append(app_user)
                seen_emails.add(app_user.email)

        return app_users

    async def _get_app_users_by_emails(self, emails: list[str]) -> list[AppUser]:
        """
        Get AppUser objects by their email addresses from database.

        Args:
            emails: List of user email addresses

        Returns:
            List of AppUser objects found in database

        Raises:
            Whatever the database read raised. A failed read is not "none of
            these people are users": a group saved from it would lose its members.
        """
        if not emails:
            return []

        # Fetch all users from database
        all_app_users = await self.data_entities_processor.get_all_app_users(
            connector_id=self.connector_id, raise_on_error=True
        )

        self.logger.debug(f"Fetched {len(all_app_users)} total users from database for email lookup")

        # Create email lookup map
        email_set = set(emails)

        # Filter users by email
        filtered_users = [user for user in all_app_users if user.email in email_set]

        if len(filtered_users) < len(emails):
            missing_count = len(emails) - len(filtered_users)
            self.logger.debug(f"  ⚠️ {missing_count} user(s) not found in database")

        return filtered_users

    def _transform_to_user_group(
        self,
        group_data: dict[str, Any]
    ) -> AppUserGroup | None:
        """
        Transform Confluence group data to AppUserGroup entity.

        Args:
            group_data: Raw group data from Confluence API

        Returns:
            AppUserGroup object or None if transformation fails
        """
        try:
            group_id = group_data.get("id")
            group_name = group_data.get("name")

            if not group_id or not group_name:
                return None

            return AppUserGroup(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_group_id=group_id,
                name=group_name,
                org_id=self.data_entities_processor.org_id,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform group: {e}")
            return None
