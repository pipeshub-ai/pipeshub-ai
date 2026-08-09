'use client';

import React, { useCallback, useMemo, useState } from 'react';
import { Badge, Button, Flex, Heading, IconButton, Select, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useToastStore } from '@/lib/store/toast-store';
import { WorkflowsApi } from '../api';
import type { CreateTriggerRequest, TriggerKind, WorkflowTrigger } from '../types';

const CREATABLE_KINDS: TriggerKind[] = ['cron', 'interval', 'one_time', 'webhook'];

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function triggerDetail(trigger: WorkflowTrigger): string {
  switch (trigger.kind) {
    case 'cron':
      return trigger.cronExpression ?? '';
    case 'interval':
      return trigger.intervalSeconds ? `every ${trigger.intervalSeconds}s` : '';
    case 'one_time':
      return formatDateTime(trigger.fireAt);
    case 'event':
      // The backend passes the filter dict through untouched and its keys are
      // snake_case, so `eventType` reads as undefined here.
      return String(trigger.eventFilter?.event_type ?? '');
    case 'webhook':
      return trigger.webhookId ? `webhook ${trigger.webhookId.slice(0, 8)}…` : '';
    default:
      return '';
  }
}

/**
 * Shown once, immediately after a webhook trigger is created. The secret is
 * stored one-way, so if the user navigates away without copying it the only
 * remedy is to delete the trigger and make a new one.
 */
function WebhookSecretCallout({
  path,
  secret,
  onDismiss,
}: {
  path: string;
  secret: string;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const absoluteUrl = useMemo(
    () => (typeof window === 'undefined' ? path : `${window.location.origin}${path}`),
    [path]
  );

  const copy = useCallback(
    async (value: string) => {
      try {
        await navigator.clipboard.writeText(value);
        addToast({ variant: 'success', title: t('common.copied', 'Copied'), duration: 2000 });
      } catch {
        addToast({
          variant: 'error',
          title: t('common.copyFailed', 'Could not copy to clipboard'),
          duration: 3000,
        });
      }
    },
    [addToast, t]
  );

  return (
    <Flex
      direction="column"
      gap="2"
      style={{
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--amber-a2)',
        border: '1px solid var(--amber-6)',
      }}
    >
      <Flex align="center" justify="between" gap="2">
        <Flex align="center" gap="2">
          <MaterialIcon name="key" size={16} style={{ color: 'var(--amber-11)' }} />
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workflowsPage.triggersPanel.secretTitle', 'Copy this signing secret now')}
          </Text>
        </Flex>
        <IconButton size="1" variant="ghost" color="gray" onClick={onDismiss}>
          <MaterialIcon name="close" size={14} />
        </IconButton>
      </Flex>
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t(
          'workflowsPage.triggersPanel.secretHelp',
          'It is shown only once. The URL below must be reachable from the calling service — if PipesHub sits behind a different public hostname, use that host with this path.'
        )}
      </Text>
      <Flex align="center" gap="2">
        <TextField.Root size="1" style={{ flex: 1 }} readOnly value={absoluteUrl} />
        <IconButton size="1" variant="soft" onClick={() => void copy(absoluteUrl)}>
          <MaterialIcon name="content_copy" size={14} />
        </IconButton>
      </Flex>
      <Flex align="center" gap="2">
        <TextField.Root size="1" style={{ flex: 1 }} readOnly value={secret} />
        <IconButton size="1" variant="soft" onClick={() => void copy(secret)}>
          <MaterialIcon name="content_copy" size={14} />
        </IconButton>
      </Flex>
    </Flex>
  );
}

