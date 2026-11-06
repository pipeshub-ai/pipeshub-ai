'use client';

import { useState, useMemo } from 'react';
import { Box, Button, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { HEADER_ELEMENT_SIZE, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { SIDEBAR_FOCUS_CLASS } from '@/app/components/sidebar/sidebar-focus-style';
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
  const { data: orgData } = useOrg();
  const { data: logoData } = useOrgLogo();
  const orgLogoUrl = logoData ?? null;
  const { t } = useTranslation();

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

  const orgName = org?.shortName || org?.registeredName;
  const triggerLabel = orgName
    ? t('workspaceMenu.triggerLabel', { name: orgName })
    : t('workspaceMenu.triggerLabelNoName');

  return (
    <Box style={{ padding: 'var(--space-2)', position: 'relative' }}>
      <WorkspaceMenu
        isOpen={isMenuOpen}
        onOpen={() => setIsMenuOpen(true)}
        onClose={() => setIsMenuOpen(false)}
        org={org}
        trigger={
          // A real button, so the menu opens from the keyboard and focus comes back to it.
          <Button
            type="button"
            variant="soft"
            color="gray"
            size="3"
            className={SIDEBAR_FOCUS_CLASS}
            aria-label={triggerLabel}
            style={{
              width: '100%',
              justifyContent: 'flex-start',
              gap: 'var(--space-2)',
              paddingInline: 'var(--space-2)',
              borderRadius: 'var(--radius-1)',
              boxShadow: 'inset 0 0 0 1px var(--border-subtle)',
            }}
          >
            {/* Org avatar — logo if available, else initial */}
            <UserAvatar
              fullName={orgName}
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
                minWidth: 0,
                textAlign: 'left',
                color: 'var(--text-primary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {orgName}
            </Text>

            <MaterialIcon
              name={isMenuOpen ? 'expand_less' : 'expand_more'}
              size={ICON_SIZE_DEFAULT}
              color="var(--text-secondary)"
              style={{ userSelect: 'none', flexShrink: 0 }}
            />
          </Button>
        }
      />
    </Box>
  );
}
