"""Confluence Data Center: reading who may see what from Confluence.

Space permissions, page and blog post restrictions, the audit-log refresh of changed restrictions, and groups
with their members. The free connector keeps listing and syncing spaces and content and calls these methods;
without a licence key a company-wide connector reads none of this (the processor's licence gate).
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from app.config.constants.arangodb import (
    AccessRule,
    CollectionNames,
    Connectors,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.sources.atlassian.core.confluence_access import (
    apply_page_access_to_dependents,
    unresolved_principal_permission,
    v1_next_start,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    Record,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType

# ------------------------------------------------------------------------------
# API Compatibility Mode
# ------------------------------------------------------------------------------
# Set to True to use Data Center v1 APIs (name-based group members, v1 space permissions).
# Set to False to use Cloud v2 APIs (ID-based group members, v2 space permissions).
# This allows testing the connector with Cloud instances before deploying to DC.
#
# When USE_DATA_CENTER_APIS = False:
#   - Space permissions: GET /wiki/api/v2/spaces/{id}/permissions (cursor-paginated)
#   - Group members: GET /wiki/rest/api/group/{id}/membersByGroupId
#   - User ID field: accountId (Cloud)
#
# When USE_DATA_CENTER_APIS = True:
#   - Space permissions: GET /wiki/rest/api/space/{key}/permissions (no pagination)
#   - Group members: GET /wiki/rest/api/group/{name}/member (offset-paginated)
#   - User ID field: userKey (DC), with accountId fallback for Cloud compatibility
# ------------------------------------------------------------------------------
USE_DATA_CENTER_APIS = True

# Constant for pseudo-user group prefix
PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"


class ConfluenceDataCenterPermissionsMixin:
    """The permission-reading half of ``ConfluenceDataCenterConnector``."""

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
        if await self._copies_no_source_access():
            return
        try:
            self.logger.info("Starting user group synchronization...")

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
                    self._sync_read_incomplete = True
                    break

                response_data = response.json()
                groups_data = response_data.get("results", [])

                if not groups_data:
                    break

                # Process each group and its members
                for group_data in groups_data:
                    try:
                        group_id = group_data.get("id")
                        group_name = group_data.get("name")

                        if not group_name:
                            continue

                        self.logger.debug(f"  Processing group: {group_name} ({group_id})")

                        # Fetch members: name-based (DC) or ID-based (Cloud) based on flag
                        member_emails = await self._fetch_group_members(
                            group_name=group_name,
                            group_id=group_id
                        )
                        if member_emails is None:
                            # Saving the group now would replace its members with an empty list.
                            self.logger.warning(
                                f"Keeping the stored members of group {group_name}: its member list could not be read"
                            )
                            await self._keep_group_as_stored(group_id or group_name)
                            continue

                        # Create user group
                        user_group = self._transform_to_user_group(group_data)
                        if not user_group:
                            continue

                        # Get AppUser objects for members
                        try:
                            app_users = await self._get_app_users_by_emails(member_emails)
                        except Exception as lookup_error:
                            # Saving the group now would replace its members with an empty list.
                            self.logger.warning(
                                f"Keeping the stored members of group {group_name}: "
                                f"its members could not be looked up ({lookup_error})"
                            )
                            await self._keep_group_as_stored(group_id or group_name)
                            continue

                        # Save group with members
                        await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                        total_groups_synced += 1
                        total_memberships_synced += len(app_users)
                        self.logger.debug(f"Group {group_name}: {len(app_users)} members")

                    except Exception as group_error:
                        self.logger.error(f"❌ Failed to process group {group_data.get('name')}: {group_error}")
                        self._sync_read_incomplete = True
                        continue

                next_start = v1_next_start(response_data, start, len(groups_data), batch_size, use_link_offset=False)
                if next_start is None:
                    break
                start = next_start

            self.logger.info(f"✅ Group sync complete. Groups: {total_groups_synced}, Memberships: {total_memberships_synced}")

        except Exception as e:
            self.logger.error(f"❌ Group sync failed: {e}", exc_info=True)
            raise

    async def _keep_group_as_stored(self, external_group_id: str) -> None:
        """Note a group whose members could not be read kept as stored, so a full sync leaves its links."""
        try:
            await self.data_entities_processor.note_kept_user_group(self.connector_id, external_group_id)
        except Exception as e:
            self.logger.warning(f"Could not note group {external_group_id} kept; this full sync unlinks nothing: {e}")
            self._sync_read_incomplete = True

    async def _refresh_archived_page_access(self, page: Record) -> bool:
        """Write an archived page's restrictions as the audit pass does; the search never re-reads it."""
        permissions = await self._fetch_page_permissions(page.external_record_id)
        if permissions is None:
            return False
        record = await self.data_entities_processor.get_typed_record_by_external_id(
            self.connector_id, page.external_record_id
        )
        if record is None:
            return False
        # A READ restriction keeps inheriting and is recorded as RESTRICTED instead; EDIT-only stays STRICT.
        record.access_rule = (
            AccessRule.RESTRICTED if any(p.type == PermissionType.READ for p in permissions) else AccessRule.STRICT
        )
        record.rewrite_permissions = True
        await self.data_entities_processor.on_new_records([(record, permissions)])
        await apply_page_access_to_dependents(
            self.data_entities_processor,
            self.connector_id,
            page.external_record_id,
            permissions,
            inherits_space=record.inherit_permissions,
        )
        return True

    async def _sync_permission_changes_from_audit_log(self) -> None:
        """
        Sync permission changes for pages/blogs using Confluence DC Auditing API.

        This method tracks permission changes that don't update the content's
        lastModified timestamp, ensuring we capture:
        - Content restriction added (user/group gets access)
        - Content restriction removed (user/group loses access)

        Flow:
        1. Get last audit sync time
        2. If first run (no checkpoint): Initialize with current time and skip (permissions already synced)
        3. If subsequent run: Fetch audit events since last sync
        4. Extract unique content IDs from audit records
        5. For each content ID: Check if exists in DB and update permissions
        6. Update audit log sync point with current timestamp

        Note: First run initializes checkpoint but skips permission sync because
        the initial content sync (_sync_content) already synced all permissions.
        """
        if await self._copies_no_source_access():
            return
        try:
            self.logger.info("🔍 Starting permission sync from audit log...")

            # Sync point key for audit log
            audit_sync_key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "permissions", "audit_log")

            # Get last audit sync timestamp
            last_audit_sync = await self.audit_log_sync_point.read_sync_point(audit_sync_key)
            last_sync_time_ms = last_audit_sync.get("last_sync_time_ms") if last_audit_sync else None

            # Current time as checkpoint
            current_time_ms = int(datetime.now().timestamp() * 1000)

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

            self.logger.info(f"🔄 Fetching audit events from {last_sync_time_ms} to {current_time_ms}")

            # Fetch audit events and extract content IDs that had permission changes
            content_ids = await self._fetch_permission_audit_content_ids(last_sync_time_ms, current_time_ms)

            if content_ids is None:
                self.logger.warning(
                    "Keeping the audit log checkpoint: the audit log could not be read in full, "
                    "so the next sync reads this window again"
                )
                return

            if not content_ids:
                self.logger.info("✅ No permission changes found in audit log")
                # Update sync point even if no changes
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            self.logger.info(f"📋 Found {len(content_ids)} content IDs with permission changes")

            # Sync permissions by content IDs (only if exists in DB)
            await self._sync_content_permissions_by_ids(content_ids)

            # Update audit log sync point with current time
            await self.audit_log_sync_point.update_sync_point(
                audit_sync_key,
                {"last_sync_time_ms": current_time_ms}
            )

            self.logger.info("✅ Permission sync from audit log completed")

        except Exception as e:
            self.logger.error(f"❌ Permission sync from audit log failed: {e}", exc_info=True)
            raise

    async def _fetch_permission_audit_content_ids(
        self,
        start_date_ms: int,
        end_date_ms: int
    ) -> Optional[list[str]]:
        """
        Fetch audit events from DC Auditing API and extract content IDs with permission changes.

        Uses /rest/auditing/1.0/events with pageCursor pagination.
        Filters for:
        - category = "Pages and Blogs" (DC End user activity)
        - scope = content (pages/blogs, not global or space-level)

        Args:
            start_date_ms: Start timestamp in milliseconds (Unix epoch * 1000)
            end_date_ms: End timestamp in milliseconds (Unix epoch * 1000)

        Returns:
            List of unique content IDs (pages/blogs) that had permission changes, or None
            when a page of the audit log could not be read.
        """
        content_ids_set: set[str] = set()
        batch_size = 100
        page_cursor: Optional[str] = None

        # Convert millisecond timestamps to ISO-8601 UTC format
        from_date = datetime.fromtimestamp(start_date_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        to_date = datetime.fromtimestamp(end_date_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_auditing_events_v1(
                from_date=from_date,
                to_date=to_date,
                limit=batch_size,
                page_cursor=page_cursor,
                categories="Pages and Blogs"  # Filter server-side to reduce noise
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"⚠️ Failed to fetch audit events: {response.status if response else 'No response'}")
                return None

            response_data = response.json()
            audit_records = response_data.get("entities", [])

            if not audit_records:
                break

            # Process each audit record
            for record in audit_records:
                content_id = self._extract_content_id_from_audit_record(record)
                if content_id:
                    content_ids_set.add(content_id)

            # Check for next page using pageCursor
            paging_info = response_data.get("pagingInfo", {})
            if paging_info.get("lastPage", True):
                break
            
            page_cursor = paging_info.get("nextPageCursor")
            if not page_cursor:
                break

        return list(content_ids_set)

    @staticmethod
    def _normalize_audit_record(record: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize audit record to unified internal shape for both Cloud and DC.

        Handles:
        - Cloud: category at top level, associatedObjects, objectType
        - DC: category in type.category, affectedObjects, type (case-insensitive)

        Args:
            record: Raw audit record from either Cloud or DC API

        Returns:
            Normalized record with consistent keys:
            {
                "category": str,
                "objects": [{"name": str, "type": str, "id": str|None}, ...]
            }
        """
        # Extract category
        if "type" in record and isinstance(record["type"], dict):
            # DC Auditing API: category in type.category
            category = (record["type"].get("category") or "").lower()
        else:
            # Cloud or legacy: category at top level
            category = (record.get("category") or "").lower()

        # Extract objects list
        objects = record.get("affectedObjects") or record.get("associatedObjects") or []

        # Normalize each object
        normalized_objects = []
        for obj in objects:
            obj_type = (obj.get("type") or obj.get("objectType") or "").lower()
            normalized_objects.append({
                "name": obj.get("name"),
                "type": obj_type,
                "id": obj.get("id")
            })

        return {
            "category": category,
            "objects": normalized_objects
        }

    def _extract_content_id_from_audit_record(self, record: dict[str, Any]) -> Optional[str]:
        """
        Extract content ID from a DC audit record if it's a content permission change.

        Filters for:
        - category is "Pages and blogs" (case-insensitive — DC End user activity)
        - Has a Page or Blog/BlogPost in affectedObjects (content-level permission)
        - Has a Space in affectedObjects (confirms it's content, not global)
        - Content object must have an ID field

        Args:
            record: Raw audit event record from DC Auditing API

        Returns:
            Content ID (page/blog) or None if not a content permission change or ID missing
        """
        normalized = self._normalize_audit_record(record)
        
        if normalized["category"] != "pages and blogs":
            return None

        objects = normalized["objects"]

        has_space = any(
            obj["type"] in self._AUDIT_SPACE_OBJECT_TYPES
            for obj in objects
        )
        content_obj = next(
            (
                obj for obj in objects
                if obj["type"] in self._AUDIT_CONTENT_OBJECT_TYPES
            ),
            None,
        )

        # Content restriction must have both page/blog AND space
        if not has_space or not content_obj:
            return None

        # Return ID if present (DC provides IDs, Cloud does not)
        return content_obj.get("id")

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
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_content_by_titles(
                    titles=batch_titles,
                    expand="version,space,history.lastUpdated,ancestors"
                )

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to search content by titles: {response.status if response else 'No response'}")
                    continue

                response_data = response.json()
                content_items = response_data.get("results", [])

                # Extract api_base_url from response root for URL construction
                api_base_url = (response_data.get("_links") or {}).get("base")

                if not content_items:
                    self.logger.debug(f"No content found for titles batch {i // batch_size + 1}")
                    continue

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

                        # Transform to WebpageRecord
                        webpage_record = self._transform_to_webpage_record(item_data, record_type, api_base_url=api_base_url)
                        if not webpage_record:
                            continue

                        # Fetch current permissions. None means unreadable — skip.
                        permissions = await self._fetch_page_permissions(item_id)
                        if permissions is None:
                            self.logger.warning(f"Restrictions for {item_id} could not be read; keeping what is stored")
                            has_failures = True
                            continue
                        total_permissions += len(permissions)

                        # A READ restriction keeps inheriting and is recorded as
                        # RESTRICTED instead; EDIT-only stays STRICT.
                        webpage_record.access_rule = (
                            AccessRule.RESTRICTED
                            if any(p.type == PermissionType.READ for p in permissions)
                            else AccessRule.STRICT
                        )

                        # Add to batch for update
                        records_with_permissions.append((webpage_record, permissions))
                        total_synced += 1

                    except Exception as item_error:
                        self.logger.error(f"❌ Failed to sync permissions for {item_data.get('title')}: {item_error}")
                        has_failures = True
                        continue

                # Update batch in database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Updated permissions for {len(records_with_permissions)} content items")

            except Exception as batch_error:
                self.logger.error(f"❌ Failed to process titles batch: {batch_error}")
                has_failures = True
                continue

        if has_failures:
            raise ValueError("Failed to sync permissions for some content items")

        if total_skipped > 0:
            self.logger.info(f"🔍 Skipped {total_skipped} items not in database (filtered during sync)")

        self.logger.info(f"✅ Permission sync complete. Items updated: {total_synced}, Permissions: {total_permissions}")

    async def _sync_content_permissions_by_ids(self, content_ids: list[str]) -> None:
        """
        Fetch content by IDs and sync their current permissions.

        IMPORTANT: This method ONLY updates permissions for records that already exist in the database.
        It will NOT create new records, ensuring sync filters are respected.

        For each content ID:
        1. Check if record exists in DB (by external_record_id)
        2. If exists: Fetch current content metadata and permissions, then update
        3. If not exists: Skip (record was filtered out during initial sync)

        Args:
            content_ids: List of Confluence content IDs to sync
        """
        if not content_ids:
            return

        # Deduplicate IDs
        unique_ids = list(set(content_ids))
        
        total_synced = 0
        total_skipped = 0
        total_permissions = 0
        has_failures = False

        self.logger.info(f"🔄 Syncing permissions for {len(unique_ids)} content items by ID")

        for content_id in unique_ids:
            try:
                # Check if record exists in database first (respects sync filters)
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=content_id
                )

                if not existing_record:
                    # Record doesn't exist - it was filtered out during initial sync
                    self.logger.debug(
                        f"Skipping content {content_id} - not in database (filtered out during sync)"
                    )
                    total_skipped += 1
                    continue

                # Fetch content metadata
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_content_v1(
                    content_id=content_id,
                    expand="version,space,history.lastUpdated,ancestors"
                )

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    if response and response.status == HttpStatusCode.NOT_FOUND.value:
                        self.logger.info(f"Content {content_id} not found (likely deleted), skipping permission sync")
                        continue
                    self.logger.warning(
                        f"⚠️ Failed to fetch content {content_id}: "
                        f"{response.status if response else 'No response'}"
                    )
                    has_failures = True
                    continue

                item_data = response.json()
                item_title = item_data.get("title")
                item_type = item_data.get("type", "").lower()

                if not item_title:
                    self.logger.warning(f"⚠️ Content {content_id} has no title, skipping")
                    continue

                # Determine record type
                if item_type == "page":
                    record_type = RecordType.CONFLUENCE_PAGE
                elif item_type == "blogpost":
                    record_type = RecordType.CONFLUENCE_BLOGPOST
                else:
                    self.logger.debug(f"Skipping unknown content type: {item_type}")
                    continue

                self.logger.debug(f"Updating permissions for {item_type}: {item_title} ({content_id})")

                # Transform to WebpageRecord
                webpage_record = self._transform_to_webpage_record(item_data, record_type)
                if not webpage_record:
                    continue

                # Fetch current permissions. None means unreadable — skip.
                permissions = await self._fetch_page_permissions(content_id)
                if permissions is None:
                    self.logger.warning(f"Restrictions for {content_id} could not be read; keeping what is stored")
                    has_failures = True
                    continue
                total_permissions += len(permissions)

                # A READ restriction keeps inheriting and is recorded as
                # RESTRICTED instead; EDIT-only stays STRICT.
                webpage_record.access_rule = (
                    AccessRule.RESTRICTED
                    if any(p.type == PermissionType.READ for p in permissions)
                    else AccessRule.STRICT
                )
                webpage_record.rewrite_permissions = True

                # Update in database
                await self.data_entities_processor.on_new_records([(webpage_record, permissions)])
                # Its stored files and comments would otherwise keep the page's old access.
                await apply_page_access_to_dependents(
                    self.data_entities_processor,
                    self.connector_id,
                    content_id,
                    permissions,
                    inherits_space=webpage_record.inherit_permissions,
                )
                total_synced += 1

            except Exception as item_error:
                self.logger.error(f"❌ Failed to sync permissions for content {content_id}: {item_error}")
                has_failures = True
                continue

        if has_failures:
            raise ValueError("Failed to sync permissions for some content items")

        if total_skipped > 0:
            self.logger.info(f"🔍 Skipped {total_skipped} items not in database (filtered during sync)")

        self.logger.info(f"✅ Permission sync complete. Items updated: {total_synced}, Permissions: {total_permissions}")

    async def _fetch_page_permissions(self, page_id: str) -> Optional[list[Permission]]:
        """
        Fetch read restrictions defined on this page.

        ``relevantViewRestrictions`` also lists ancestor restrictions. Those stay
        on the ancestor; this page inherits them. A result that names another
        content id is skipped. Edit restrictions are ignored.
        """
        if await self._copies_no_source_access():
            return []
        permissions = []

        try:
            self.logger.debug(f"Fetching view permissions for page: {page_id}")

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_relevant_view_restrictions_v1(
                page_id=page_id
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.error(
                    f"❌ Could not read view permissions for page {page_id}: "
                    f"{response.status if response else 'No response'}"
                )
                return None

            response_data = response.json()
            view_restrictions = response_data.get("viewContentRestrictions", {})
            restrictions = view_restrictions.get("results", [])

            for restriction_data in restrictions:
                if restriction_data.get("operation") != "read":
                    continue
                defined_on = (restriction_data.get("content") or {}).get("id")
                if defined_on and str(defined_on) != str(page_id):
                    continue
                operation_permissions = await self._transform_page_restriction_to_permissions(
                    restriction_data
                )
                permissions.extend(operation_permissions)

            self.logger.debug(
                f"Found {len(permissions)} view permissions for page {page_id}"
            )
            return permissions

        except Exception as e:
            # Not []: an unreadable ACL is not an absent ACL. See the docstring.
            self.logger.error(
                f"❌ Could not read view permissions for page {page_id}: {e}"
            )
            return None

    async def _fetch_space_permissions(
        self, space_key_or_id: str, space_name: str, space_id: Optional[str] = None
    ) -> Optional[list[Permission]]:
        """Fetch space permissions using v1 (DC) or v2 (Cloud) API based on USE_DATA_CENTER_APIS.

        When USE_DATA_CENTER_APIS = True (Data Center mode):
          Uses ``GET /wiki/rest/api/space/{spaceKey}/permissions`` (v1).
          Response is a flat JSON array — no pagination needed.
          Each entry has subjects.user/group lists with "operation" field.

          **DC version check:** This endpoint requires DC 9.1+ (CONFSERVER-78176).
          On older DC instances, raises explicit error instead of silently dropping permissions.

        When USE_DATA_CENTER_APIS = False (Cloud mode):
          Uses ``GET /wiki/api/v2/spaces/{id}/permissions`` (v2).
          Response has cursor-paginated {"results": [...]} structure.
          Each entry has principal.id + operation.key fields.

        Args:
            space_key_or_id: Space key (for v1) or space ID (for v2)
            space_name: Space name for logging
            space_id: Optional numeric space ID (required for v2 Cloud mode)

        Returns:
            The space's permissions, or None when they could not be read.
        """
        if await self._copies_no_source_access():
            return []
        try:
            permissions: list[Permission] = []
            datasource = await self._get_fresh_datasource()

            if USE_DATA_CENTER_APIS:
                # Check DC version first
                server_version = await self._get_server_version()
                if server_version < (9, 1):
                    version_str = ".".join(map(str, server_version))
                    self.logger.error(
                        f"❌ Confluence Data Center version {version_str} does not support "
                        f"REST space permissions (requires 9.1+, CONFSERVER-78176). "
                        f"Space '{space_name}' will have no permissions. "
                        f"Upgrade DC to 9.1+ or implement JSON-RPC fallback."
                    )
                    # A permanent limit of this server, not a failed read: there are no grants to keep.
                    return []

                # DC v1: GET /rest/api/space/{key}/permissions (no pagination)
                response = await datasource.get_space_permissions_v1(space_key=space_key_or_id)

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(
                        "Failed to fetch space permissions for %s (HTTP %s)",
                        space_name,
                        response.status if response else "no response",
                    )
                    return None

                perm_entries = response.json()
                if not isinstance(perm_entries, list):
                    self.logger.warning(
                        "Unexpected space permissions response shape for %s: expected list, got %s",
                        space_name,
                        type(perm_entries).__name__,
                    )
                    return []

                for entry in perm_entries:
                    entry_permissions = await self._transform_v1_space_permission_entry(entry)
                    permissions.extend(entry_permissions)

            else:
                # Cloud v2: GET /api/v2/spaces/{id}/permissions (cursor-paginated)
                if not space_id:
                    self.logger.warning(
                        "Cannot fetch Cloud v2 space permissions: space_id missing for %s", space_name
                    )
                    return []

                try:
                    space_id_int = int(space_id)
                except (TypeError, ValueError):
                    self.logger.warning(
                        "Cannot fetch Cloud v2 space permissions: non-numeric space_id %r for %s",
                        space_id,
                        space_name,
                    )
                    return []

                batch_size = 100
                cursor: Optional[str] = None

                while True:
                    response = await datasource.get_space_permissions_assignments(
                        id=space_id_int,
                        limit=batch_size,
                        cursor=cursor,
                    )

                    if not response or response.status != HttpStatusCode.SUCCESS.value:
                        self.logger.warning(
                            "Failed to fetch Cloud v2 space permissions for %s (HTTP %s)",
                            space_name,
                            response.status if response else "no response",
                        )
                        return None

                    response_data = response.json()
                    permissions_data = response_data.get("results", [])

                    if not permissions_data:
                        break

                    for perm_data in permissions_data:
                        permission = await self._transform_v2_space_permission(perm_data)
                        if permission:
                            permissions.append(permission)

                    next_url = response_data.get("_links", {}).get("next")
                    if not next_url:
                        break

                    cursor = self._extract_cursor_from_next_link(next_url)
                    if not cursor:
                        break

            return permissions

        except Exception as e:
            self.logger.error(
                "Failed to fetch permissions for space %s: %s",
                space_name,
                e,
                exc_info=True,
            )
            return None

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        create_pseudo_group_if_missing: bool = False
    ) -> Optional[Permission]:
        """
        Create Permission object from principal data (user or group).

        This is a common function used by both space and page permission processing.

        Args:
            principal_type: "user" or "group"
            principal_id: accountId for users, groupId for groups (or group name as fallback)
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

                    if not pseudo_group:
                        # Create pseudo-group on-the-fly
                        pseudo_group = await self._create_pseudo_group(principal_id)
                    else:
                        self.data_entities_processor.note_kept_user_groups(self.connector_id, [pseudo_group])

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
                if group:
                    return Permission(
                        external_id=group.source_user_group_id,
                        type=permission_type,
                        entity_type=entity_type
                    )

                # Group not found by ID - try fallback by name
                # (DC space permissions may return only 'name' without UUID 'id')
                self.logger.debug(
                    f"  ⚠️ Group {principal_id} not found by ID, trying name fallback"
                )
                async with self.data_store_provider.transaction() as tx_store:
                    groups = await tx_store.get_nodes_by_filters(
                        collection=CollectionNames.GROUPS.value,
                        filters={"connectorId": self.connector_id, "name": principal_id},
                        raise_on_error=True,
                    )
                if len(groups) > 1:
                    # Granting to one of several same-named groups could give the space to the wrong members.
                    self.logger.warning(
                        f"  ⚠️ Group name {principal_id} matches {len(groups)} groups, skipping permission"
                    )
                    return None
                if groups and groups[0].get("externalGroupId"):
                    external_group_id = groups[0]["externalGroupId"]
                    self.logger.debug(
                        f"  ✓ Found group by name: {principal_id} -> {external_group_id}"
                    )
                    return Permission(
                        external_id=external_group_id,
                        type=permission_type,
                        entity_type=entity_type
                    )

                self.logger.debug(f"  ⚠️ Group {principal_id} not found in DB (by ID or name), skipping permission")
                return None

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to create permission from principal: {e}")
            return None

    async def _create_pseudo_group(self, account_id: str) -> Optional[AppUserGroup]:
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
                app_name=Connectors.CONFLUENCE_DATA_CENTER,
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

    async def _transform_v1_space_permission_entry(
        self, entry: dict[str, Any]
    ) -> list[Permission]:
        """Transform a single v1 space-permission entry into Permission objects.

        The v1 ``GET /wiki/rest/api/space/{key}/permissions`` response is a flat
        array of entries.  Each entry covers one operation and lists all users and
        groups that hold that operation — so one entry can produce many Permissions.

        v1 entry shape:
          {
            "subjects": {
              "user":  { "results": [{ "accountId": "...", ... }] },   # Cloud
              # DC:     { "results": [{ "userKey": "...", "username": "...", ... }] }
              "group": { "results": [{ "id": "...", "name": "...", "type": "group" }] }
            },
            "operation": {
              "operation":  "read",   # key is "operation" (v1), NOT "key" (v2)
              "targetType": "space"
            },
            "anonymousAccess": false
          }

        Differences from the old v2 ``_transform_space_permission``:
          v2 had { "principal": { "type": "user"|"group", "id": "..." },
                   "operation": { "key": "read", "targetType": "space" } }
          — one Permission per entry.
          v1 has subjects.user / subjects.group lists — one entry → many Permissions.

        **anonymousAccess handling:**
          When ``anonymousAccess: true`` for a read operation, the space is public
          and accessible without authentication. We skip creating explicit permissions
          since the space-level public flag means records inherit public access.
        """
        permissions: list[Permission] = []
        try:
            operation = entry.get("operation", {})
            # Cloud v1: operation.operation; DC: operation.operationKey
            operation_key = operation.get("operation") or operation.get("operationKey")
            target_type = operation.get("targetType")

            if not operation_key:
                return permissions

            # Check for anonymous access (public space) — Cloud bundled format
            is_anonymous = entry.get("anonymousAccess", False)
            if is_anonymous and operation_key == "read":
                self.logger.debug(
                    "Space has anonymous read access (public), skipping explicit permissions"
                )
                return permissions

            permission_type = self._map_confluence_permission(operation_key, target_type)

            # DC flat format: one subject per array item
            subject = entry.get("subject")
            if isinstance(subject, dict):
                subject_type = subject.get("type")
                if subject_type == "anonymous":
                    return permissions
                if subject_type == "user":
                    user_id = (
                        subject.get("userKey")
                        or subject.get("accountId")
                        or subject.get("key")
                    )
                    if user_id:
                        perm = await self._create_permission_from_principal(
                            "user", user_id, permission_type
                        )
                        if perm:
                            permissions.append(perm)
                elif subject_type == "group":
                    group_id = subject.get("name") or subject.get("id")
                    if group_id:
                        perm = await self._create_permission_from_principal(
                            "group", group_id, permission_type
                        )
                        if perm:
                            permissions.append(perm)
                return permissions

            subjects = entry.get("subjects", {})

            # Process users (Cloud bundled format)
            for user_data in subjects.get("user", {}).get("results", []):
                # Cloud:  accountId field
                # DC v1:  userKey field (userKey is the stable identifier)
                # Fallback to "key" which some DC versions also expose
                user_id = (
                    user_data.get("accountId")      # Cloud v1
                    or user_data.get("userKey")     # DC v1
                    or user_data.get("key")         # older DC fallback
                )
                if user_id:
                    perm = await self._create_permission_from_principal("user", user_id, permission_type)
                    if perm:
                        permissions.append(perm)

            # Process groups
            for group_data in subjects.get("group", {}).get("results", []):
                # Both Cloud and DC v1 include "id" for groups; DC may only have "name"
                group_id = group_data.get("id") or group_data.get("name")
                if group_id:
                    perm = await self._create_permission_from_principal("group", group_id, permission_type)
                    if perm:
                        permissions.append(perm)

        except Exception as e:
            self.logger.error(f"❌ Failed to transform v1 space permission entry: {e}")

        return permissions

    async def _transform_v2_space_permission(self, perm_data: dict[str, Any]) -> Optional[Permission]:
        """Transform Confluence Cloud v2 space permission to Permission object.

        Used only when USE_DATA_CENTER_APIS = False (Cloud testing mode).

        Cloud v2 shape:
          {
            "principal": {
              "type": "user" | "group",
              "id": "<accountId or groupId>"
            },
            "operation": {
              "key": "read",           # v2 uses "key", not "operation"
              "targetType": "space"
            }
          }

        Maps Confluence operations to PermissionType:
        - administer → OWNER
        - read → READ
        - create/delete (comment) → COMMENT
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - restrict_content/export → OTHER
        - delete (space) → OWNER
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

            # Map Confluence permission to PermissionType
            permission_type = self._map_confluence_permission(operation_key, target_type)

            # Use common function to create permission
            return await self._create_permission_from_principal(
                principal_type,
                principal_id,
                permission_type
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform Cloud v2 space permission: {e}")
            return None

    def _map_confluence_permission(self, operation_key: str, target_type: str) -> PermissionType:
        """
        Map Confluence operation to PermissionType enum.

        Mapping logic:
        - administer → OWNER
        - read → READ
        - create/delete (comment) → COMMENT
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - restrict_content/export → OTHER
        - delete (space) → OWNER

        Args:
            operation_key: Operation key (e.g., "read", "create", "delete")
            target_type: Target type (e.g., "space", "page", "comment")

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

        # Commenting grants no more than read access.
        if target_type == "comment" and operation_key in ["create", "delete"]:
            return PermissionType.READ

        # Page/blogpost/attachment operations = WRITE
        if target_type in ["page", "blogpost", "attachment"]:
            if operation_key in ["create", "delete", "archive"]:
                return PermissionType.WRITE

        # Everything else = READ
        return PermissionType.READ

    def _map_page_permission(self, operation: str) -> PermissionType:
        """
        Map page restriction operation to PermissionType enum.

        Page restrictions only have two operations:
        - read → READ
        - update → WRITE

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
                    # Cloud v1: accountId is the stable user identifier
                    "results": [{"type": "known", "accountId": "...", "displayName": "..."}]
                    # DC v1:    userKey is the stable user identifier; accountId absent
                    # "results": [{"type": "known", "userKey": "...", "username": "...", "displayName": "..."}]
                },
                "group": {
                    # Cloud: id + name present; DC: often name only
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
                # Cloud v1: accountId is the stable user identifier
                # DC v1:    userKey is the stable identifier (accountId is absent)
                # Fallback to generic "id" or "key" for edge cases
                principal_id = (
                    user_data.get("accountId")      # Cloud v1
                    or user_data.get("userKey")     # DC v1
                    or user_data.get("key")         # older DC fallback
                    or user_data.get("id")          # generic fallback
                )
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
                # Data Center often identifies a group by name only; the lookup falls back to name.
                principal_id = group_data.get("id") or group_data.get("name")
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
        self, group_name: str, group_id: Optional[str] = None
    ) -> Optional[list[str]]:
        """Fetch all members of a group and return their email addresses.

        When USE_DATA_CENTER_APIS = True (Data Center mode):
          Uses ``GET /rest/api/group/{groupName}/member`` (name-based).
          Preferred for DC because DC groups may not have stable UUID-style IDs.

        When USE_DATA_CENTER_APIS = False (Cloud mode):
          Uses ``GET /rest/api/group/{id}/membersByGroupId`` (ID-based).
          Cloud groups always have numeric/UUID IDs.

        Email resolution for DC:
          Cloud: ``email`` is always present in the member listing.
          DC v1: ``email`` may be absent for some users (e.g. LDAP-synced accounts).
          When email is missing we attempt a follow-up ``GET /rest/api/user?key={userKey}``
          to resolve the full profile, which usually includes email.

          Elastic's reference implementation identifies DC users by ``userKey``/
          ``username`` and skips email entirely.  Our connector requires email, so
          we fall back to the user-detail call and skip if still absent.

        Args:
            group_name: The group name (used for name-based endpoint in DC mode)
            group_id: Optional group ID (required for ID-based endpoint in Cloud mode)

        Returns:
            List of resolved email addresses for group members, or None when the
            member list could not be read in full.
        """
        try:
            member_emails = []
            batch_size = 200
            start = 0

            while True:
                datasource = await self._get_fresh_datasource()

                if USE_DATA_CENTER_APIS:
                    # DC: name-based endpoint
                    response = await datasource.get_group_members_by_name(
                        group_name=group_name,
                        start=start,
                        limit=batch_size
                    )
                else:
                    # Cloud: ID-based endpoint
                    if not group_id:
                        self.logger.warning(
                            "Cannot fetch Cloud group members: group_id missing for %s", group_name
                        )
                        return None
                    response = await datasource.get_group_members(
                        group_id=group_id,
                        start=start,
                        limit=batch_size
                    )

                if response and response.status == HttpStatusCode.NOT_FOUND.value:
                    # The group no longer exists, so it has no members to keep (not even earlier pages).
                    self.logger.warning("Group %s was not found while reading its members", group_name)
                    return []

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(
                        "⚠️ Failed to fetch members for group %s: HTTP %s",
                        group_name,
                        response.status if response else "no response",
                    )
                    return None

                response_data = response.json()
                members_data = response_data.get("results", [])

                if not members_data:
                    break

                for member_data in members_data:
                    email = member_data.get("email", "").strip()

                    if not email:
                        # Resolve email via GET /user when the listing omits it.
                        # Cloud: use ?accountId=... (passing accountId as ?key= returns 400).
                        # DC:    use ?key=... with userKey (or legacy key).
                        if USE_DATA_CENTER_APIS:
                            user_identifier = (
                                member_data.get("userKey")
                                or member_data.get("key")
                                or member_data.get("accountId")
                            )
                            lookup_as: Literal["key", "accountId"] = "key"
                        else:
                            account_id = member_data.get("accountId")
                            if account_id:
                                user_identifier = account_id
                                lookup_as = "accountId"
                            else:
                                user_identifier = (
                                    member_data.get("key")
                                    or member_data.get("userKey")
                                )
                                lookup_as = "key"
                        if user_identifier:
                            email = await self._resolve_user_email(
                                user_identifier, datasource, lookup_as=lookup_as
                            )
                            if email is None:
                                # Dropping this member would take away their access through the group.
                                self.logger.warning(
                                    "Keeping the stored members of group %s: the email of member %s could not be looked up",
                                    group_name,
                                    user_identifier,
                                )
                                return None

                    if email:
                        member_emails.append(email)
                    else:
                        self.logger.debug(
                            "Skipping group member '%s' in group '%s': no email found",
                            member_data.get("displayName") or member_data.get("username", "unknown"),
                            group_name,
                        )

                next_start = v1_next_start(response_data, start, len(members_data), batch_size, use_link_offset=False)
                if next_start is None:
                    break
                start = next_start

            return member_emails

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch members for group {group_name}: {e}")
            return None

    async def _resolve_user_email(
        self,
        user_identifier: str,
        datasource: Any,
        *,
        lookup_as: Literal["key", "accountId"] = "key",
    ) -> Optional[str]:
        """Resolve a user's email via ``GET /rest/api/user``.

        ``lookup_as`` must match how Confluence identifies the user:
        - ``key``: Data Center ``userKey`` / legacy username (``?key=``).
        - ``accountId``: Confluence Cloud Atlassian account id (``?accountId=``).

        When ``USE_DATA_CENTER_APIS`` is False (Cloud testing), callers should pass
        ``lookup_as="accountId"`` whenever the identifier is a Cloud ``accountId``;
        using ``?key=`` with an ``accountId`` value returns HTTP 400 on Cloud.

        Returns an empty string when the user has no email or is not visible to us
        (a 4xx other than 401 or 429), and None when the lookup itself failed (no response,
        401, 429, 5xx or a network error), so the caller can keep what is stored.
        """
        try:
            response = await datasource.get_user_by_key(
                user_identifier, lookup_as=lookup_as
            )
        except Exception as e:
            self.logger.warning(
                "Email lookup failed (%s=%s): %s", lookup_as, user_identifier, e
            )
            return None
        if not response:
            return None
        if response.status == HttpStatusCode.SUCCESS.value:
            user_data = response.json()
            return (user_data.get("email") or "").strip() if isinstance(user_data, dict) else ""
        if (
            response.status in (HttpStatusCode.UNAUTHORIZED.value, HttpStatusCode.TOO_MANY_REQUESTS.value)
            or response.status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value
        ):
            return None
        return ""

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
    ) -> Optional[AppUserGroup]:
        """
        Transform Confluence group data to AppUserGroup entity.

        Args:
            group_data: Raw group data from Confluence API

        Returns:
            AppUserGroup object or None if transformation fails
        """
        try:
            group_name = group_data.get("name")
            if not group_name:
                return None
            # DC often returns name only; use name as stable group identifier
            group_id = group_data.get("id") or group_name

            return AppUserGroup(
                app_name=Connectors.CONFLUENCE_DATA_CENTER,
                connector_id=self.connector_id,
                source_user_group_id=group_id,
                name=group_name,
                org_id=self.data_entities_processor.org_id,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform group: {e}")
            return None
