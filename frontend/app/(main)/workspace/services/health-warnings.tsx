'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

const MAX_WARNINGS = 5;

/**
 * The API reports configuration that works but loses data or misbehaves as plain
 * strings next to a healthy status. Anything that is not a non-empty string is dropped.
 */
export function readHealthWarnings(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    .slice(0, MAX_WARNINGS);
}

export function HealthWarnings({ warnings }: { warnings: string[] }) {
  const { t } = useTranslation();
  if (warnings.length === 0) return null;

  return (
    <Flex
      role="alert"
      align="start"
      gap="3"
      data-testid="health-warnings"
      style={{
        backgroundColor: 'var(--amber-2)',
        border: '1px solid var(--amber-6)',
        borderRadius: 'var(--radius-2)',
        padding: '12px 14px',
        marginBottom: 20,
      }}
    >
      <Flex style={{ flexShrink: 0, marginTop: 2 }}>
        <MaterialIcon name="warning" size={16} color="var(--amber-11)" />
      </Flex>
      <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.services.healthWarnings.title')}
        </Text>
        {warnings.map((warning) => (
          <Text key={warning} size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
            {warning}
          </Text>
        ))}
      </Flex>
    </Flex>
  );
}
