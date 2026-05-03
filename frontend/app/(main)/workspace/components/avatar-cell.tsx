'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { UserAvatar } from '@/app/components/ui/user-avatar';
export type { UserAvatarProps, UserAvatarProfile } from '@/app/components/ui/user-avatar';
export { UserAvatar } from '@/app/components/ui/user-avatar';

/**
 * AvatarCell — avatar + name (+ optional email)
 */

export interface AvatarCellProps {
  /** Full name to display */
  name: string;
  /** Email to display below name (optional) */
  email?: string;
  /** Avatar size in px (default: 32) */
  avatarSize?: number;
  /** Whether this is the current user (appends "(You)") */
  isSelf?: boolean;
  /** Optional profile picture URL */
  profilePicture?: string;
}

/**
 * AvatarCell — renders avatar with initials + name (+ optional email).
 *
 * Used in:
 * - Users table: User column (avatar + name + email)
 * - Groups/Teams: Name column (avatar + name), Created by column (avatar + name + email)
 */
export function AvatarCell({
  name,
  email,
  avatarSize = 32,
  isSelf = false,
  profilePicture,
}: AvatarCellProps) {
  const displayName = isSelf ? `${name} (You)` : name;

  return (
    <Flex align="center" gap="3">
      <UserAvatar fullName={name} email={email} src={profilePicture} size={avatarSize} radius='small'/>
      <Flex direction="column" style={{ overflow: 'hidden', minWidth: 0 }}>
        <Text
          size="2"
          weight="medium"
          style={{
            color: 'var(--slate-12)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {displayName}
        </Text>
        {email && (
          <Text
            size="1"
            style={{
              color: 'var(--slate-11)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {email}
          </Text>
        )}
      </Flex>
    </Flex>
  );
}
