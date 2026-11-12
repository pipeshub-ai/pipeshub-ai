'use client';

import { useState, useRef, useMemo } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { HEADER_ELEMENT_SIZE, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { WorkspaceMenu } from '@/config';
import type { OrgInfo } from '@/app/components/workspace-menu';
import { useOrg, useOrgLogo } from '@/app/(main)/workspace/general/use-org';

/**
 * Sidebar footer — organisation selector button + popup menu.
 *
 * Reads the org and its logo from the session cache (fetched once, refreshed
 * by the General settings writes).
 */
export function ChatSidebarFooter() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const { data: orgData } = useOrg();
  const { data: logoData } = useOrgLogo();
  const orgLogoUrl = logoData ?? null;

  const org = useMemo<OrgInfo | null>(
    () =>
      orgData
        ? {
            _id: orgData._id,
            registeredName: orgData.registeredName,
            shortName: orgData.shortName,
            domain: orgData.domain,
            accountType: orgData.accountType,
            logoUrl: orgLogoUrl,
          }
        : null,
    [orgData, orgLogoUrl],
  );

  return (
    <Box style={{ padding: 'var(--space-2)', position: 'relative' }}>
      <WorkspaceMenu
        isOpen={isMenuOpen}
        onClose={() => setIsMenuOpen(false)}
        org={org}
        triggerRef={triggerRef}
      />

      <Flex
        ref={triggerRef}
        align="center"
        gap="2"
        onClick={() => setIsMenuOpen((prev) => !prev)}
        style={{
          width: '100%',
          // Avatar gets the same inset on every side; the chevron keeps the wider right edge
          padding: 'var(--space-1) var(--space-2) var(--space-1) var(--space-1)',
          background: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-1)',
          cursor: 'pointer',
        }}
      >
        {/* Org avatar — logo if available, else initial */}
        <UserAvatar
          fullName={org?.shortName || org?.registeredName}
          src={orgLogoUrl}
          size={HEADER_ELEMENT_SIZE}
          radius="small"
        />

        {/* Org name */}
        <Text
          size="2"
          weight="medium"
          style={{
            flex: 1,
            textAlign: 'left',
            color: 'var(--emerald-12)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {org?.shortName || org?.registeredName}
        </Text>

        <MaterialIcon
          name={isMenuOpen ? 'keyboard_arrow_down' : 'keyboard_arrow_up'}
          size={ICON_SIZE_DEFAULT}
          color="var(--slate-11)"
          style={{ userSelect: 'none' }}
        />
      </Flex>
    </Box>
  );
}
