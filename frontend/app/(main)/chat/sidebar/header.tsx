'use client';

import { Flex, IconButton, Tooltip } from '@radix-ui/themes';
import { Link } from '@/lib/navigation';
import { HEADER_ELEMENT_SIZE } from '@/app/components/sidebar';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { useUserStore } from '@/lib/store/user-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { toast } from '@/lib/store/toast-store';
import { PipesHubIcon } from '@/app/components/ui';

function SidebarPanelIcon({ size = 18, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ display: 'block', flexShrink: 0, color }}
      aria-hidden
    >
      <rect
        x="3"
        y="3"
        width="20"
        height="20"
        rx="2.5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
      />
      <path d="M9 3v18" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}

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
              <SidebarPanelIcon size={18} color="var(--gray-10)" />
            </IconButton>
          </Tooltip>
        )}
      </Flex>
    </Flex>
  );
}
