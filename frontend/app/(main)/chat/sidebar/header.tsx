'use client';

import { Flex, IconButton, Tooltip } from '@radix-ui/themes';
import Link from 'next/link';
import { HEADER_ELEMENT_SIZE } from '@/app/components/sidebar';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore } from '@/lib/store/user-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { toast } from '@/lib/store/toast-store';
import { PipesHubIcon } from '@/app/components/ui';

/**
 * Sidebar header — logo, user avatar, and a desktop collapse button.
 * When the sidebar is collapsed the header is not visible (sidebar is 0-wide),
 * so we only need to handle the expanded state here.
 */
export function ChatSidebarHeader() {
  const profile = useUserStore((s) => s.profile);
  const setNavCollapsed = useSidebarWidthStore((s) => s.setNavCollapsed);
  const isMobile = useIsMobile();

  const avatar = (
    <UserAvatar
      fullName={profile?.fullName}
      firstName={profile?.firstName}
      lastName={profile?.lastName}
      email={profile?.email}
      src={profile?.avatarUrl}
      size={HEADER_ELEMENT_SIZE}
      radius="small"
    />
  );

  return (
    <Flex align="center" justify="between" gap="2" style={{ height: '100%', padding: 'var(--space-4)' }}>
      <PipesHubIcon size={HEADER_ELEMENT_SIZE} color="var(--accent-11)" />
      <Flex align="center" gap="2">
        {isMobile ? (
          <IconButton
            variant="ghost"
            color="gray"
            aria-label="Open profile"
            onClick={() => {
              toast.info('Coming soon', {
                description: 'Profile page on mobile is coming soon.',
              });
            }}
            style={{ margin: 0, padding: 0, lineHeight: 0, cursor: 'pointer' }}
          >
            {avatar}
          </IconButton>
        ) : (
          <Link href="/workspace/profile/" aria-label="Open profile" style={{ textDecoration: 'none', lineHeight: 0 }}>
            {avatar}
          </Link>
        )}
        {!isMobile && (
          <Tooltip content="Collapse sidebar" side="right">
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              aria-label="Collapse sidebar"
              onClick={() => setNavCollapsed(true)}
              style={{ margin: 0, cursor: 'pointer' }}
            >
              <MaterialIcon name="keyboard_tab" size={18} color="var(--gray-10)" style={{ transform: 'scaleX(-1)' }} />
            </IconButton>
          </Tooltip>
        )}
      </Flex>
    </Flex>
  );
}
