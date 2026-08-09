'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Box, Card, Flex, IconButton, ScrollArea, Text, Tooltip } from '@radix-ui/themes';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkflowsApi } from '@/app/(main)/workflows/api';
import { useWorkflowRunUpdates } from '@/lib/hooks/use-workflow-run-updates';
import type { Workflow } from '@/app/(main)/workflows/types';
import type { WorkflowRunUpdate } from '@/lib/hooks/use-workflow-run-updates';

interface WorkflowPanelProps {
  conversationId: string;
  onClose: () => void;
}

function statusColor(s: string): 'green' | 'amber' | 'gray' | 'violet' {
  if (s === 'active') return 'green';
  if (s === 'paused') return 'amber';
  return 'gray';
}

function triggerLabel(kind: string): string {
  const map: Record<string, string> = {
    cron: 'Recurring',
    interval: 'Interval',
    one_time: 'One-Time',
    event: 'Event',
    webhook: 'Webhook',
  };
  return map[kind] ?? kind;
}

function WorkflowRow({
  workflow,
  runStatuses,
  onDryRun,
  dryRunLoading,
}: {
  workflow: Workflow;
  runStatuses: Record<string, string>;
  onDryRun: (id: string) => void;
  dryRunLoading: string | null;
}) {
  const { i18n } = useTranslation();
  const liveStatus = runStatuses[workflow.workflowId];
  const nextTrigger = workflow.triggers[0];
  const nextRunAt = nextTrigger?.nextRunAt
    ? (() => {
        const d = new Date(nextTrigger.nextRunAt);
        if (Number.isNaN(d.getTime())) return null;
        try {
          return new Intl.DateTimeFormat(i18n.language, { dateStyle: 'short', timeStyle: 'short' }).format(d);
        } catch {
          return d.toLocaleString();
        }
      })()
    : null;

  return (
    <Card
      size="1"
      variant="surface"
      style={{
        border: '1px solid var(--gray-a5)',
        borderRadius: 'var(--radius-3)',
      }}
    >
      <Flex direction="column" gap="1" p="2">
        <Flex align="center" gap="2" justify="between">
          <Text size="2" weight="medium" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {workflow.name}
          </Text>
          <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
            {liveStatus && (
              <Badge size="1" color={liveStatus === 'succeeded' ? 'green' : liveStatus === 'failed' ? 'red' : 'blue'}>
                {liveStatus}
              </Badge>
            )}
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <Badge size="1" color={statusColor(workflow.status) as any} variant="soft">
              {workflow.status}
            </Badge>
          </Flex>
        </Flex>

        {nextTrigger && (
          <Flex align="center" gap="2" wrap="wrap">
            <Badge size="1" variant="soft" color="violet">
              {triggerLabel(nextTrigger.kind)}
            </Badge>
            {nextRunAt && (
              <Text size="1" color="gray">
                Next: {nextRunAt}
              </Text>
            )}
          </Flex>
        )}

        <Flex align="center" gap="2" mt="1">
          <Tooltip content="Dry Run">
            <IconButton
              variant="ghost"
              size="1"
              color="blue"
              disabled={dryRunLoading === workflow.workflowId}
              onClick={() => onDryRun(workflow.workflowId)}
            >
              <MaterialIcon
                name={dryRunLoading === workflow.workflowId ? 'hourglass_empty' : 'play_circle'}
                size={14}
              />
            </IconButton>
          </Tooltip>
          <Link
            href={`/workflows?workflowId=${encodeURIComponent(workflow.workflowId)}`}
            style={{ textDecoration: 'none' }}
          >
            <Text size="1" color="violet">
              View
            </Text>
          </Link>
        </Flex>
      </Flex>
    </Card>
  );
}

/**
 * Slide-out panel listing all workflows connected to the active conversation.
 * Triggered by a toolbar icon in the chat header (see `page.tsx`).
 */
