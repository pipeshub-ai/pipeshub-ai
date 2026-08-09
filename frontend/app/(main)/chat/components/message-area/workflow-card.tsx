'use client';

import React, { useCallback, useState } from 'react';
import { Badge, Box, Button, Callout, Flex, Text } from '@radix-ui/themes';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  useWorkflowRunUpdates,
  type WorkflowRunUpdate,
} from '@/lib/hooks/use-workflow-run-updates';
import type { WorkflowCardPayload, WorkflowTriggerSummary } from '../../types';
import { WorkflowsApi } from '../../../workflows/api';
import {
  formatWorkflowTimestamp,
  triggerLabelKey,
  workflowCardStyle,
  workflowStatusColor,
} from './workflow-format';

export type { WorkflowCardPayload, WorkflowTriggerSummary };
export type { WorkflowRunUpdate };

function TriggerRow({ trigger, locale }: { trigger: WorkflowTriggerSummary; locale: string }) {
  const { t } = useTranslation();
  const nextRun = formatWorkflowTimestamp(trigger.nextRunAt, locale);
  return (
    <Flex align="center" gap="2" wrap="wrap">
      <Badge size="1" variant="soft" color="violet">
        {t(triggerLabelKey(trigger.kind))}
      </Badge>
      {nextRun && (
        <Text size="1" color="gray">
          {t('workflowCard.nextRun', { when: nextRun })}
        </Text>
      )}
    </Flex>
  );
}

export interface WorkflowCardProps {
  payload: WorkflowCardPayload;
}

/**
 * In-chat card rendered when a workflow is created/scheduled.
 * Subscribes to `workflowRunUpdate` socket events for live status.
 */
export function WorkflowCard({ payload }: WorkflowCardProps) {
  const { t, i18n } = useTranslation();
  const workflowId = payload.workflowId || payload.taskId || '';
  const triggers = payload.triggers ?? [];

  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [isDryRunning, setIsDryRunning] = useState(false);
  const [dryRunError, setDryRunError] = useState<string | null>(null);

  const handleDryRun = useCallback(async () => {
    if (!workflowId || isDryRunning) return;
    setIsDryRunning(true);
    setDryRunError(null);
    try {
      await WorkflowsApi.dryRun(workflowId);
    } catch (err) {
      setDryRunError(err instanceof Error ? err.message : 'Dry run failed');
    } finally {
      setIsDryRunning(false);
    }
  }, [workflowId, isDryRunning]);

  // Subscribe to live run updates. useWorkflowRunUpdates replaces the
  // deprecated window.__pipeshubWs pattern (that global was never assigned).
  const handleRunUpdate = useCallback(
    (update: WorkflowRunUpdate) => {
      if (update.workflowId === workflowId) {
        setRunStatus(update.status);
      }
    },
    [workflowId],
  );
  useWorkflowRunUpdates(handleRunUpdate);

  return (
    <Box style={{ ...workflowCardStyle, marginTop: 'var(--space-3)' }}>
      <Flex direction="column" gap="2">
        <Flex align="center" gap="2" justify="between">
          <Flex align="center" gap="2">
            <MaterialIcon name="account_tree" size={16} color="var(--slate-11)" />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {triggers.length === 1
                ? t('workflowCard.scheduledSingular')
                : triggers.length > 1
                  ? t('workflowCard.scheduledPlural', { count: triggers.length })
                  : t('workflowCard.created')}
            </Text>
          </Flex>
          {runStatus && (
            <Badge size="1" variant="soft" color={workflowStatusColor(runStatus)}>
              {t(`workflowRun.status.${runStatus}`, runStatus)}
            </Badge>
          )}
        </Flex>

        {payload.title && (
          <Text size="2" color="gray">
            {payload.title}
          </Text>
        )}

        {triggers.length > 0 && (
          <Flex direction="column" gap="1">
            {triggers.map((trigger) => (
              <TriggerRow key={trigger.triggerId} trigger={trigger} locale={i18n.language} />
            ))}
          </Flex>
        )}

        {(payload.executionKind || (payload.connectorIds && payload.connectorIds.length > 0) || (payload.toolNames && payload.toolNames.length > 0)) && (
          <Flex align="center" gap="1" wrap="wrap">
            {payload.executionKind === 'code' ? (
              <Badge size="1" variant="outline" color="iris">
                <MaterialIcon name="code" size={11} />
                {t('workflowCard.codeWorkflow', 'Code')}
              </Badge>
            ) : (
              <Badge size="1" variant="outline" color="amber">
                <MaterialIcon name="smart_toy" size={11} />
                {t('workflowCard.agentMode', 'Agent mode')}
              </Badge>
            )}
            {payload.connectorIds && payload.connectorIds.length > 0 && (
              <Badge size="1" variant="outline" color="cyan">
                <MaterialIcon name="hub" size={11} />
                {t('workflowCard.connectors', '{{count}} connector', { count: payload.connectorIds.length })}
              </Badge>
            )}
            {payload.toolNames && payload.toolNames.length > 0 && (
              <Badge size="1" variant="outline" color="jade">
                <MaterialIcon name="build" size={11} />
                {t('workflowCard.tools', '{{count}} tool', { count: payload.toolNames.length })}
              </Badge>
            )}
            {payload.collectionIds && payload.collectionIds.length > 0 && (
              <Badge size="1" variant="outline" color="amber">
                <MaterialIcon name="library_books" size={11} />
                {t('workflowCard.collections', '{{count}} KB', { count: payload.collectionIds.length })}
              </Badge>
            )}
          </Flex>
        )}

        {payload.codegenNote && (
          <Callout.Root color="amber" size="1" variant="surface">
            <Callout.Icon>
              <MaterialIcon name="warning" size={14} />
            </Callout.Icon>
            <Callout.Text>{payload.codegenNote}</Callout.Text>
          </Callout.Root>
        )}

        {workflowId && (
          <Flex align="center" gap="1" wrap="wrap">
            <Button
              variant="ghost"
              size="1"
              color="gray"
              onClick={handleDryRun}
              disabled={isDryRunning}
            >
              <MaterialIcon name={isDryRunning ? 'hourglass_empty' : 'science'} size={14} />
              {isDryRunning ? t('workflowCard.dryRunning', 'Running…') : t('workflowCard.dryRun', 'Dry Run')}
            </Button>
            <Button asChild variant="ghost" size="1" color="gray">
              <Link href={`/workflows?workflowId=${encodeURIComponent(workflowId)}`}>
                {t('workflowCard.viewInDashboard')}
              </Link>
            </Button>
            <Button asChild variant="ghost" size="1" color="gray">
              <Link href={`/workflows?workflowId=${encodeURIComponent(workflowId)}&edit=true`}>
                {t('workflowCard.edit', 'Edit')}
              </Link>
            </Button>
          </Flex>
        )}
        {dryRunError && (
          <Text size="1" style={{ color: 'var(--red-9)' }}>
            {dryRunError}
          </Text>
        )}
      </Flex>
    </Box>
  );
}
