'use client';

import React from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { BatchRow } from '../hooks/use-batch-add-models';

interface BatchProgressListProps {
  rows: BatchRow[];
  onRetry: (modelId: string) => void;
  onRemove: (modelId: string) => void;
  disabled?: boolean;
}

export function BatchProgressList({ rows, onRetry, onRemove, disabled = false }: BatchProgressListProps) {
  const { t } = useTranslation();
  if (rows.length === 0) return null;
  return (
    <Flex direction="column" gap="2" data-testid="ai-batch-progress">
      {rows.map((row) => (
        <Flex key={row.model} align="center" justify="between" gap="3">
          <Flex direction="column" style={{ minWidth: 0 }}>
            <Text size="2" weight="medium">{row.model}</Text>
            <Text size="1" style={{ color: row.status === 'failed' ? 'var(--red-11)' : 'var(--gray-11)' }}>
              {t(`workspace.aiModels.batchStatus.${row.status}`)}
              {row.message ? ` — ${row.message}` : ''}
            </Text>
          </Flex>
          <Flex gap="2">
            {row.status === 'failed' ? (
              <Button type="button" size="1" variant="soft" disabled={disabled} onClick={() => onRetry(row.model)}>
                {t('workspace.aiModels.batchRetry')}
              </Button>
            ) : null}
            {row.status === 'healthy' ? null : (
              <Button type="button" size="1" variant="ghost" color="gray" disabled={disabled} onClick={() => onRemove(row.model)}>
                {t('workspace.aiModels.batchRemove')}
              </Button>
            )}
          </Flex>
        </Flex>
      ))}
    </Flex>
  );
}
