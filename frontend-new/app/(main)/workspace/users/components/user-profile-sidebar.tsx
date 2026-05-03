'use client';

import React from 'react';
import { Flex, Text, Avatar, Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { WorkspaceRightPanel } from '../../components';
import { useUsersStore } from '../store';

// ========================================
// Helpers
// ========================================

/** Format timestamp to "DD/MM/YYYY, HH:mm:ss" for profile display */
function formatDateTime(timestampMs?: number): string {
  if (!timestampMs) return '-';
  const date = new Date(timestampMs);
  if (isNaN(date.getTime())) return '-';

  const day = String(date.getDate()).padStart(2, '0');
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const year = date.getFullYear();
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');

  return `${day}/${month}/${year}, ${hours}:${minutes}:${seconds}`;
}

/** Extract initials from a full name */
function getInitials(name: string): string {
  if (!name) return '?';
  const parts = name.trim().split(/[\s._-]+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

// ========================================
// Profile Field Row
// ========================================

interface ProfileFieldProps {
  label: string;
  value: string;
  valueColor?: string;
}

function ProfileField({ label, value, valueColor }: ProfileFieldProps) {
  return (
    <Box
      style={{
        background: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-3) var(--space-4)',
      }}
    >
      <Flex direction="column" gap="1">
        <Text size="1" style={{ color: 'var(--slate-9)' }}>
          {label}
        </Text>
        <Text size="2" weight="medium" style={{ color: valueColor || 'var(--slate-12)' }}>
          {value}
        </Text>
      </Flex>
    </Box>
  );
}

// ========================================
// User Profile Sidebar
// ========================================

export function UserProfileSidebar() {
  const { t } = useTranslation();
  const currentUser = useAuthStore((s) => s.user);

  const { isProfilePanelOpen, profileUser, closeProfilePanel } = useUsersStore();

  if (!profileUser) return null;

  const isSelf = currentUser?.id === profileUser.id || currentUser?.email === profileUser.email;
  const displayName = profileUser.name || profileUser.email || '-';
  const nameWithSuffix = isSelf ? `${displayName} (You)` : displayName;

  const status = profileUser.hasLoggedIn ? 'Active' : 'Pending';

  // Map status to color for the profile display
  const statusColor =
    status === 'Active'
      ? 'var(--accent-11)'
      : status === 'Pending'
        ? 'var(--amber-11)'
        : 'var(--slate-11)';

  // Count of groups (excluding "everyone")
  const groupsCount = profileUser.groupCount ?? 0;

  return (
    <WorkspaceRightPanel
      open={isProfilePanelOpen}
      onOpenChange={(open) => {
        if (!open) closeProfilePanel();
      }}
      title={t('workspace.users.profile.title')}
      icon="person"
      hideFooter
    >
      <Flex direction="column" gap="2">
        {/* Name — own card with avatar */}
        <Box
          style={{
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3) var(--space-4)',
          }}
        >
          <Flex align="center" justify="between">
            <Flex direction="column" gap="1">
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                {t('workspace.users.profile.name')}
              </Text>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {nameWithSuffix}
              </Text>
            </Flex>
            <Avatar
              size="2"
              variant="soft"
              fallback={getInitials(displayName)}
              style={{ flexShrink: 0 }}
            />
          </Flex>
        </Box>

        <ProfileField
          label={t('workspace.users.profile.email')}
          value={profileUser.email || '-'}
        />
        <ProfileField
          label={t('workspace.users.profile.role')}
          value={profileUser.role || 'Member'}
        />
        <ProfileField
          label={t('workspace.users.profile.companyDesignation')}
          value="-"
        />
        <ProfileField
          label={t('workspace.users.profile.groupsCount')}
          value={String(groupsCount)}
        />
        <ProfileField
          label={t('workspace.users.profile.teamsCount')}
          value="-"
        />
        <ProfileField
          label={t('workspace.users.profile.status')}
          value={status}
          valueColor={statusColor}
        />
        <ProfileField
          label={t('workspace.users.profile.lastActive')}
          value={formatDateTime(profileUser.updatedAtTimestamp)}
        />
        <ProfileField
          label={t('workspace.users.profile.dateJoined')}
          value={formatDateTime(profileUser.createdAtTimestamp)}
        />
        <ProfileField
          label={t('workspace.users.profile.invitedBy')}
          value="-"
        />
      </Flex>
    </WorkspaceRightPanel>
  );
}
