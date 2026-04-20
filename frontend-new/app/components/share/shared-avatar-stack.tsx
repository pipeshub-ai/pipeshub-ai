'use client';

import React from 'react';
import { Flex, Text, Avatar } from '@radix-ui/themes';
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
  /** Max avatars shown before "+N" badge (default: 3) */
  maxVisible?: number;
  /** Avatar size in px (default: 28) */
  size?: number;
  /** Called when the stack is clicked */
  onClick?: () => void;
}



export function SharedAvatarStack({
  members,
  maxVisible = 3,
  size = 22,
  onClick,
}: SharedAvatarStackProps) {
  if (members.length === 0) return null;

  const visible = members.slice(0, maxVisible);
  const remaining = members.length - maxVisible;

  return (
    <Flex
      align="center"
      onClick={onClick}
      style={{ cursor: onClick ? 'pointer' : 'default' }}
    >
      {visible.map((member, index) => (
        <Flex
          key={member.id}
          align="center"
          justify="center"
          style={{
            width: size,
            height: size,
            marginLeft: index > 0 ? -8 : 0,
            position: 'relative',
            zIndex: maxVisible - index,
            borderRadius: '50%',
            overflow: 'hidden',
            flexShrink: 0,
            border: '1px solid var(--slate-5)',
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
                <Text style={{ fontSize: size * 0.5, lineHeight: 1, letterSpacing: '-0.02em' }}>
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
      ))}

      {remaining > 0 && (
        <Flex
          align="center"
          justify="center"
          style={{
            width: size,
            height: size,
            borderRadius: '50%',
            backgroundColor: 'var(--accent-3)',
            marginLeft: -2,
            position: 'relative',
            zIndex: 0,
          }}
        >
          <Text size="1" weight="bold" style={{ color: 'var(--accent-contrast)', fontSize: 10 }}>
            +{remaining}
          </Text>
        </Flex>
      )}
    </Flex>
  );
}