function AddTriggerForm({
  workflowId,
  onCreated,
  onCancel,
  onWebhookSecret,
}: {
  workflowId: string;
  onCreated: () => Promise<void> | void;
  onCancel: () => void;
  onWebhookSecret: (path: string, secret: string) => void;
}) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [kind, setKind] = useState<TriggerKind>('cron');
  const [cronExpression, setCronExpression] = useState('0 9 * * *');
  const [intervalSeconds, setIntervalSeconds] = useState('3600');
  const [fireAt, setFireAt] = useState('');
  const [saving, setSaving] = useState(false);

  const buildSpec = useCallback((): CreateTriggerRequest | null => {
    switch (kind) {
      case 'cron':
        return cronExpression.trim() ? { kind, cronExpression: cronExpression.trim() } : null;
      case 'interval': {
        const seconds = Number(intervalSeconds);
        return Number.isFinite(seconds) && seconds > 0 ? { kind, intervalSeconds: seconds } : null;
      }
      case 'one_time': {
        if (!fireAt) return null;
        const at = new Date(fireAt);
        return Number.isNaN(at.getTime()) ? null : { kind, fireAt: at.toISOString() };
      }
      case 'webhook':
        return { kind };
      default:
        return null;
    }
  }, [kind, cronExpression, intervalSeconds, fireAt]);

  const spec = buildSpec();

  const submit = useCallback(async () => {
    if (!spec) return;
    setSaving(true);
    try {
      const created = await WorkflowsApi.createTrigger(workflowId, spec);
      if (created.webhookSecret && created.webhookPath) {
        onWebhookSecret(created.webhookPath, created.webhookSecret);
      }
      await onCreated();
      onCancel();
    } catch (err) {
      addToast({
        variant: 'error',
        title: t('workflowsPage.triggersPanel.createError', 'Could not add trigger'),
        description: err instanceof Error ? err.message : undefined,
        duration: 4000,
      });
    } finally {
      setSaving(false);
    }
  }, [spec, workflowId, onCreated, onCancel, onWebhookSecret, addToast, t]);

  return (
    <Flex
      direction="column"
      gap="3"
      style={{
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--gray-a2)',
        border: '1px solid var(--slate-5)',
      }}
    >
      <Flex align="center" gap="2" wrap="wrap">
        <Select.Root value={kind} onValueChange={(v) => setKind(v as TriggerKind)} size="1">
          <Select.Trigger />
          <Select.Content>
            {CREATABLE_KINDS.map((k) => (
              <Select.Item key={k} value={k}>
                {k}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>

        {kind === 'cron' && (
          <TextField.Root
            size="1"
            style={{ minWidth: 180 }}
            placeholder="0 9 * * *"
            value={cronExpression}
            onChange={(e) => setCronExpression(e.target.value)}
          />
        )}
        {kind === 'interval' && (
          <TextField.Root
            size="1"
            type="number"
            min="1"
            style={{ width: 120 }}
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(e.target.value)}
          />
        )}
        {kind === 'one_time' && (
          <TextField.Root
            size="1"
            type="datetime-local"
            value={fireAt}
            onChange={(e) => setFireAt(e.target.value)}
          />
        )}
        {kind === 'webhook' && (
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t(
              'workflowsPage.triggersPanel.webhookHelp',
              'A URL and signing secret are generated when you add this.'
            )}
          </Text>
        )}
      </Flex>

      <Flex align="center" gap="2" justify="end">
        <Button size="1" variant="ghost" color="gray" onClick={onCancel} disabled={saving}>
          {t('common.cancel', 'Cancel')}
        </Button>
        <LoadingButton size="1" loading={saving} disabled={!spec} onClick={() => void submit()}>
          {t('workflowsPage.triggersPanel.add', 'Add trigger')}
        </LoadingButton>
      </Flex>
    </Flex>
  );
}

