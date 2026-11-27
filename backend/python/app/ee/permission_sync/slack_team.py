"""Slack (workspace): reading who may see what from Slack.

Channel members as grants (the workspace role on a public channel, its guests, a private channel's or group
DM's members, a DM's other person), the workspace role with its non-guest members, and Slack user groups with
their members. The free connector keeps listing channels and syncing their messages, and the grant to the
person whose token it reads with (they see what the token reads); without a licence key a company-wide
connector reads none of this (the processor's licence gate).
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.constants.arangodb import Connectors
from app.connectors.sources.slack.common.rate_limits import Tier
from app.models.entities import AppRole, AppUser, AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType

PAGE_SIZE_MEMBERS           = 999


class SlackWorkspacePermissionsMixin:
    """The permission-reading half of ``SlackConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Slack. The processor then keeps every
        stored grant, group and role (``skips_permission_writes``), so the empty answers below only keep the run
        whole: None for a channel would mean its members could not be read."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _sync_workspace_roles(self) -> None:
        """
        Create/update a ``workspace_member`` AppRole containing all non-guest
        users (owners, admins, regular members).  This role is referenced in
        channel permissions so that public channels don't need per-user edges.
        """
        if await self._copies_no_source_access():
            return
        if getattr(self, "_users_read_failed", False):
            # Written from a short user list, the role would drop every member
            # not yet read, and with it their access to every public channel.
            self.logger.warning("ℹ️ User list was not read in full; workspace role left as it is")
            return
        non_guests = getattr(self, "_non_guest_app_users", [])
        if not non_guests:
            self.logger.info("ℹ️ No non-guest users to sync workspace role for")
            return

        role = AppRole(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=self.connector_id,
            source_role_id="workspace_member",
            name="Workspace Member",
            org_id=self.data_entities_processor.org_id,
        )
        await self.data_entities_processor.on_new_app_roles([(role, non_guests)])
        self.logger.info(
            f"✅ Workspace role synced — {len(non_guests)} non-guest members"
        )

    async def _sync_user_groups(self) -> None:
        """Sync Slack usergroups and their members."""
        if await self._copies_no_source_access():
            return
        self.logger.info("👥 Syncing user groups…")
        try:
            await self.rate_limiter.acquire(Tier.T2)
            ds = await self._fresh_datasource()
            resp = await ds.usergroups_list(include_users=True)

            if not resp or not resp.success:
                error = getattr(resp, "error", "no response")
                # A missing scope is how this workspace is set up, not a failed read:
                # its groups can never be read, so they are not kept as if they
                # might be. Any other error leaves them unread this time.
                if error != "missing_scope":
                    self._full_sync_read_failed = True
                self.logger.warning(
                    f"⚠️  Could not fetch usergroups (scope missing?): {error}"
                )
                return

            groups = resp.data.get("usergroups", [])
            if not groups:
                self.logger.info("No usergroups found.")
                return

            # Reuse the source_id → AppUser map built once in _build_user_id_caches.
            source_id_to_user: dict[str, AppUser] = self._source_id_to_app_user

            batch: list[tuple[AppUserGroup, list[AppUser]]] = []
            g_synced = m_synced = 0

            for gd in groups:
                ug = self._to_user_group(gd)
                if not ug:
                    continue

                member_ids: list[str] | None = gd.get("users", [])
                if not member_ids:
                    member_ids = await self._fetch_usergroup_members(gd.get("id", ""))
                if member_ids is None:
                    # Unread is not empty: re-sent with no members, the group would
                    # lose every one it has stored. Keep it as it is.
                    self._full_sync_read_failed = True
                    try:
                        await self.data_entities_processor.note_kept_user_group(
                            self.connector_id, ug.source_user_group_id,
                        )
                    except Exception as exc:
                        self.logger.warning(
                            f"Could not note user group {ug.source_user_group_id} as kept: {exc}"
                        )
                    continue

                members: list[AppUser] = []
                for uid in member_ids:
                    user = source_id_to_user.get(uid)
                    if user:
                        members.append(user)
                    else:
                        self.logger.debug(
                            f"User {uid} in group {gd.get('handle')} not in synced users"
                        )

                batch.append((ug, members))
                g_synced += 1
                m_synced += len(members)

            if batch:
                await self.data_entities_processor.on_new_user_groups(batch)

            self.logger.info(
                f"✅ User groups — groups: {g_synced}, memberships: {m_synced}"
            )

        except Exception as exc:
            self.logger.error(f"❌ User group sync failed: {exc}", exc_info=True)
            raise

    def _to_user_group(self, gd: dict[str, Any]) -> Optional[AppUserGroup]:
        gid  = gd.get("id")
        name = gd.get("name") or gd.get("handle")
        date_create = gd.get("date_create")
        date_update = gd.get("date_update")

        if date_create:
            date_create = int(float(date_create) * 1000)
        if date_update:
            date_update = int(float(date_update) * 1000)

        if not gid or not name:
            return None
        try:
            return AppUserGroup(
                app_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                source_user_group_id=gid,
                name=name,
                org_id=self.data_entities_processor.org_id,
                description=gd.get("description", ""),
                source_created_at=date_create,
                source_updated_at=date_update,
            )
        except Exception as exc:
            self.logger.error(f"_to_user_group failed for {gid}: {exc}")
            return None

    async def _fetch_usergroup_members(self, group_id: str) -> list[str] | None:
        """The group's member ids, or None when Slack did not return them."""
        try:
            await self.rate_limiter.acquire(Tier.T2)
            ds = await self._fresh_datasource()
            r  = await ds.usergroups_users_list(usergroup=group_id)
        except Exception as exc:
            self.logger.error(f"usergroups.users.list({group_id}) failed: {exc}", exc_info=True)
            return None
        if not r or not r.success:
            self.logger.warning(
                f"usergroups.users.list({group_id}) failed: {getattr(r, 'error', 'no response')}; "
                f"its stored members are kept"
            )
            return None
        return r.data.get("users", [])

    async def _channel_permissions(
        self,
        channel_data: dict[str, Any],
    ) -> list[Permission] | None:
        """
        Return the correct HAS_PERMISSION list for a single channel, the authenticator's
        grant included, or None when a private channel's members cannot be read (the
        grants stored by an earlier sync stay, and the authenticator is not added to them).
        A public channel whose members cannot be read gets the workspace role alone.

        Public channel  → workspace_member AppRole (efficient single edge)
        Private channel → explicit member list from conversations.members
        IM              → the remote user (channel_data['user'])
        MPIM            → explicit member list from conversations.members
        """
        if await self._copies_no_source_access():
            return []
        cid = channel_data.get("id", "")

        # Direct message — only the other user needs permission
        if channel_data.get("is_im"):
            remote_uid = channel_data.get("user", "")
            email = self.user_id_to_email_cache.get(remote_uid)
            if email:
                return self._with_authenticator([Permission(
                    email=email,
                    entity_type=EntityType.USER,
                    type=PermissionType.READ,
                )])
            return self._with_authenticator([])

        # Public channel — workspace members, plus guests who were added to this channel.
        if not channel_data.get("is_private") and not channel_data.get("is_mpim"):
            perms = [Permission(
                entity_type=EntityType.ROLE,
                external_id="workspace_member",
                type=PermissionType.READ,
            )]
            try:
                member_ids = await self._fetch_channel_members(cid, keep_if_unreadable=True)
            except RuntimeError as exc:
                # The channel still syncs for the workspace. Its guests get no grant this
                # time, and neither does the authenticator, who may not be in it.
                self._full_sync_read_failed = True
                self.logger.warning(f"Members of {channel_data.get('name')} not read: {exc}")
                return perms
            if member_ids is None:
                # The token cannot read this channel's members, so its holder may not be in it:
                # no authenticator grant, and the guests cannot be listed.
                return perms
            for uid in member_ids:
                if uid not in self._guest_user_ids:
                    continue
                email = self.user_id_to_email_cache.get(uid)
                if email:
                    perms.append(Permission(
                        email=email,
                        entity_type=EntityType.USER,
                        type=PermissionType.READ,
                    ))
            return self._with_authenticator(perms)

        # Private channel or group DM — resolve explicit member list
        member_ids = await self._fetch_channel_members(cid, keep_if_unreadable=True)
        if member_ids is None:
            self.logger.info(f"Members of channel {cid} cannot be read; its stored grants are kept")
            return None
        perms: list[Permission] = []
        for uid in member_ids:
            email = self.user_id_to_email_cache.get(uid)
            if email:
                perms.append(Permission(
                    email=email,
                    entity_type=EntityType.USER,
                    type=PermissionType.READ,
                ))
        return self._with_authenticator(perms)

    async def _fetch_channel_members(
        self, channel_id: str, *, keep_if_unreadable: bool = False
    ) -> list[str] | None:
        """
        Return all member Slack user IDs for a channel via conversations.members
        with full cursor-based pagination.

        An empty list when the first page answers channel_not_found,
        not_in_channel or missing_scope: the members cannot be read with this
        token. Those answers repeat on every sync, so they are not a failed read.
        Any failure after the first page, or any other failure, raises. With
        ``keep_if_unreadable`` those answers return None (unknown) instead of [].
        """
        members: list[str] = []
        cursor: Optional[str] = None

        while True:
            try:
                await self.rate_limiter.acquire(Tier.T4)
                ds   = await self._fresh_datasource()
                resp = await ds.conversations_members(
                    channel=channel_id,
                    limit=PAGE_SIZE_MEMBERS,
                    cursor=cursor,
                )

                if not resp or not resp.success:
                    error = getattr(resp, "error", "no response")
                    # On the first page these mean the members cannot be read at
                    # all, and will not be next time either. After it they cut
                    # the list short like any other error.
                    if cursor is None and error in ("channel_not_found", "not_in_channel", "missing_scope"):
                        self.logger.warning(f"Cannot read the members of {channel_id} ({error})")
                        return None if keep_if_unreadable else []
                    # Any other failure leaves the member list short. Returned,
                    # it would replace the channel's grants and lock out every
                    # member not yet read; raised, the channel is left as it is.
                    self.logger.warning(
                        f"conversations.members({channel_id}) failed: {error}"
                    )
                    raise RuntimeError(
                        f"conversations.members({channel_id}) failed part-way: {error}"
                    )

                members.extend(resp.data.get("members", []))
                cursor = resp.data.get("response_metadata", {}).get("next_cursor", "")
                if not cursor:
                    break

            except RuntimeError:
                raise
            except Exception as exc:
                self.logger.error(
                    f"_fetch_channel_members({channel_id}): {exc}", exc_info=True
                )
                raise RuntimeError(f"conversations.members({channel_id}) failed: {exc}") from exc

        return members
