'use client';

import React from 'react';
import { Flex, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
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
  /** When set, the stack gets a visible count and a "can see this" hint; omitted outside collaborative chats. */
  peopleCount?: number;
}

export function ShareHeaderGroup({
  members,
  onShareClick,
  maxVisibleAvatars = 3,
  peopleCount,
}: ShareHeaderGroupProps) {
  const { t } = useTranslation();
  const stack = members.length > 0 && (
    <SharedAvatarStack members={members} maxVisible={maxVisibleAvatars} onClick={onShareClick} />
  );
  const names = members.map((m) => m.name).join(', ');
  const summary = peopleCount !== undefined ? t('chat.collab.header.peopleCanSee', { count: peopleCount }) : '';

  return (
    <Flex align="center" gap={{ initial: '2', sm: '4' }} style={{ flexShrink: 0 }}>
      {stack && peopleCount !== undefined ? (
        <Tooltip content={names ? `${summary}: ${names}` : summary}>
          <Flex align="center" gap="1" data-testid="header-people-summary" aria-label={summary} role="group">
            {stack}
            <MaterialIcon name="group" size={14} color="var(--slate-11)" />
            <Text size="1" style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}>
              {peopleCount}
            </Text>
          </Flex>
        </Tooltip>
      ) : (
        stack
      )}
      <ShareButton onClick={onShareClick} />
    </Flex>
  );
}
