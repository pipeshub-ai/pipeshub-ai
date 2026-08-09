'use client';

import React, { useState } from 'react';
import { Badge, Box, Button, Callout, Flex, Text } from '@radix-ui/themes';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useWorkflowRunUpdates } from '@/lib/hooks/use-workflow-run-updates';
import type { RunResultCardPayload } from '../../types';
import { WorkflowRunAnswerPrompt } from './workflow-run-answer-prompt';
import {
  TERMINAL_RUN_STATUSES,
  formatWorkflowTimestamp,
  triggerLabelKey,
  workflowCardStyle,
  workflowStatusColor,
} from './workflow-format';

export interface WorkflowRunHeaderProps {
  payload: RunResultCardPayload;
}

/**
 * Header strip for an assistant message produced by a workflow run. It takes
 * the place the user query occupies on a normal turn — a workflow run has no
 * user turn of its own — while the run's markdown output renders below it
 * through the same `AnswerContent`/tabs/actions path as any other answer.
 *
 * A persisted run arrives already terminal. A dry run instead renders
 * immediately as `pending`, so the strip follows the run over the
 * notification socket until it reaches a terminal state; otherwise it would
 * sit at "Dry run started" forever.
 */
export function WorkflowRunHeader({ payload }: WorkflowRunHeaderProps) {
  const { t, i18n } = useTranslation();
  const [live, setLive] = useState<Partial<RunResultCardPayload> | null>(null);

  useWorkflowRunUpdates((update) => {
    if (!payload.runId || update.runId !== payload.runId) return;
    setLive({
      status: update.status,
      outputSummary: update.outputSummary,
      completedAt: TERMINAL_RUN_STATUSES.has(update.status) ? new Date().toISOString() : undefined,
    });
  });

  const merged: RunResultCardPayload = live ? { ...payload, ...live } : payload;
  const isSuccess = merged.status === 'succeeded';
  const isFailed =
    merged.status !== 'succeeded' && TERMINAL_RUN_STATUSES.has(merged.status ?? '');
  const isAwaitingInput = merged.status === 'awaiting_input';
  const isDryRun = merged.isDryRun ?? false;
  const workflowId = merged.workflowId;

  const iconName = isSuccess
    ? 'check_circle'
    : isFailed
      ? 'error'
      : isAwaitingInput
        ? 'help'
        : 'schedule';
  const iconColor = isSuccess
    ? 'var(--green-9)'
    : isFailed
      ? 'var(--red-9)'
      : isAwaitingInput
        ? 'var(--amber-9)'
        : 'var(--slate-9)';

  const completedAt = formatWorkflowTimestamp(merged.completedAt, i18n.language);

  return (
    <Box>
      <Flex align="center" gap="2" wrap="wrap" style={workflowCardStyle}>
        <MaterialIcon name={iconName} size={16} color={iconColor} />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {merged.workflowName || t('workflowRun.title', 'Workflow run')}
        </Text>

        <Badge size="1" variant="soft" color={workflowStatusColor(merged.status)}>
          {t(`workflowRun.status.${merged.status}`, merged.status)}
        </Badge>

        {isDryRun && (
          <Badge size="1" variant="outline" color="gray">
            {t('workflowRun.dryRun', 'Dry run')}
          </Badge>
        )}

        {merged.triggerKind && (
          <Badge size="1" variant="soft" color="gray">
            {t(triggerLabelKey(merged.triggerKind))}
          </Badge>
        )}

        {completedAt && (
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('workflowRun.completedAt', { when: completedAt })}
          </Text>
        )}

        {workflowId && (
          <Flex align="center" gap="1" style={{ marginLeft: 'auto' }}>
            <Button asChild variant="ghost" size="1" color="gray">
              <Link href={`/workflows?workflowId=${encodeURIComponent(workflowId)}`}>
                {t('workflowRun.viewWorkflow', 'View workflow')}
              </Link>
            </Button>
            <Button asChild variant="ghost" size="1" color="gray">
              <Link href={`/workflows?workflowId=${encodeURIComponent(workflowId)}&edit=true`}>
                {t('workflowRun.edit', 'Edit')}
              </Link>
            </Button>
          </Flex>
        )}
      </Flex>

      {isAwaitingInput && (
        <WorkflowRunAnswerPrompt
          workflowId={merged.workflowId}
          runId={merged.runId}
          question={merged.outputSummary}
          suspensionKind={merged.suspensionKind}
          onAnswered={(status) => setLive((prev) => ({ ...prev, status }))}
        />
      )}

      {/* The failure reason is not answer content, so it stays out of the
          markdown body and renders as a callout under the strip. */}
      {isFailed && merged.error && (
        <Callout.Root color="red" size="1" variant="surface" mt="2">
          <Callout.Icon>
            <MaterialIcon name="error" size={14} />
          </Callout.Icon>
          <Callout.Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {merged.error}
          </Callout.Text>
        </Callout.Root>
      )}
    </Box>
  );
}
