'use client';

import React from 'react';
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
  return (
    <SettingsSection title="Roles & Permissions">

      {/* Role */}
      <SettingsRow label="Role" description="Your role on Pipeshub app">
        <TextField.Root
          value={role}
          readOnly
          style={{ color: 'var(--gray-10)' }}
        />
      </SettingsRow>

      {/* Groups */}
      <SettingsRow label="Groups" description="User groups you belong to">
        {groups.length === 0 ? (
          <Text size="2" style={{ color: 'var(--gray-9)' }}>
            No user groups found
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
