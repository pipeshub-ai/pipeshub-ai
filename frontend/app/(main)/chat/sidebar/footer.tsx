'use client';

import { useState, useEffect, useRef } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { HEADER_ELEMENT_SIZE, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { WorkspaceMenu } from '@/app/components/workspace-menu';
import type { OrgInfo } from '@/app/components/workspace-menu';
import { fetchOrgWithLogo } from '@/chat/api';

/**
 * Sidebar footer — organisation selector button + popup menu.
 *
 * Fetches organisation details on mount (not on popup open) and
 * caches the result for the session.
 */
export function ChatSidebarFooter() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [org, setOrg] = useState<OrgInfo | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);

  // Fetch org + logo on mount — uses a module-level singleton so the
  // network request fires exactly once regardless of StrictMode double-mount
  // or component remounts across navigation.
  useEffect(() => {
    fetchOrgWithLogo().then(({ org: data, logoUrl }) => {
      if (!data) return;
      setOrg({
        _id: data._id,
        registeredName: data.registeredName,
        shortName: data.shortName,
        domain: data.domain,
        accountType: data.accountType,
        logoUrl,
      });
    });
  }, []);

  const orgLogoUrl = org?.logoUrl ?? null;

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
          height: 40,
          padding: 'var(--space-2)',
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
