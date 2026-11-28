# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Zoom: reading who may see a meeting from Zoom.

The host, alternative hosts, participants (Zoom's report API) and invitees of each meeting, as grants
on its record. The free connector keeps syncing meetings and their transcripts and calls this; without
a licence key a company-wide connector reads none of it (the processor's licence gate).

The grant on each Zoom user's own record group (``ZoomConnector._build_record_group``) is not read
from Zoom: it says whose meetings the group holds, so it stays in the free connector.
"""

from typing import Optional

from app.connectors.sources.zoom.models import (
    ZoomMeetingDetail,
    ZoomMeetingSettings,
    ZoomParticipant,
)
from app.models.permission import EntityType, Permission, PermissionType


class ZoomPermissionsMixin:
    """The permission-reading half of ``ZoomConnector``. Without a licence key a meeting's grants are empty: the
    processor then keeps what is stored (``skips_permission_writes``), and the participants are not read for
    them. Empty, not an error: the meeting record is still written."""

    async def _build_meeting_permissions(
        self,
        meeting_detail: Optional[ZoomMeetingDetail],
        encoded_uuid: str,
        host_email: str,
    ) -> tuple[list[Permission], bool]:
        """Build permission list from host, alt-hosts, participants, and invitees.

        Flow:
        1. Host email → OWNER
        2. Alt-hosts (settings.alternative_hosts, semicolon-separated) → READ each
        3. Participants (report API) → READ each
        4. Invitees (settings.meeting_invitees[].email) → READ each
        5. All deduplicated via seen_emails set

        Returns (permissions, whether the participants were read in full).
        """
        if await self.data_entities_processor.skips_permission_writes(self.connector_id):
            # Nothing read: added to, never replacing, the stored grants (which the processor keeps anyway).
            return [], False
        perms: list[Permission] = []
        seen_emails: set[str] = set()

        # 1 — host → OWNER
        if host_email:
            perms.append(Permission(
                email=host_email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER,
            ))
            seen_emails.add(host_email)

        settings = meeting_detail.settings if meeting_detail else ZoomMeetingSettings()

        # 2 — alt-hosts → READ
        for raw_alt in settings.alternative_hosts.split(";"):
            alt_email = raw_alt.strip().lower()
            if alt_email and alt_email not in seen_emails:
                perms.append(Permission(
                    email=alt_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
                seen_emails.add(alt_email)

        # 3 — participants → READ
        # A read that fails part way still grants the participants it returned;
        # the caller then adds these grants instead of replacing the stored ones.
        participants_read = True
        read_so_far: list[ZoomParticipant] = []
        try:
            participants = await self._list_meeting_participants(
                encoded_uuid, raise_on_error=True, read_so_far=read_so_far
            )
        except Exception as exc:
            participants = read_so_far
            participants_read = False
            self.logger.warning("Zoom: failed to fetch participants: %s", exc)
        self._add_participant_permissions(perms, participants, seen_emails)

        # 4 — invitees → READ
        for inv in settings.meeting_invitees:
            inv_email = inv.email.strip().lower()
            if inv_email and inv_email not in seen_emails:
                perms.append(Permission(
                    email=inv_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
                seen_emails.add(inv_email)

        return perms, participants_read

    @staticmethod
    def _add_participant_permissions(
        perms: list[Permission],
        participants: list[ZoomParticipant],
        seen_emails: set[str],
    ) -> None:
        """Append READ permission per participant email."""
        for p in participants:
            p_email = p.user_email.strip().lower()
            if p_email and p_email not in seen_emails:
                perms.append(Permission(
                    email=p_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
                seen_emails.add(p_email)
