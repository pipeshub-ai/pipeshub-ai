'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import type { JwtUser } from '@/lib/utils/auth-helpers';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface UserBadgeProps {
  user: JwtUser;
}

const ELLIPSIS: React.CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * UserBadge — displays an initials circle + name + email.
 *
 * Used in the change-password form (top-right of the panel).
 */
export default function UserBadge({ user }: UserBadgeProps) {
  if (!user.email && !user.name) return null;

  return (
    <Flex align="center" gap="2" style={{ minWidth: 0, maxWidth: '100%' }}>
      {user.initials && (
        <Flex
          align="center"
          justify="center"
          style={{
            width: '32px',
            height: '32px',
            borderRadius: '50%',
            backgroundColor: 'var(--accent-a9)',
            flexShrink: 0,
          }}
        >
          <Text size="1" weight="bold" style={{ color: 'white' }}>
            {user.initials}
          </Text>
        </Flex>
      )}
      <Flex direction="column" style={{ minWidth: 0 }}>
        {user.name && (
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', ...ELLIPSIS }}>
            {user.name}
          </Text>
        )}
        {user.email && (
          <Text size="1" style={{ color: 'var(--gray-10)', ...ELLIPSIS }}>
            {user.email}
          </Text>
        )}
      </Flex>
    </Flex>
  );
}