export function WorkflowTriggersPanel({
  workflowId,
  triggers,
  onChanged,
}: {
  workflowId: string;
  triggers: WorkflowTrigger[];
  /** Refetches the workflow so the panel reflects server-computed next-run times. */
  onChanged: () => Promise<void> | void;
}) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [adding, setAdding] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<{ path: string; secret: string } | null>(null);

  const mutate = useCallback(
    async (triggerId: string, action: () => Promise<unknown>, errorTitle: string) => {
      setPendingId(triggerId);
      try {
        await action();
        await onChanged();
      } catch (err) {
        addToast({
          variant: 'error',
          title: errorTitle,
          description: err instanceof Error ? err.message : undefined,
          duration: 4000,
        });
      } finally {
        setPendingId(null);
      }
    },
    [onChanged, addToast]
  );

  return (
    <Flex direction="column" gap="2">
      <Flex align="center" justify="between" gap="2">
        <Heading size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workflowsPage.triggers', 'Triggers')}
        </Heading>
        {!adding && (
          <Button size="1" variant="ghost" onClick={() => setAdding(true)}>
            <MaterialIcon name="add" size={14} />
            {t('workflowsPage.triggersPanel.add', 'Add trigger')}
          </Button>
        )}
      </Flex>

      {revealed && (
        <WebhookSecretCallout
          path={revealed.path}
          secret={revealed.secret}
          onDismiss={() => setRevealed(null)}
        />
      )}

      {adding && (
        <AddTriggerForm
          workflowId={workflowId}
          onCreated={onChanged}
          onCancel={() => setAdding(false)}
          onWebhookSecret={(path, secret) => setRevealed({ path, secret })}
        />
      )}

      {triggers.length === 0 && !adding ? (
        <Text size="2" style={{ color: 'var(--slate-9)' }}>
          {t('workflowsPage.detail.noTriggers', 'No schedule configured — this workflow only runs on demand.')}
        </Text>
      ) : (
        <Flex direction="column">
          {triggers.map((trigger) => {
            const detail = triggerDetail(trigger);
            const busy = pendingId === trigger.triggerId;
            return (
              <Flex
                key={trigger.triggerId}
                align="center"
                justify="between"
                gap="3"
                wrap="wrap"
                style={{ padding: 'var(--space-2) 0' }}
              >
                <Flex align="center" gap="2">
                  <Badge variant="soft" color={trigger.enabled ? 'iris' : 'gray'} size="1">
                    {trigger.kind}
                  </Badge>
                  {detail ? (
                    <Text size="2" style={{ color: 'var(--slate-11)' }}>
                      {detail}
                    </Text>
                  ) : null}
                </Flex>
                <Flex align="center" gap="2">
                  <Text size="1" style={{ color: 'var(--slate-9)' }}>
                    {trigger.nextRunAt
                      ? t('workflowsPage.detail.nextRun', 'Next: {{when}}', {
                          when: formatDateTime(trigger.nextRunAt),
                        })
                      : t('workflowsPage.detail.noNextRun', 'No upcoming run')}
                  </Text>
                  <IconButton
                    size="1"
                    variant="ghost"
                    color="gray"
                    disabled={busy}
                    title={
                      trigger.enabled
                        ? t('workflowsPage.triggersPanel.disable', 'Disable')
                        : t('workflowsPage.triggersPanel.enable', 'Enable')
                    }
                    onClick={() =>
                      void mutate(
                        trigger.triggerId,
                        () =>
                          WorkflowsApi.setTriggerEnabled(
                            workflowId,
                            trigger.triggerId,
                            !trigger.enabled
                          ),
                        t('workflowsPage.triggersPanel.toggleError', 'Could not update trigger')
                      )
                    }
                  >
                    <MaterialIcon name={trigger.enabled ? 'pause' : 'play_arrow'} size={14} />
                  </IconButton>
                  <IconButton
                    size="1"
                    variant="ghost"
                    color="red"
                    disabled={busy}
                    title={t('common.delete', 'Delete')}
                    onClick={() =>
                      void mutate(
                        trigger.triggerId,
                        () => WorkflowsApi.deleteTrigger(workflowId, trigger.triggerId),
                        t('workflowsPage.triggersPanel.deleteError', 'Could not delete trigger')
                      )
                    }
                  >
                    <MaterialIcon name="delete" size={14} />
                  </IconButton>
                </Flex>
              </Flex>
            );
          })}
        </Flex>
      )}
    </Flex>
  );
}
