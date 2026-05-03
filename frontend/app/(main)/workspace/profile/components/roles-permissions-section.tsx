'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, TextField, Badge } from '@radix-ui/themes';
import { SettingsSection } from './settings-section';
import { SettingsRow } from './settings-row';

// ========================================
// Types
// ========================================

export interface RolesPermissionsSectionProps {
  role: string;
  groups: Array<{ name: string; type: string }>;
}

// ========================================
// Component
// ========================================

export function RolesPermissionsSection({ role, groups }: RolesPermissionsSectionProps) {
  const { t } = useTranslation();
  return (
    <SettingsSection title={t('workspace.profile.rolesPermissions.title')}>

      {/* Role */}
      <SettingsRow label={t('workspace.profile.rolesPermissions.role')} description={t('workspace.profile.rolesPermissions.roleDescription')}>
        <TextField.Root
          value={role}
          readOnly
          style={{ color: 'var(--gray-10)' }}
        />
      </SettingsRow>

      {/* Groups */}
      <SettingsRow label={t('workspace.profile.rolesPermissions.groups')} description={t('workspace.profile.rolesPermissions.groupsDescription')}>
        {groups.length === 0 ? (
          <Text size="2" style={{ color: 'var(--gray-9)' }}>
            {t('workspace.profile.rolesPermissions.noGroups')}
          </Text>
        ) : (
          <Flex wrap="wrap" gap="2">
            {groups.map((g) => (
              <Badge key={g.name} color="gray" variant="soft" radius="medium">
                {g.name}
              </Badge>
            ))}
          </Flex>
        )}
      </SettingsRow>

    </SettingsSection>
  );
}
