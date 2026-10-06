'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Avatar, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { SharedAvatarMember } from './types';

function getInitials(name: string): string {
  if (!name) return '?';
  const parts = name.trim().split(/[\s._-]+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

interface SharedAvatarStackProps {
  /** List of shared user details */
  members: SharedAvatarMember[];
  /** Max avatars shown before the "+N" badge (default: 3) */
  maxVisible?: number;
  /** Avatar size in px (default: 24) */
  size?: number;
  /** Called when the stack is clicked */
  onClick?: () => void;
}



export function SharedAvatarStack({
  members,
  maxVisible = 3,
  size = 24,
  onClick,
}: SharedAvatarStackProps) {
  const { t } = useTranslation();
  if (members.length === 0) return null;

  const visible = members.slice(0, maxVisible);
  const hidden = members.slice(maxVisible);
  // The ring is the page background, so neighbours read as separate circles instead of one run of initials.
  const ring = '2px solid var(--color-background)';
  const overlap = -Math.round(size / 4);

  return (
    <Flex
      align="center"
      role="group"
      aria-label={t('shareSidebar.sharedWith', { names: members.map((m) => m.name).join(', ') })}
      onClick={onClick}
      style={{ cursor: onClick ? 'pointer' : 'default' }}
    >
      {visible.map((member, index) => (
        <Tooltip key={member.id} content={member.name}>
          <Flex
            align="center"
            justify="center"
            role="img"
            aria-label={member.name}
            data-testid="shared-avatar"
            style={{
              width: size,
              height: size,
              marginLeft: index > 0 ? overlap : 0,
              position: 'relative',
              zIndex: maxVisible - index,
              borderRadius: '50%',
              overflow: 'hidden',
              flexShrink: 0,
              boxSizing: 'content-box',
              border: ring,
              backgroundColor: member.type === 'team' ? 'var(--accent-6)' : undefined,
            }}
          >
            {member.type === 'team' ? (
              <MaterialIcon name="group" size={size * 0.65} color="var(--slate-12)" />
            ) : (
              <Avatar
                size="1"
                variant="solid"
                radius="full"
                fallback={
                  <Text style={{ fontSize: size * 0.42, lineHeight: 1, letterSpacing: '-0.02em' }}>
                    {getInitials(member.name)}
                  </Text>
                }
                src={member.avatarUrl}
                style={{
                  width: size,
                  height: size,
                  minWidth: size,
                  minHeight: size,
                }}
              />
            )}
          </Flex>
        </Tooltip>
      ))}

      {hidden.length > 0 && (
        <Tooltip content={hidden.map((m) => m.name).join(', ')}>
          <Flex
            align="center"
            justify="center"
            role="img"
            aria-label={t('shareSidebar.andMore', { count: hidden.length })}
            data-testid="shared-avatar-overflow"
            style={{
              width: size,
              height: size,
              boxSizing: 'content-box',
              border: ring,
              borderRadius: '50%',
              backgroundColor: 'var(--accent-9)',
              marginLeft: overlap,
              position: 'relative',
              zIndex: 0,
            }}
          >
            <Text size="1" weight="bold" style={{ color: 'var(--accent-contrast)', fontSize: 10 }}>
              +{hidden.length}
            </Text>
          </Flex>
        </Tooltip>
      )}
    </Flex>
  );
}