export function WorkflowPanel({ conversationId, onClose }: WorkflowPanelProps) {
  const { t } = useTranslation();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runStatuses, setRunStatuses] = useState<Record<string, string>>({});
  const [dryRunLoading, setDryRunLoading] = useState<string | null>(null);

  const fetchWorkflows = useCallback(async () => {
    if (!conversationId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await WorkflowsApi.listByConversation(conversationId);
      setWorkflows(resp.workflows ?? []);
    } catch {
      setError(t('workflowPanel.fetchError', { defaultValue: 'Failed to load workflows.' }));
    } finally {
      setLoading(false);
    }
  }, [conversationId, t]);

  useEffect(() => {
    void fetchWorkflows();
  }, [fetchWorkflows]);

  const handleRunUpdate = useCallback((update: WorkflowRunUpdate) => {
    setRunStatuses((prev) => ({ ...prev, [update.workflowId]: update.status }));
  }, []);
  useWorkflowRunUpdates(handleRunUpdate);

  const handleDryRun = useCallback(async (workflowId: string) => {
    setDryRunLoading(workflowId);
    try {
      await WorkflowsApi.dryRun(workflowId);
    } catch {
      // result is surfaced via workflowRunUpdate socket event
    } finally {
      setDryRunLoading(null);
    }
  }, []);

  return (
    <Box
      style={{
        position: 'absolute',
        top: 0,
        right: 0,
        bottom: 0,
        width: 320,
        background: 'var(--color-panel)',
        borderLeft: '1px solid var(--gray-a5)',
        zIndex: 30,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header */}
      <Flex align="center" justify="between" px="3" py="2" style={{ borderBottom: '1px solid var(--gray-a4)' }}>
        <Flex align="center" gap="2">
          <MaterialIcon name="account_tree" size={18} color="var(--violet-9)" />
          <Text size="2" weight="medium">
            {t('workflowPanel.title', { defaultValue: 'Workflows' })}
          </Text>
          {workflows.length > 0 && (
            <Badge size="1" color="violet" variant="soft">
              {workflows.length}
            </Badge>
          )}
        </Flex>
        <IconButton variant="ghost" color="gray" size="1" onClick={onClose} aria-label="Close workflow panel">
          <MaterialIcon name="close" size={18} />
        </IconButton>
      </Flex>

      {/* Body */}
      <ScrollArea style={{ flex: 1 }}>
        <Flex direction="column" gap="2" p="3">
          {loading && (
            <Flex align="center" justify="center" py="4">
              <MaterialIcon name="hourglass_empty" size={24} color="var(--gray-9)" />
            </Flex>
          )}

          {error && (
            <Text size="1" color="red">
              {error}
            </Text>
          )}

          {!loading && !error && workflows.length === 0 && (
            <Flex direction="column" align="center" gap="2" py="4">
              <MaterialIcon name="account_tree" size={32} color="var(--gray-7)" />
              <Text size="2" color="gray" align="center">
                {t('workflowPanel.empty', { defaultValue: 'No workflows connected to this conversation.' })}
              </Text>
            </Flex>
          )}

          {workflows.map((wf) => (
            <WorkflowRow
              key={wf.workflowId}
              workflow={wf}
              runStatuses={runStatuses}
              onDryRun={handleDryRun}
              dryRunLoading={dryRunLoading}
            />
          ))}
        </Flex>
      </ScrollArea>

      {/* Footer */}
      <Box px="3" py="2" style={{ borderTop: '1px solid var(--gray-a4)' }}>
        <Link href="/workflows" style={{ textDecoration: 'none' }}>
          <Flex align="center" gap="1">
            <Text size="1" color="violet" weight="medium">
              {t('workflowPanel.viewAll', { defaultValue: 'View all workflows' })}
            </Text>
            <MaterialIcon name="arrow_forward" size={14} />
          </Flex>
        </Link>
      </Box>
    </Box>
  );
}
