'use client';

import React from 'react';
import { Badge, Box, Button, Flex, Text } from '@radix-ui/themes';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { WorkflowUpdatedPayload } from '../../types';
import { workflowCardStyle } from './workflow-format';

export interface WorkflowUpdatedCardProps {
  payload: WorkflowUpdatedPayload;
}

/**
 * In-chat card shown after `workflow_manage(action="update")` regenerates
 * workflow code. Provides a direct link to the detail view so the user
 * can inspect the diff and graph changes.
 */
export function WorkflowUpdatedCard({ payload }: WorkflowUpdatedCardProps) {
  const { t } = useTranslation();
  const workflowId = payload.workflowId ?? '';
  const regenerated = payload.regenerated !== false;

  return (
    <Box style={{ ...workflowCardStyle, marginTop: 'var(--space-3)' }}>
      <Flex direction="column" gap="2">
        <Flex align="center" gap="2" justify="between">
          <Flex align="center" gap="2">
            <MaterialIcon
              name={regenerated ? 'edit' : 'warning'}
              size={16}
              color={regenerated ? 'var(--slate-11)' : 'var(--amber-9)'}
            />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workflowUpdatedCard.title', 'Workflow Updated')}
            </Text>
          </Flex>
          <Badge size="1" variant="soft" color={regenerated ? 'gray' : 'amber'}>
            {regenerated
              ? t('workflowUpdatedCard.newVersion', 'New version')
              : t('workflowUpdatedCard.codeUnchanged', 'Code unchanged')}
          </Badge>
        </Flex>

        {payload.title && (
          <Text size="2" color="gray">
            {payload.title}
          </Text>
        )}

        {payload.changesSummary && (
          <Text
            size="2"
            style={{
              color: regenerated ? 'var(--slate-11)' : 'var(--amber-11)',
              lineHeight: 1.5,
            }}
          >
            {payload.changesSummary}
          </Text>
        )}

        {workflowId && (
          <Flex align="center" gap="1" wrap="wrap">
            <Button asChild variant="ghost" size="1" color="gray">
              <Link href={`/workflows?workflowId=${encodeURIComponent(workflowId)}`}>
                {t('workflowUpdatedCard.viewChanges', 'View Changes')}
              </Link>
            </Button>
            <Button asChild variant="ghost" size="1" color="gray">
              <Link href={`/workflows?workflowId=${encodeURIComponent(workflowId)}&edit=true`}>
                {t('workflowUpdatedCard.editMore', 'Edit More')}
              </Link>
            </Button>
          </Flex>
        )}
      </Flex>
    </Box>
  );
}
