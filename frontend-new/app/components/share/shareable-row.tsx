'use client';

import React from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AvatarCell } from '@/app/(main)/workspace/components/avatar-cell';
import type { ShareRole } from './types';
import { RoleDropdownMenu } from './role-dropdown-menu';

interface ShareableRowProps {
  type: 'team' | 'member';
  name: string;
  subtitle?: string;
  avatarUrl?: string;
  /** Whether this row is selected (for suggestion rows) */
  isSelected?: boolean;
  /** Whether this is the entity owner */
  isOwner?: boolean;
  /** Current role (for existing members) */
  role?: ShareRole;
  /** Whether to show the role dropdown */
  showRoleDropdown?: boolean;
  /** When provided, suppresses role options in the dropdown (e.g. for chat) */
  noRolesInfo?: { title: string; description: string };
  /** Whether to show a radio button for selection */
  showRadio?: boolean;
  /** Whether to show "Invite to Pipeshub" link */
  showInvite?: boolean;
  /** Callback when the row is toggled (selection) */
  onToggle?: () => void;
  /** Callback when role changes */
  onRoleChange?: (newRole: ShareRole) => void;
  /** Callback for invite action */
  onInvite?: () => void;
  /** Callback for remove action */
  onRemove?: () => void;
}

export function ShareableRow({
  type,
  name,
  subtitle,
  avatarUrl,
  isSelected = false,
  isOwner = false,
  role,
  showRoleDropdown = false,
  showRadio = false,
  showInvite = false,
  noRolesInfo,
  onToggle,
  onRoleChange,
  onInvite,
  onRemove,
}: ShareableRowProps) {
  return (
    <Flex
      align="center"
      gap="3"
      style={{
        padding: '6px 0',
        minHeight: '40px',
        cursor: showRadio ? 'pointer' : 'default',
      }}
      onClick={showRadio ? onToggle : undefined}
    >
      {/* Avatar / Team icon + Name + subtitle */}
      {type === 'team' ? (
        <>
          <Flex
            align="center"
            justify="center"
            style={{
              width: 24,
              height: 24,
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--slate-3)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="group" size={18} color="var(--slate-11)" />
          </Flex>
          <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
            <Text
              size="2"
              weight="medium"
              style={{
                color: 'var(--slate-12)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {name}
            </Text>
            {subtitle && (
              <Text
                size="1"
                style={{
                  color: 'var(--slate-9)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {subtitle}
              </Text>
            )}
          </Flex>
        </>
      ) : (
        <Box style={{ flex: 1, minWidth: 0 }}>
          <AvatarCell
            name={name}
            email={subtitle}
            avatarSize={32}
            profilePicture={avatarUrl}
          />
        </Box>
      )}

      {/* Right side: radio / role dropdown / owner label / invite */}
      {showRadio && (
        <Box
          style={{
            width: 16,
            height: 16,
            borderRadius: '50%',
            border: `2px solid ${isSelected ? 'var(--accent-9)' : 'var(--slate-7)'}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          {isSelected && (
            <Box
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                backgroundColor: 'var(--accent-9)',
              }}
            />
          )}
        </Box>
      )}

      {showRoleDropdown && role && !isOwner && (
        <RoleDropdownMenu
          role={role}
          onRoleChange={onRoleChange}
          onRemove={onRemove}
          isTeam={type === 'team'}
          noRolesInfo={noRolesInfo}
        />
      )}

      {isOwner && (
        <Text size="2" style={{ color: 'var(--slate-9)', flexShrink: 0 }}>
          Owner
        </Text>
      )}

      {showInvite && (
        <Text
          size="2"
          style={{
            color: 'var(--accent-9)',
            cursor: 'pointer',
            flexShrink: 0,
          }}
          onClick={(e) => {
            e.stopPropagation();
            onInvite?.();
          }}
        >
          Invite to Pipeshub
        </Text>
      )}
    </Flex>
  );
}
