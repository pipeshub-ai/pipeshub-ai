# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""BookStack: reading who may see what from BookStack.

Each book's, chapter's and page's content permission rows, the roles with their members and
permissions, and the role sync (``bookstack_access`` works out an item's grants from them). The
free connector keeps listing and syncing content and calls these methods; without a licence key a
company-wide connector reads none of this (the processor's licence gate).
"""

import asyncio
from typing import Any, Dict, List, Optional

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.bookstack.reads import (
    ReadOutcome,
    _answered,
    _combined,
    _gone,
    read_failure,
)
from app.ee.permission_sync.bookstack_access import ItemAccess, ItemRows, item_access
from app.models.entities import AppRole, AppUser


class BookStackPermissionsMixin:
    """The permission-reading half of ``BookStackConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from BookStack. The processor then keeps
        every stored grant, role and membership whatever is handed to it (``skips_permission_writes``), so the
        answers below only keep the run whole: an item's access is empty rather than None, which would count it
        as unreadable and leave it unsynced, and a role read counts as done rather than failed, which would fail
        the run."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def list_roles_with_details(self) -> Dict[int, Dict]:
        """
        Gets a list of all roles with their detailed information, including
        permissions and assigned users.

        Returns:
            A dictionary where the key is the role_id and the value is the
            dictionary containing the role's full details.
        """
        self.logger.info("Fetching all roles with details...")
        self._roles_read = ReadOutcome.INTERRUPTED

        # First, get the basic list of all roles
        basic_roles, outcome = await self._read_listing(self.data_source.list_roles, "roles")
        if not basic_roles:
            self._roles_read = outcome
            if outcome is ReadOutcome.COMPLETE:
                self.logger.info("No roles found in BookStack.")
            return {}

        # Create a list of concurrent tasks to get the details for each role
        tasks = [self.data_source.get_role(role['id']) for role in basic_roles]

        # Execute all tasks in parallel and wait for them to complete
        detail_responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Process the results into the final dictionary
        roles_details_map = {}
        for role, response in zip(basic_roles, detail_responses):
            role_id = role['id']
            if _answered(response):
                roles_details_map[role_id] = response.data
            elif _gone(response):
                self.logger.info(f"Role ID {role_id} is no longer in BookStack")
            else:
                outcome = _combined(outcome, read_failure(response))
                self.logger.error(f"Failed to get details for role ID {role_id}: {getattr(response, 'error', response)}")

        self._roles_read = outcome
        self.logger.info(f"Successfully fetched details for {len(roles_details_map)} roles.")
        return roles_details_map

    async def _load_roles_for_access(self) -> bool:
        """Every role with its members and permissions: access cannot be worked out from part of them."""
        if await self._copies_no_source_access():
            return True
        self._roles_details = await self.list_roles_with_details()
        if self._roles_read is not ReadOutcome.COMPLETE or not self._roles_details:
            self._health.note_status("the roles", None if self._roles_read is ReadOutcome.INTERRUPTED else 403,
                                     "user-roles-manage")
            self._kept_unnoted_access = True
            self.logger.warning("BookStack roles were not all read, so content keeps its stored access this sync")
            return False
        return True

    #-------------------------------Access-----------------------------------#
    async def _rows(self, kind: str, item_id: int) -> Optional[ItemRows]:
        """An item's content permission rows, read once per run; None when BookStack did not give them."""
        key = f"{kind}/{item_id}"
        if key in self._rows_cache:
            return self._rows_cache[key]
        response = await self.data_source.get_content_permissions(content_type=kind, content_id=item_id)
        rows = ItemRows.from_api(response.data) if response.success else None
        if rows is None:
            if response.status_code == HttpStatusCode.NOT_FOUND.value:
                return None
            self._health.note_status(f"the permissions of {key}", response.status_code, "restrictions-manage-all",
                                     response.error)
            return None
        self._rows_cache[key] = rows
        return rows

    async def _access(self, kind: str, item: Dict) -> Optional[ItemAccess]:
        """Who may view one book, chapter or page, as permissions; None when it cannot be worked out."""
        if await self._copies_no_source_access():
            return ItemAccess()
        item_id = int(item["id"])
        chain_refs = [(kind, item_id)]
        if kind == "page" and item.get("chapter_id"):
            chain_refs.append(("chapter", int(item["chapter_id"])))
        if kind in ("page", "chapter") and item.get("book_id"):
            chain_refs.append(("book", int(item["book_id"])))
        chain = []
        for ref in chain_refs:
            rows = await self._rows(*ref)
            if rows is None:
                return None
            chain.append(rows)
        owner = item.get("owned_by")
        owner_id = int(owner["id"]) if isinstance(owner, dict) else (int(owner) if owner else chain[0].owner_id)
        access = item_access(kind, chain, self._roles_details, self._user_emails, owner_id)
        target = self._dependent_pages if kind == "page" else self._dependent_groups
        key: Any = item_id if kind == "page" else f"{kind}/{item_id}"
        if access.membership_dependent:
            target.add(key)
        else:
            target.discard(key)
        return access

    async def _handle_user_upsert_event(self, user_upsert_events: List[Dict], app_users: List[AppUser]) -> bool:
        """Re-sync the roles of each user named in the events.

        Returns False when a user's roles could not be read in full; that user's
        role memberships are then left as stored.
        """
        self.logger.info("Processing user upsert events... !")
        done = True

        # First handle the user update itself (updates the user record in arango)
        await self._handle_user_create_event(user_upsert_events, app_users)
        if await self._copies_no_source_access():
            return True

        # Process each user upsert event
        for event in user_upsert_events:
            user_id, name = self._parse_id_and_name_from_event(event)

            if user_id is None:
                self.logger.warning(f"Could not parse user ID from event: {event}")
                continue

            self.logger.info(f"Processing role updates for user: {name} (ID: {user_id})")

            try:
                # Get the updated user details to find their roles
                user_response = await self.data_source.get_user(user_id)

                if not _answered(user_response):
                    self.logger.error(f"Failed to fetch details for user ID {user_id}: {user_response.error}")
                    if not _gone(user_response) and read_failure(user_response) is ReadOutcome.INTERRUPTED:
                        done = False
                    else:
                        # Gone, or refused for good: their roles can't be confirmed, so
                        # access through them is withdrawn rather than kept unchecked.
                        await self._withdraw_user_roles(user_id, name)
                    continue

                user_details = user_response.data
                roles = user_details.get('roles', [])

                user_email = user_details.get('email')
                if not user_email:
                    self.logger.warning(f"User {name} (ID: {user_id}) has no email, skipping role updates.")
                    continue

                # Read every role the user is in, with its members, before taking the
                # user out of any: the roles are written back whole, and one that
                # can't be read would otherwise lose this user until its next change.
                role_reads = []
                for role in roles:
                    role_id = role.get('id')
                    if role_id:
                        self.logger.info(f"Resyncing role '{role.get('display_name')}' (ID: {role_id}) due to user update")
                        role_reads.append(await self._read_role_with_members(role_id))
                    else:
                        self.logger.warning(f"Role '{role.get('display_name')}' has no ID, skipping resync")
                if any(outcome is ReadOutcome.INTERRUPTED for outcome, _ in role_reads):
                    self.logger.warning(
                        f"Could not read every role of user {name} (ID: {user_id}); their roles stay as stored this sync"
                    )
                    done = False
                    continue

                # Delete edges to groups for the user. A failed lookup raises rather
                # than answering None, which would count the change as applied.
                user = await self.data_entities_processor.get_user_by_email(user_email, raise_on_error=True)
                if not user:
                    self.logger.warning(f"User {name} (ID: {user_id}) not found in the database, skipping role updates.")
                    continue
                await self.data_entities_processor.delete_edges_between_collections(
                    user.id, CollectionNames.USERS.value, CollectionNames.PERMISSION.value, CollectionNames.ROLES.value,
                    connector_id=self.connector_id,
                )

                if not roles:
                    self.logger.info(f"User {name} (ID: {user_id}) has no roles assigned.")
                    continue

                # Resync each role the user is part of
                self.logger.info(f"User {name} is part of {len(roles)} role(s). Resyncing...")
                read_roles = [role for _, role in role_reads if role]
                if read_roles:
                    await self.data_entities_processor.on_new_app_roles(read_roles)

            except Exception as e:
                self.logger.error(f"Error processing user upsert for user ID {user_id}: {e}", exc_info=True)
                # Its role edges may already be deleted; the next sync applies the event again.
                done = False

        self.logger.info("✅ Finished processing user upsert events and role resyncs")
        return done

    async def _withdraw_user_roles(self, user_id: int, name: Optional[str]) -> None:
        """Take a BookStack user out of every role they are stored in.

        Raises when the stored user can't be looked up or their role edges can't be
        deleted, so the caller reads the event again; a user never stored has nothing to withdraw.
        """
        if await self._copies_no_source_access():
            return
        user = await self.data_entities_processor.get_user_by_source_id(
            str(user_id), self.connector_id, raise_on_error=True
        )
        if not user:
            return
        self.logger.warning(f"Withdrawing the stored roles of user {name} (ID: {user_id})")
        await self.data_entities_processor.delete_edges_between_collections(
            user.id, CollectionNames.USERS.value, CollectionNames.PERMISSION.value, CollectionNames.ROLES.value,
            connector_id=self.connector_id,
        )

    async def _sync_user_roles_full(self) -> bool:
        """
        Fetches all roles with their detailed user assignments to build and
        upsert user groups with their member users (AppUser objects).

        Returns False when the roles or users could not be read in full, or this
        run's user sync didn't store every user: roles that can't be written whole
        are left as stored, since writing one replaces its members, and the next
        sync does this again.
        """
        if await self._copies_no_source_access():
            return True
        self.logger.info("Starting BookStack user group and permissions sync...")
        self._roles_rebuilt = True
        # This read decides what is owed: a role refused earlier in this sync and read now is not.
        self._refused_roles_kept = False
        if not self._users_stored:
            # The processor attaches a member only through their stored user, so a role
            # written now would leave out everyone the user sync didn't store.
            self.logger.warning("⚠️ BookStack users were not all stored, so role members stay as stored this sync")
            return False

        # 1. Fetch details for all roles, including the users assigned to each role.
        # A role that wasn't read is not in the batch, so it keeps its stored members.
        # Interrupted, the role checkpoint stays and the next sync reads it again.
        # Refused (401, 403), it keeps its members and the checkpoint moves on: the
        # refusal repeats every sync, and holding the checkpoint for it would replay
        # the audit log on every sync and lose role deletes made meanwhile.
        all_roles_with_details = await self._fetch_all_roles_with_details()
        roles_complete = self._roles_read is not ReadOutcome.INTERRUPTED
        if self._roles_read is ReadOutcome.REFUSED:
            self.logger.error(
                "❌ BookStack refused part of the role list or a role's details; roles not read keep their "
                "stored members until they can be read"
            )
            if not await self._keep_refused_roles():
                return False
        elif not roles_complete:
            self.logger.warning("⚠️ BookStack roles were not all read; the next sync reads them again")
        if not all_roles_with_details:
            if self._roles_read is ReadOutcome.COMPLETE:
                self.logger.warning("No roles found in BookStack. Aborting user group sync.")
            return roles_complete
        self.logger.info(f"Found {len(all_roles_with_details)} total roles with details.")

        # 2. Fetch all user details to get complete user information
        all_users_with_details = await self._fetch_all_users_with_details()
        if self._users_read is ReadOutcome.INTERRUPTED:
            self.logger.warning(
                "⚠️ BookStack users were not read in full, so role members stay as stored this sync"
            )
            return False

        # Create a map for quick user lookup by ID
        user_details_map = {
            user.get("id"): user for user in all_users_with_details
        }

        # 3. Build the final batch for the data processor.
        roles_batch = []
        for role in all_roles_with_details:
            # Create the AppRole object for the role.
            app_role = AppRole(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_role_id=str(role.get("id")),
                name=role.get("display_name"),
                org_id=self.data_entities_processor.org_id
            )

            # Build list of AppUser objects for users in this role
            app_users = []
            for user in role.get("users", []):
                user_id = user.get("id")
                user_details = user_details_map.get(user_id)

                if not user_details:
                    self.logger.warning(f"No details found for user ID {user_id} in role {role.get('display_name')}")
                    continue

                # Create AppUser object from the user details
                app_user = AppUser(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_user_id=str(user_id),
                    email=user_details.get("email", ""),
                    full_name=user_details.get("name", ""),
                    org_id=self.data_entities_processor.org_id,
                    is_active=True,  # Assuming users in roles are active
                    title=user_details.get("title") if user_details.get("title") else None,
                    source_created_at=self._parse_timestamp(user_details.get("created_at")) if user_details.get("created_at") else None,
                    source_updated_at=self._parse_timestamp(user_details.get("updated_at")) if user_details.get("updated_at") else None
                )
                app_users.append(app_user)

            roles_batch.append((app_role, app_users))

        # 4. Send the complete batch to the processor.
        if roles_batch:
            self.logger.info(f"Submitting {len(roles_batch)} roles with their users...")
            await self.data_entities_processor.on_new_app_roles(roles_batch)
            self.logger.info("✅ Successfully processed app roles and permissions.")
        else:
            self.logger.info("No app roles were processed.")
        if self._roles_read is ReadOutcome.COMPLETE:
            return await self._remove_roles_bookstack_no_longer_has(all_roles_with_details)
        return roles_complete

    async def _remove_roles_bookstack_no_longer_has(self, roles: list[dict]) -> bool:
        """Remove the stored roles a complete role read did not return.

        Writing roles replaces the members of the roles written only, so a role deleted
        in BookStack while its delete event was missed (the role list refused, or a full
        sync's sync points cleared) would keep its members. False when the stored roles
        can't be listed, so the next sync reads every role again.
        """
        present = {str(role.get("id")) for role in roles}
        try:
            stored = await self.data_entities_processor.get_nodes_by_filters(
                CollectionNames.ROLES.value,
                {"connectorId": self.connector_id},
                return_fields=["externalRoleId"],
                raise_on_error=True,
            )
        except Exception as e:
            self.logger.error(f"Could not list the stored BookStack roles: {e}")
            self._kept_unnoted_access = True
            return False
        gone = sorted({str(node.get("externalRoleId")) for node in stored if node.get("externalRoleId")} - present)
        for role_id in gone:
            self.logger.info(f"Role ID {role_id} is no longer in BookStack; removing it")
            await self.data_entities_processor.on_app_role_deleted(
                external_role_id=role_id, connector_id=self.connector_id
            )
        return True

    async def _keep_refused_roles(self) -> bool:
        """Keep the stored members of the roles BookStack refused to show.

        They are not in the role write, so their members stay. Each one is noted as
        seen by a full sync in progress, so the unlink after it leaves them alone. A
        refused page of the role list names no roles, so that sync reports that it
        did not read everything instead. False when the stored roles can't be read.
        """
        if self._role_list_refused:
            self._kept_unnoted_access = True
            self._refused_roles_kept = True
        for role_id in self._refused_role_ids:
            if not await self._keep_refused_role(role_id):
                return False
        return True

    async def _keep_refused_role(self, role_id: int) -> bool:
        """Keep one refused role as stored and note it as seen; False when that can't be read."""
        self._refused_roles_kept = True
        try:
            await self.data_entities_processor.has_stored_permissions(
                "role", self.connector_id, str(role_id), keep=True
            )
        except Exception as e:
            self.logger.error(f"Could not read the stored members of refused role ID {role_id}: {e}")
            self._kept_unnoted_access = True
            return False
        return True

    async def _fetch_all_roles_with_details(self) -> List[Dict]:
        """
        Helper to fetch all roles and then get their detailed profiles,
        including assigned users, concurrently.

        Whether every role was read is left in ``self._roles_read``.
        """
        # First, get the basic list of all roles to find their IDs.
        self.logger.info("Fetching basic list of all roles...")
        basic_roles = await self._fetch_all_roles()
        if not basic_roles:
            return []

        # Asynchronously fetch the full details for each role in parallel.
        self.logger.info(f"Fetching details for {len(basic_roles)} roles concurrently...")
        tasks = [self.data_source.get_role(role['id']) for role in basic_roles]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        detailed_roles = []
        for role, res in zip(basic_roles, responses):
            if _answered(res):
                detailed_roles.append(res.data)
            elif _gone(res):
                self.logger.info(f"Role ID {role['id']} is no longer in BookStack")
            else:
                outcome = read_failure(res)
                if outcome is ReadOutcome.REFUSED:
                    self._refused_role_ids.append(role['id'])
                self._roles_read = _combined(self._roles_read, outcome)
                self.logger.error(f"Failed to get details for role ID {role['id']}: {getattr(res, 'error', res)}")

        self.logger.info(f"Successfully fetched details for {len(detailed_roles)} roles.")
        return detailed_roles

    async def _fetch_all_roles(self) -> List[Dict]:
        """Helper to fetch all roles with pagination; whether it got to the end is left in ``self._roles_read``."""
        self.logger.info("Fetching all roles from BookStack...")
        all_roles, self._roles_read = await self._read_listing(self.data_source.list_roles, "roles")
        self._refused_role_ids = []
        self._role_list_refused = self._roles_read is ReadOutcome.REFUSED
        return all_roles

    async def _fetch_all_users_with_details(self) -> List[Dict]:
        """Helper to fetch all users and then get detailed profiles concurrently.

        Whether every user was read is left in ``self._users_read``.
        """
        self.logger.info("Fetching all user IDs...")
        self._users_read = ReadOutcome.INTERRUPTED
        all_users_summary, outcome = await self._read_listing(self.data_source.list_users, "users")

        if not all_users_summary:
            self._users_read = outcome
            return []

        self.logger.info(f"Fetching details for {len(all_users_summary)} users concurrently...")
        detailed_users, details_outcome = await self._user_details([user['id'] for user in all_users_summary])

        self._users_read = _combined(outcome, details_outcome)
        if self._users_read is ReadOutcome.REFUSED:
            self.logger.error(
                f"❌ BookStack refused part of the user list; roles are written from the {len(detailed_users)} "
                "users read, so access through them is withdrawn from the rest until they can be read"
            )
        self.logger.info(f"Successfully fetched details for {len(detailed_users)} users.")
        return detailed_users

    async def _read_role_with_members(
        self, role_id: int
    ) -> tuple[ReadOutcome, tuple[AppRole, list[AppUser]] | None]:
        """A role and its members as BookStack has them now; (COMPLETE, None) when the role is gone."""
        role_details_response = await self.data_source.get_role(role_id)
        if not _answered(role_details_response):
            if _gone(role_details_response):
                return ReadOutcome.COMPLETE, None
            self.logger.warning(
                f"Could not fetch details for role ID {role_id}: {getattr(role_details_response, 'error', None)}"
            )
            return read_failure(role_details_response), None

        role_details = role_details_response.data

        # Create the AppRole object
        app_role = AppRole(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_role_id=str(role_id),
            name=role_details.get("display_name"),
            org_id=self.data_entities_processor.org_id
        )

        # Fetch detailed user information for each user in the role, in parallel
        user_ids = [user.get("id") for user in role_details.get("users", []) if user.get("id")]
        users_details, outcome = await self._user_details(user_ids)

        app_users = [
            AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=str(user_details.get("id")),
                email=user_details.get("email", ""),
                full_name=user_details.get("name", ""),
                org_id=self.data_entities_processor.org_id,
                is_active=True,  # Users in roles are assumed to be active
                title=user_details.get("title") if user_details.get("title") else None,
                source_created_at=self._parse_timestamp(user_details.get("created_at")) if user_details.get("created_at") else None,
                source_updated_at=self._parse_timestamp(user_details.get("updated_at")) if user_details.get("updated_at") else None
            )
            for user_details in users_details
        ]
        return outcome, (app_role, app_users)

    async def _handle_role_delete_event(self, role_id: int) -> None:
        """
        Handles a 'role_delete' audit log event by parsing the role ID
        and calling the data processor to delete the corresponding user group.
        """
        if await self._copies_no_source_access():
            return
        self.logger.info(f"Processing deletion for user group (External ID: {role_id})")

        # Call the data processor to delete the group from the database
        await self.data_entities_processor.on_app_role_deleted(
            external_role_id=str(role_id),
            connector_id=self.connector_id
        )
