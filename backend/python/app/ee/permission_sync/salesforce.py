"""Salesforce: reading who may see what from Salesforce.

Each user's access to each record and account (UserRecordAccess), the readers of products, roles and public
groups and queues with their members. The free connector keeps syncing content and calls these methods; without a
licence key a company-wide connector reads none of this (the processor's licence gate).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Set, Tuple
from urllib.parse import quote

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.salesforce.rows import (
    SalesforceGroup,
    SalesforceRole,
    SalesforceUser,
    _parse_salesforce_timestamp,
    _sanitize_soql_id,
    _sanitize_soql_ids_batch,
)
from app.connectors.sources.salesforce.sync_notices import ACCESS_KEPT
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.cache.invalidation_hooks import notify_connector_permissions_changed

if TYPE_CHECKING:
    from app.connectors.core.base.data_store.data_store import TransactionStore
    from app.connectors.sources.salesforce.connector import SalesforceRawPageStream
    from app.connectors.sources.salesforce.eligibility import SalesforceEligibility


class SalesforcePermissionsMixin:
    """The permission-reading half of ``SalesforceConnector``."""

    # Salesforce takes at most 5 query subrequests in one composite call (of its 25), and
    # at most 200 RecordIds in one UserRecordAccess query (REST API, Composite; Object
    # Reference, UserRecordAccess).
    URA_IDS_PER_QUERY = 200
    QUERIES_PER_COMPOSITE = 5

    async def _iter_record_access(
        self,
        user_id: str,
        user_email: str,
        record_ids: List[str],
        api_version: str,
        incomplete: set | None = None,
    ) -> AsyncGenerator[tuple[str, str, str], None]:
        """
        Yields (record_id, user_email, access_level) tuples for a given user,
        streaming results as each composite batch resolves. A query that failed
        adds the user's email to ``incomplete``: what was yielded is then not the
        user's full access.
        """
        unique_ids = list(dict.fromkeys(rid for rid in record_ids if rid))
        if not unique_ids or not user_id:
            return

        try:
            safe_user_id = _sanitize_soql_id(user_id)
        except ValueError:
            self.logger.warning("Invalid Salesforce user ID in access check: %r", user_id)
            if incomplete is not None:
                incomplete.add(user_email)
            return

        base_query_url = f"/services/data/v{api_version}/query/"

        sub_requests: list[dict[str, Any]] = []
        batch_ids: dict[str, list[str]] = {}
        for i in range(0, len(unique_ids), self.URA_IDS_PER_QUERY):
            safe_ids = _sanitize_soql_ids_batch(
                unique_ids[i : i + self.URA_IDS_PER_QUERY], self.logger, "access check",
            )
            if not safe_ids:
                continue
            ids_str = "','".join(safe_ids)
            query = (
                f"SELECT RecordId, MaxAccessLevel FROM UserRecordAccess "
                f"WHERE UserId='{safe_user_id}' "
                f"AND RecordId IN ('{ids_str}')"
            )
            reference = f"access_batch_{i}"
            batch_ids[reference] = safe_ids
            sub_requests.append({
                "method": "GET",
                "url": f"{base_query_url}?q={quote(query)}",
                "referenceId": reference,
            })

        for i in range(0, len(sub_requests), self.QUERIES_PER_COMPOSITE):
            batch = sub_requests[i : i + self.QUERIES_PER_COMPOSITE]
            answered: set = set()
            try:
                response = await self.data_source.composite(
                    version=api_version,
                    data={"compositeRequest": batch}
                )
                if not response.success or not response.data:
                    self.logger.error(f"Composite access check failed: {response.error}")
                else:
                    for item in response.data.get("compositeResponse") or []:
                        reference = item.get("referenceId")
                        body = item.get("body")
                        if item.get("httpStatusCode") != 200 or not isinstance(body, dict):
                            continue
                        if not body.get("done", True):
                            # 200 ids fit on one page; anything else is not the whole answer.
                            continue
                        answered.add(reference)
                        for row in body.get("records") or []:
                            rec_id = row.get("RecordId")
                            max_access = row.get("MaxAccessLevel")

                            if max_access in ("All", "Transfer"):
                                access_level = "OWNER"
                            elif max_access in ("Edit", "Delete"):
                                access_level = "WRITER"
                            elif max_access == "Read":
                                access_level = "READER"
                            else:
                                continue  # Skip OTHERS entirely — no yield, no DB write

                            yield rec_id, user_email, access_level
            except Exception as e:
                self.logger.error(
                    f"Error processing access batch {i} for user {user_email}: {e}",
                    exc_info=True
                )
            if incomplete is not None and any(sub["referenceId"] not in answered for sub in batch):
                incomplete.add(user_email)

    async def _sync_roles(
        self,
        role_records_pages: SalesforceRawPageStream,
        user_role_records_pages: SalesforceRawPageStream,
    ) -> None:
        """Sync roles with their member users.  The user-role stream is drained first
        into a map (bounded by user count, the smaller of the two); roles then stream
        through with per-page commits."""
        # Without a licence key no role or group is read from Salesforce; the processor keeps the stored ones.
        if await self.data_entities_processor.skips_permission_writes(self.connector_id):
            return
        try:
            self.logger.info("Starting Salesforce role sync...")
            if not self.data_source:
                self.logger.error("Salesforce data source not initialized")
                return

            # Drain user-role records into the lookup map first.
            role_to_app_users: Dict[str, List[AppUser]] = {}
            async for page in user_role_records_pages:
                for r in page:
                    user = SalesforceUser.model_validate(r)
                    role_id = user.UserRoleId
                    if not role_id:
                        continue
                    role_to_app_users.setdefault(role_id, []).append(
                        self.user_to_app_user(user),
                    )

            total_roles = 0
            async for page in role_records_pages:
                if not page:
                    continue
                roles_batch: List[Tuple[AppRole, List[AppUser]]] = []
                for r in page:
                    role = SalesforceRole.model_validate(r)
                    members = role_to_app_users.get(role.Id, [])
                    roles_batch.append((self.role_to_app_role(role), members))
                if roles_batch:
                    await self.data_entities_processor.on_new_app_roles(roles_batch)
                    total_roles += len(roles_batch)

            self.logger.info("Finished syncing %d Salesforce roles", total_roles)

        except Exception as e:
            self.logger.error(f"Error syncing roles: {e}", exc_info=True)
            raise

    async def _sync_user_groups(
        self,
        api_version: str,
        group_records_pages: SalesforceRawPageStream,
    ) -> None:
        """Sync Public Groups and Queues with flattened membership hierarchy.

        The flattened-membership map is fetched once before the page loop (it is the
        cross-page lookup); each page of group records then commits independently.
        """
        # Without a licence key no role or group is read from Salesforce; the processor keeps the stored ones.
        if await self.data_entities_processor.skips_permission_writes(self.connector_id):
            return
        try:
            self.logger.info("Starting Salesforce Public Groups and Queues sync...")

            flattened_user_memberships = await self._flatten_group_members(api_version)
            if not flattened_user_memberships:
                self.logger.warning("No flattened group memberships found")
                # Still drain the input so the producer can release resources.
                async for _ in group_records_pages:
                    pass
                return

            org_id = self.data_entities_processor.org_id
            total_groups = 0

            async for page in group_records_pages:
                user_groups_batch: List[Tuple[AppUserGroup, List[AppUser]]] = []
                for r in page:
                    group = SalesforceGroup.model_validate(r)
                    group_id = group.Id
                    if not group_id:
                        continue
                    user_data_set = flattened_user_memberships.get(group_id, set())
                    if not user_data_set:
                        self.logger.debug(
                            "Group %s (%s) has no users", group.Name or "", group_id,
                        )
                        continue
                    app_user_group = AppUserGroup(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group.Name or "",
                        org_id=org_id,
                        description=f"Salesforce {group.Type or ''}",
                        source_created_at=_parse_salesforce_timestamp(group.CreatedDate),
                        source_updated_at=_parse_salesforce_timestamp(group.LastModifiedDate),
                    )
                    app_users = [
                        AppUser(
                            app_name=self.connector_name,
                            connector_id=self.connector_id,
                            source_user_id=user_id,
                            email=email,
                            full_name="",
                            org_id=org_id,
                        )
                        for user_id, email in user_data_set
                    ]
                    if app_users:
                        user_groups_batch.append((app_user_group, app_users))

                if user_groups_batch:
                    await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                    total_groups += len(user_groups_batch)

            self.logger.info(
                "Finished syncing %d Salesforce Public Groups/Queues", total_groups,
            )

        except Exception as e:
            self.logger.error(f"Error syncing user groups: {e}", exc_info=True)
            raise

    async def _sync_permissions_edges(self, api_version: str) -> None:
        """
        Sync all Salesforce permission edges in three phases:
        1. Pre-fetch records, record-groups, and DB users in one transaction.
        2. Collect (user, record, access_level) tuples from Salesforce concurrently
           (API calls only — no DB writes during collection).
        3. Replace the permission edges of every record and record group whose access
           was read for every user; a record some user's read failed for keeps its
           stored edges and only gains the fresh ones (unknown is not empty).

        Products are not shared record by record: the product group's readers are the
        users whose permission sets grant Read on Product2 (``_sync_product_readers``).
        """
        # These edges are written straight to the store, so the processor's licence gate is asked here.
        if await self.data_entities_processor.skips_permission_writes(self.connector_id):
            return
        # 1. Users who can sign in: active and not frozen.
        soql_query = "SELECT Id, Email FROM User WHERE IsActive = true"
        sf_users: List[Dict[str, str]] = []
        async for page in self._soql_query_paginated(api_version=api_version, q=soql_query):
            for u in page:
                if u.get("Id") and u.get("Email"):
                    sf_users.append(u)
        eligibility = self._eligibility(api_version)
        frozen = await eligibility.frozen_user_ids()
        if frozen:
            sf_users = [u for u in sf_users if u.get("Id") not in frozen]

        # 2. Fetch records, record groups, and matching DB users in one transaction
        org_id = self.data_entities_processor.org_id
        async with self.data_store_provider.transaction() as tx_store:
            salesforce_records = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORDS.value,
                field="recordType",
                values=[RecordType.DEAL.value, RecordType.TASK.value, RecordType.CASE.value, RecordType.PRODUCT.value],
            )
            # A record in the trash is gone from Salesforce: asking about it can fail the read.
            salesforce_records = [
                r for r in salesforce_records
                if r.get("connectorId") == self.connector_id and not r.get("isDeleted")
            ]
            # Products take their readers from the product group; per-user edges older
            # versions wrote to them are removed and not written again.
            product_record_ids = [
                (r.get("id") or r.get("_key")) for r in salesforce_records
                if r.get("recordType") == RecordType.PRODUCT.value and (r.get("id") or r.get("_key"))
            ]
            salesforce_records = [r for r in salesforce_records if r.get("recordType") != RecordType.PRODUCT.value]
            # A record the last full sync no longer saw (outside the sync filters) was unlinked
            # from the hierarchy. Granting it here would make it readable again on the next
            # incremental sync, so only records still in the hierarchy get readers.
            linked = set(await tx_store.filter_linked_record_ids(
                [rid for rid in ((r.get("id") or r.get("_key")) for r in salesforce_records) if rid]))
            salesforce_records = [r for r in salesforce_records if (r.get("id") or r.get("_key")) in linked]

            salesforce_record_groups = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORD_GROUPS.value,
                field="groupType",
                values=[RecordGroupType.SALESFORCE_ORG.value],
            )
            salesforce_record_groups = [r for r in salesforce_record_groups if r.get("connectorId") == self.connector_id]

            # PipesHub users of this org whose email matches a Salesforce user. Salesforce
            # emails are case-insensitive; a user of another PipesHub org is never matched.
            emails = {u["Email"] for u in sf_users} | {u["Email"].lower() for u in sf_users}
            db_users = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.USERS.value,
                field="email",
                values=sorted(emails),
            )

        # 3. Build O(1) lookup maps to avoid per-record DB round-trips
        parent_org_id = self._get_parent_org_id()
        ext_id_to_record_id: Dict[str, str] = {
            r.get("externalRecordId"): (r.get("id") or r.get("_key"))
            for r in salesforce_records
            if r.get("externalRecordId") and (r.get("id") or r.get("_key"))
        }
        ext_id_to_rg_id: Dict[str, str] = {
            r.get("externalGroupId"): (r.get("id") or r.get("_key"))
            for r in salesforce_record_groups
            if r.get("externalGroupId") and (r.get("id") or r.get("_key"))
        }
        email_to_user_id: Dict[str, str] = {
            u.get("email").lower(): (u.get("id") or u.get("_key"))
            for u in (db_users or [])
            if u.get("email") and (u.get("id") or u.get("_key"))
            and u.get("orgId") == (parent_org_id or org_id)
        }
        # Only Salesforce users with a PipesHub account get edges, so only they are read.
        sf_users = [u for u in sf_users if u["Email"].lower() in email_to_user_id]

        await self._sync_product_readers(eligibility, sf_users)

        salesforce_external_ids = list(ext_id_to_record_id.keys())
        salesforce_rg_external_ids = list(ext_id_to_rg_id.keys())

        if not salesforce_external_ids and not salesforce_rg_external_ids:
            self.logger.info("No Salesforce records found to sync permissions for.")
            return

        self.logger.info(
            "Syncing permissions for %d users across %d records and %d record groups.",
            len(sf_users), len(salesforce_external_ids), len(salesforce_rg_external_ids),
        )

        # 4. Collect all permission tuples from Salesforce API concurrently (no DB writes)
        MAX_CONCURRENT_USERS = 5
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_USERS)

        # Users (lower-cased emails) some of whose access could not be read this run.
        unknown_users: set = set()
        tasks = [
            self._sync_permissions_for_user(
                user=user,
                salesforce_external_ids=salesforce_external_ids,
                salesforce_record_group_external_ids=salesforce_rg_external_ids,
                api_version=api_version,
                semaphore=semaphore,
                incomplete=unknown_users,
            )
            for user in sf_users
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_permission_tuples: List[Tuple[str, str, str, bool]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(
                    "Failed to collect permissions for user %s: %s",
                    sf_users[i].get("Email"), result,
                )
                unknown_users.add(sf_users[i]["Email"].lower())
            else:
                all_permission_tuples.extend(result)  # type: ignore[arg-type]

        self.logger.info("Collected %d permission tuples total.", len(all_permission_tuples))

        # 5. One transaction: rewrite the permission edges of every record and record group
        #    from this read. A user whose read was incomplete keeps their stored edges to
        #    them (unknown is not empty); everyone else's revocations apply now.
        #    SALESFORCE_ORG record groups carry no ORG-level permission edges, so wiping all
        #    PERMISSION edges to them is safe.
        delete_targets = [
            (rid, CollectionNames.RECORDS.value) for rid in ext_id_to_record_id.values()
        ] + [
            (rgid, CollectionNames.RECORD_GROUPS.value) for rgid in ext_id_to_rg_id.values()
        ] + [
            (pid, CollectionNames.RECORDS.value) for pid in product_record_ids
        ]
        if unknown_users:
            self.logger.warning(
                "Access of %d user(s) could not be read in full; they keep their stored "
                "permission edges until a later sync reads them in full.",
                len(unknown_users),
            )
            self._problems.note(ACCESS_KEPT)
        org_by_node_id: dict[str, str | None] = {
            (n.get("id") or n.get("_key")): n.get("orgId")
            for n in salesforce_records + salesforce_record_groups
        }
        narrowed_orgs: set[str | None] = set()
        record_edges: list[dict] = []
        rg_edges: list[dict] = []
        try:
            async def write_permissions(tx_store: TransactionStore) -> tuple[list[dict], list[dict]]:
                # Fresh each attempt: a re-run after a deadlock must not write them twice.
                record_edges: list[dict] = []
                rg_edges: list[dict] = []
                kept_edges = await self._stored_edges_of(
                    tx_store, unknown_users, email_to_user_id,
                    set(ext_id_to_record_id.values()), set(ext_id_to_rg_id.values()),
                )
                for node_id, collection in delete_targets:
                    if await tx_store.delete_edges_to(
                        node_id, collection, CollectionNames.PERMISSION.value
                    ):
                        narrowed_orgs.add(org_by_node_id.get(node_id))

                for email, ext_id, access_level, is_group in all_permission_tuples:
                    user_internal_id = email_to_user_id.get((email or "").lower())
                    if not user_internal_id:
                        self.logger.debug("Skipping permission: no DB user for email %s", email)
                        continue
                    try:
                        perm_type = PermissionType(access_level)
                    except ValueError:
                        self.logger.warning(
                            "Unknown access level %r for user %s, skipping", access_level, email
                        )
                        continue

                    permission = Permission(email=email, type=perm_type, entity_type=EntityType.USER)
                    if is_group:
                        target_id = ext_id_to_rg_id.get(ext_id)
                        if not target_id:
                            continue
                        rg_edges.append(permission.to_arango_permission(
                            from_id=user_internal_id,
                            from_collection=CollectionNames.USERS.value,
                            to_id=target_id,
                            to_collection=CollectionNames.RECORD_GROUPS.value,
                        ))
                    else:
                        target_id = ext_id_to_record_id.get(ext_id)
                        if not target_id:
                            continue
                        record_edges.append(permission.to_arango_permission(
                            from_id=user_internal_id,
                            from_collection=CollectionNames.USERS.value,
                            to_id=target_id,
                            to_collection=CollectionNames.RECORDS.value,
                        ))

                if kept_edges:
                    await tx_store.batch_create_edges(kept_edges, collection=CollectionNames.PERMISSION.value)
                if record_edges:
                    await tx_store.batch_create_edges(record_edges, collection=CollectionNames.PERMISSION.value)
                if rg_edges:
                    await tx_store.batch_create_edges(rg_edges, collection=CollectionNames.PERMISSION.value)
                return record_edges, rg_edges

            record_edges, rg_edges = await self.data_store_provider.execute_in_transaction(write_permissions)
        finally:
            # Even when the write raised: on Neo4j without explicit transactions each
            # delete commits on its own, and a rerun finds nothing left to delete.
            fallback_org = self.data_entities_processor.org_id or None
            for changed_org in {org or fallback_org for org in narrowed_orgs}:
                await notify_connector_permissions_changed(self.connector_id, changed_org)

        self.logger.info(
            "Permission sync complete: %d record edges and %d record group edges written.",
            len(record_edges), len(rg_edges),
        )

    @staticmethod
    async def _stored_edges_of(
        tx_store: TransactionStore,
        unknown_users: set,
        email_to_user_id: dict[str, str],
        record_ids: set[str],
        group_ids: set[str],
    ) -> list[dict]:
        """The stored permission edges of ``unknown_users`` to this connector's records and
        record groups, rebuilt to be written back after the rewrite."""
        kept: list[dict] = []
        for email in unknown_users:
            user_id = email_to_user_id.get(email)
            if not user_id:
                continue
            # A read that fails must abort the rewrite: an empty answer would drop their edges.
            edges = await tx_store.get_edges_from_node_with_target_name(
                f"{CollectionNames.USERS.value}/{user_id}", CollectionNames.PERMISSION.value,
                raise_on_error=True,
            )
            for edge in edges or []:
                target = str(edge.get("_to") or edge.get("to") or "")
                collection, _, key = target.partition("/")
                if collection == CollectionNames.RECORDS.value and key in record_ids:
                    pass
                elif collection == CollectionNames.RECORD_GROUPS.value and key in group_ids:
                    pass
                else:
                    continue
                try:
                    perm_type = PermissionType(edge.get("role"))
                except ValueError:
                    continue
                kept.append(Permission(email=email, type=perm_type, entity_type=EntityType.USER).to_arango_permission(
                    from_id=user_id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=key,
                    to_collection=collection,
                ))
        return kept

    async def _sync_product_readers(
        self, eligibility: SalesforceEligibility, sf_users: list[dict[str, str]],
    ) -> None:
        """Grant the product group to the users who may read Product2 in Salesforce: an
        exact list that replaces the stored one. When it cannot be read, the stored
        grants stay."""
        readers = await eligibility.product_reader_ids()
        if readers is None:
            return
        permissions = [
            Permission(email=u["Email"], type=PermissionType.READ, entity_type=EntityType.USER)
            for u in sf_users if u.get("Id") in readers
        ]
        await self.data_entities_processor.on_new_record_groups([(self._product_record_group(), permissions)])

    async def _sync_permissions_for_user(
        self,
        user: Dict[str, str],
        salesforce_external_ids: List[str],
        salesforce_record_group_external_ids: List[str],
        api_version: str,
        semaphore: asyncio.Semaphore,
        incomplete: set | None = None,
    ) -> list[tuple[str, str, str, bool]]:
        """
        Collect all permission tuples for one user via the Salesforce UserRecordAccess
        API.  Returns a list of (email, external_id, access_level, is_record_group)
        tuples.  No database writes are performed here — the caller batches them.
        """
        user_id = user.get("Id")
        user_email = user.get("Email")

        if not user_id or not user_email:
            return []

        collected: List[Tuple[str, str, str, bool]] = []
        unread: set = set()
        async with semaphore:
            async for rec_id, email, access_level in self._iter_record_access(
                user_id=user_id,
                user_email=user_email,
                record_ids=salesforce_external_ids,
                api_version=api_version,
                incomplete=unread,
            ):
                collected.append((email, rec_id, access_level, False))

            async for rg_id, email, access_level in self._iter_record_access(
                user_id=user_id,
                user_email=user_email,
                record_ids=salesforce_record_group_external_ids,
                api_version=api_version,
                incomplete=unread,
            ):
                collected.append((email, rg_id, access_level, True))

        if unread and incomplete is not None:
            incomplete.add(user_email.lower())
        return collected

    async def salesforce_record_group_permissions_sync(
        self,
        connector_id: str,
        record_group_external_id: str,
        users_email: str,
        access_level: PermissionType,
    ) -> None:
        """
        Ensure a user has at least the given permission level on a Salesforce org record group.
        """
        try:
            user = await self.data_entities_processor.get_user_by_email(users_email)
            if not user:
                self.logger.warning(f"User with email {users_email} not found in database")
                return

            record_group = await self.data_entities_processor.get_record_group_by_external_id(
                connector_id=connector_id,
                external_id=record_group_external_id,
            )
            if not record_group:
                self.logger.warning(
                    f"Record group with external id {record_group_external_id} not found in database"
                )
                return

            if record_group.group_type != RecordGroupType.SALESFORCE_ORG:
                return

            permission = Permission(
                email=users_email,
                type=access_level,
                entity_type=EntityType.USER,
            )
            await self.data_entities_processor.upsert_permission_edge(
                connector_id=self.connector_id,
                from_id=user.id,
                from_collection=CollectionNames.USERS.value,
                to_id=record_group.id,
                to_collection=CollectionNames.RECORD_GROUPS.value,
                permission=permission,
                upgrade_only=True,
            )
        except Exception as e:
            self.logger.error(f"Failed to sync record group permissions: {str(e)}", exc_info=True)
            raise

    async def salesforce_permissions_sync(
        self,
        connector_id: str,
        record_external_id: str,
        users_email: str,
        access_level: PermissionType,
    ) -> None:
        """
        Ensure a user has at least the given permission level on a record.
        If they already have that level or higher, return. Otherwise create or upgrade the permission edge.
        """
        try:
            user = await self.data_entities_processor.get_user_by_email(users_email)
            if not user:
                self.logger.warning(f"User with email {users_email} not found in database")
                return

            record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=connector_id,
                external_record_id=record_external_id,
            )
            if not record:
                self.logger.warning(f"Record with external id {record_external_id} not found in database")
                return

            permission = Permission(
                email=users_email,
                type=access_level,
                entity_type=EntityType.USER,
            )
            await self.data_entities_processor.upsert_permission_edge(
                connector_id=self.connector_id,
                from_id=user.id,
                from_collection=CollectionNames.USERS.value,
                to_id=record.id,
                to_collection=CollectionNames.RECORDS.value,
                permission=permission,
                upgrade_only=True,
            )
            self.logger.info(
                f"Created/updated permission for {users_email} on record {record_external_id} to {access_level.value}"
            )
        except Exception as e:
            self.logger.error(f"Failed to sync permissions: {str(e)}", exc_info=True)
            raise

    def _flatten_single_group_members(
        self,
        group_id: str,
        group_to_members: Dict[str, List[str]],
        visited: set,
        current_path: set,
    ) -> set:
        """
        Recursively flatten a single group's members to find all actual User IDs.
        Returns:
            Set of User IDs that belong to this group (directly or indirectly)
        """
        if group_id in current_path:
            self.logger.warning(f"Circular reference detected for group {group_id}")
            return set()
        if group_id in visited:
            return set()

        visited.add(group_id)
        current_path.add(group_id)

        user_ids = set()
        immediate_members = group_to_members.get(group_id, [])

        for member_id in immediate_members:
            if member_id in group_to_members:
                nested_user_ids = self._flatten_single_group_members(
                    member_id, group_to_members, visited, current_path.copy()
                )
                user_ids.update(nested_user_ids)
            else:
                user_ids.add(member_id)

        current_path.remove(group_id)
        return user_ids

    async def _flatten_group_members(self, api_version: str) -> Dict[str, Set[tuple]]:
        """
        Fetch all groups and their members, then flatten nested groups to actual User IDs with emails.
        Returns:
            Map of group ID -> set of dicts with {'user_id': str, 'email': str}
        """
        try:
            soql_query = """
                SELECT Id, Name, 
                    (SELECT Id, UserOrGroupId, UserOrGroup.Email 
                    FROM GroupMembers) 
                FROM Group 
                WHERE Type IN ('Regular', 'Queue')
            """
            group_to_members: Dict[str, List[str]] = {}
            user_id_to_email: Dict[str, str] = {}

            async for page in self._soql_query_paginated(api_version=api_version, q=soql_query):
                for group in page:
                    group_id = group.get("Id")
                    if not group_id:
                        continue
                    members = await self._complete_child_list(group.get("GroupMembers"))
                    records = (members or {}).get("records", [])
                    member_ids: List[str] = []
                    for member in records:
                        user_or_group_id = member.get("UserOrGroupId")
                        if not user_or_group_id:
                            continue
                        member_ids.append(user_or_group_id)
                        user_or_group = member.get("UserOrGroup")
                        if user_or_group and user_or_group.get("Email"):
                            user_id_to_email[user_or_group_id] = user_or_group.get("Email")
                    group_to_members[group_id] = member_ids

            if not group_to_members:
                self.logger.warning("No groups found")
                return {}
            
            # Flatten all groups to get actual user IDs
            flattened_result: Dict[str, Set[tuple]] = {}
            
            for group_id in group_to_members:
                visited: set = set()
                current_path: set = set()
                user_ids = self._flatten_single_group_members(
                    group_id, group_to_members, visited, current_path
                )
                
                # Convert user IDs to set of (user_id, email) tuples
                user_set = set()
                for user_id in user_ids:
                    email = user_id_to_email.get(user_id, "")
                    if email:  # Only include users with emails
                        user_set.add((user_id, email))
                
                flattened_result[group_id] = user_set
            
            return flattened_result
            
        except Exception as e:
            self.logger.error(f"Error flattening group members: {e}", exc_info=True)
            return {}
