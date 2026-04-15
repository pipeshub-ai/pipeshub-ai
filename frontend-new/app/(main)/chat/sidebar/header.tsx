'use client';

import { Flex } from '@radix-ui/themes';
import { HEADER_ELEMENT_SIZE } from '@/app/components/sidebar';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { useUserStore } from '@/lib/store/user-store';
import { PipesHubIcon } from '@/app/components/ui';

/**
 * Sidebar header — logo and user avatar.
 * Avatar resolves to the user's profile picture if set, otherwise shows initials.
 */
export function ChatSidebarHeader() {
  const profile = useUserStore((s) => s.profile);

  return (
    <Flex align="center" justify="between" gap="2" style={{ height: '100%', padding: 'var(--space-4)' }}>
      <PipesHubIcon size={HEADER_ELEMENT_SIZE} color="var(--accent-8)" />
      <UserAvatar
        fullName={profile?.fullName}
        firstName={profile?.firstName}
        lastName={profile?.lastName}
        email={profile?.email}
        src={profile?.avatarUrl}
        size={HEADER_ELEMENT_SIZE}
        radius="small"
      />
    </Flex>
  );
}
