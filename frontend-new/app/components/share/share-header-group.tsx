'use client';

import React from 'react';
import { Flex } from '@radix-ui/themes';
import { SharedAvatarStack } from './shared-avatar-stack';
import { ShareButton } from './share-button';
import type { SharedAvatarMember } from './types';

interface ShareHeaderGroupProps {
  /** Shared members to show in avatar stack */
  members: SharedAvatarMember[];
  /** Called when Share button is clicked */
  onShareClick: () => void;
  /** Max visible avatars (default: 3) */
  maxVisibleAvatars?: number;
}

export function ShareHeaderGroup({
  members,
  onShareClick,
  maxVisibleAvatars = 3,
}: ShareHeaderGroupProps) {
  return (
    <Flex align="center" gap="4">
      {members.length > 0 && (
        <SharedAvatarStack
          members={members}
          maxVisible={maxVisibleAvatars}
          onClick={onShareClick}
        />
      )}
      <ShareButton onClick={onShareClick} />
    </Flex>
  );
}
