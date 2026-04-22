'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { SchemaFormField } from '../schema-form-field';
import type { SyncCustomField } from '../../types';

// ========================================
// CustomSyncFieldsSection
// ========================================

export function CustomSyncFieldsSection({
  fields,
  values,
  errors,
  onChange,
}: {
  fields: SyncCustomField[];
  values: Record<string, unknown>;
  errors: Record<string, string>;
  onChange: (key: string, value: unknown) => void;
}) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      gap="4"
      style={{
        padding: 16,
        backgroundColor: 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.connectors.configTab.additionalSettings')}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.connectors.configTab.additionalSettingsDescription')}
        </Text>
      </Flex>

      {fields.map((field) => (
        <SchemaFormField
          key={field.name}
          field={field}
          value={values[field.name]}
          onChange={onChange}
          error={errors[field.name]}
        />
      ))}
    </Flex>
  );
}
