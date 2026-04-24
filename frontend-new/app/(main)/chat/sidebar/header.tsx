'use client';

import { Flex, Box } from '@radix-ui/themes';
import Link from 'next/link';
import { HEADER_ELEMENT_SIZE } from '@/app/components/sidebar';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { useUserStore } from '@/lib/store/user-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { toast } from '@/lib/store/toast-store';
import { PipesHubIcon } from '@/app/components/ui';

/**
 * Sidebar header — logo and user avatar.
 * Avatar resolves to the user's profile picture if set, otherwise shows initials.
 *
 * On mobile, tapping the avatar shows a "coming soon" toast instead of
 * navigating — the profile page is not yet adapted for mobile.
 */
export function ChatSidebarHeader() {
  const profile = useUserStore((s) => s.profile);
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
      {isMobile ? (
        <Box
          role="button"
          tabIndex={0}
          aria-label="Open profile"
          onClick={() => {
            toast.info('Coming soon', {
              description: 'Profile page on mobile is coming soon.',
            });
          }}
          style={{ cursor: 'pointer', lineHeight: 0 }}
        >
          {avatar}
        </Box>
      ) : (
        <Link href="/workspace/profile/" aria-label="Open profile" style={{ textDecoration: 'none', lineHeight: 0 }}>
          {avatar}
        </Link>
      )}
    </Flex>
  );
}
