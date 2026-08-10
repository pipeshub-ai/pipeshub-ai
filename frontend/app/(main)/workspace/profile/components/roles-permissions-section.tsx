'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { TextField } from '@radix-ui/themes';
import { SettingsSection } from './settings-section';
import { SettingsRow } from './settings-row';

export interface RolesPermissionsSectionProps {
  role: string;
}

export function RolesPermissionsSection({ role }: RolesPermissionsSectionProps) {
  const { t } = useTranslation();
  return (
    <SettingsSection title={t('workspace.profile.rolesPermissions.title')}>
      <SettingsRow
        label={t('workspace.profile.rolesPermissions.role')}
        description={t('workspace.profile.rolesPermissions.roleDescription')}
      >
        <TextField.Root
          value={role}
          readOnly
          style={{ color: 'var(--gray-10)' }}
        />
      </SettingsRow>
    </SettingsSection>
  );
}
