'use client';

import React from 'react';
import { Flex, Switch, Text } from '@radix-ui/themes';
import type { ShareSettingDescriptor } from './types';

interface ShareSettingsProps {
  descriptors: ShareSettingDescriptor[];
  onToggle: (id: string, value: boolean) => void;
  /** Id of a setting whose update is in flight. */
  pendingId?: string | null;
}

export function ShareSettings({ descriptors, onToggle, pendingId }: ShareSettingsProps) {
  if (descriptors.length === 0) return null;
  return (
    <Flex direction="column" gap="3" style={{ padding: '12px 0' }}>
      {descriptors.map((d) => {
        const labelId = `share-setting-${d.id}`;
        return (
          <Flex key={d.id} align="center" justify="between" gap="3">
            <Flex direction="column" style={{ minWidth: 0 }}>
              <Text id={labelId} size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {d.label}
              </Text>
              {d.description && (
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {d.description}
                </Text>
              )}
            </Flex>
            <Switch
              aria-labelledby={labelId}
              checked={d.value}
              disabled={pendingId === d.id}
              onCheckedChange={(value) => onToggle(d.id, value)}
            />
          </Flex>
        );
      })}
    </Flex>
  );
}
