'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import Link from 'next/link';
import { Badge, Button, Flex, Text } from '@radix-ui/themes';
import { useFeatureFlagsStore, selectAgentHarnessPaused, selectScheduledRunsEnabled } from '@/lib/store/feature-flags-store';
import { SchedulesApi } from '@/app/(main)/workspace/schedules/api';
import { isSystemStatus, firingReasonKey, ranOnce } from '@/app/(main)/workspace/schedules/copy';
import { onceWords } from '@/app/(main)/workspace/schedules/schedule-words';
import { formatDateTime } from '@/app/(main)/workspace/harness-ui/format';
import { ScheduleDrawer } from '@/app/(main)/workspace/schedules/components/schedule-drawer';
import { WebhookSecretDialog, type WebhookCredential } from '@/app/(main)/workspace/schedules/components/webhook-secret-dialog';
import type { NewSchedule, Schedule } from '@/app/(main)/workspace/schedules/types';
import { Section } from './core-node-section';

const K = 'agentBuilder.schedules';
export const SCHEDULES_ROUTE = '/workspace/schedules';

type Load = { status: 'loading' } | { status: 'ready'; schedules: Schedule[] } | { status: 'unavailable' };

function openAgentKey(): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get('agentKey');
}

/**
 * "Schedules" on the agent card (UX-07): the person's own schedules of this agent, in words (never the raw cron),
 * and a way to add one with this agent already chosen. Schedules are saved and checked by their own route; the full
 * list and every action stay on Workspace > Schedules.
 */
export function AgentSchedules({ readOnly = false }: { readOnly?: boolean }) {
  const { t, i18n } = useTranslation();
  const enabled = useFeatureFlagsStore(selectScheduledRunsEnabled);
  const paused = useFeatureFlagsStore(selectAgentHarnessPaused);
  const [agentKey] = useState<string | null>(openAgentKey);
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<WebhookCredential | null>(null);

  const read = useCallback(async () => {
    if (agentKey === null) return;
    try {
      const mine = await SchedulesApi.list('mine');
      setLoad({ status: 'ready', schedules: mine.filter((schedule) => schedule.agentKey === agentKey) });
    } catch {
      setLoad({ status: 'unavailable' });
    }
  }, [agentKey]);

  useEffect(() => {
    if (enabled && agentKey !== null) void read();
  }, [enabled, agentKey, read]);

  if (!enabled) return null;

  const create = async (input: NewSchedule) => {
    const created = await SchedulesApi.create(input);
    setCreating(false);
    if (created.webhookSecret) setSecret({ scheduleId: created.schedule.id, kind: 'signature', value: created.webhookSecret });
    else if (created.webhookToken) setSecret({ scheduleId: created.schedule.id, kind: 'token', value: created.webhookToken });
    await read();
  };

  const when = (schedule: Schedule): string => {
    if (ranOnce(schedule)) return t('schedules.ranOnce');
    if (schedule.status !== 'active') {
      return isSystemStatus(schedule.status, schedule.statusReason)
        ? t(schedule.status === 'disabled' ? 'schedules.disabledBecause' : 'schedules.pausedBecause', {
            reason: t(firingReasonKey(schedule.statusReason)),
          })
        : t(`${K}.pausedByYou`);
    }
    if (schedule.skipNextAt !== null) return t('schedules.skippedNext');
    if (schedule.trigger.kind === 'once') {
      return onceWords(t, schedule.trigger.at, schedule.trigger.timeZone, i18n.language);
    }
    if (schedule.trigger.kind !== 'cron') {
      return t(schedule.trigger.kind === 'webhook' ? 'schedules.trigger.webhook' : 'schedules.trigger.connector');
    }
    return schedule.nextFireAt
      ? t('schedules.nextRun', { date: formatDateTime(schedule.nextFireAt, i18n.language) })
      : t('schedules.noNextRun');
  };

  return (
    <Section title={t(`${K}.title`)} icon="schedule">
      <Flex direction="column" gap="2" style={{ padding: 'var(--space-2)' }} data-testid="agent-schedules">
        {paused && (
          <Text size="1" role="note" data-testid="agent-schedules-paused" style={{ color: 'var(--amber-11)' }}>
            {t('schedules.harnessPaused')}
          </Text>
        )}
        {agentKey === null ? (
          <Text size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
            {t(`${K}.saveFirst`)}
          </Text>
        ) : load.status === 'loading' ? (
          <Text size="1" role="status" style={{ color: 'var(--agent-flow-text-muted)' }}>
            {t('aiAccounts.common.loading')}
          </Text>
        ) : load.status === 'unavailable' ? (
          <Flex direction="column" gap="2" align="start" role="alert">
            <Text size="1">{t(`${K}.unavailable`)}</Text>
            <Button className="nodrag nopan" type="button" size="1" variant="soft" onClick={() => void read()}>
              {t('aiAccounts.common.tryAgain')}
            </Button>
          </Flex>
        ) : (
          <>
            <Text size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
              {load.schedules.length === 0 ? t(`${K}.empty`) : t(`${K}.hint`)}
            </Text>
            {load.schedules.length > 0 ? (
              <Flex direction="column" role="list" aria-label={t(`${K}.title`)}>
                {load.schedules.map((schedule) => (
                  <Flex
                    key={schedule.id}
                    role="listitem"
                    align="center"
                    justify="between"
                    gap="2"
                    wrap="wrap"
                    style={{ padding: 'var(--space-1) 0', borderBottom: '1px solid var(--gray-a3)', minHeight: 44 }}
                  >
                    <Flex direction="column" gap="1" style={{ minWidth: 0, flex: '1 1 8rem' }}>
                      <Text size="1" weight="medium" style={{ color: 'var(--gray-12)', overflowWrap: 'anywhere' }}>
                        {schedule.name}
                      </Text>
                      <Text size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
                        {when(schedule)}
                      </Text>
                    </Flex>
                    <Badge size="1" color={schedule.status === 'active' ? 'jade' : 'gray'}>
                      {t(`schedules.status.${schedule.status}`)}
                    </Badge>
                  </Flex>
                ))}
              </Flex>
            ) : null}
            <Flex gap="3" align="center" wrap="wrap">
              {!readOnly ? (
                <Button
                  className="nodrag nopan"
                  type="button"
                  size="2"
                  variant="soft"
                  onClick={() => setCreating(true)}
                  style={{ minHeight: 44 }}
                >
                  {t(`${K}.add`)}
                </Button>
              ) : null}
              <Link href={SCHEDULES_ROUTE} className="nodrag nopan" style={{ fontSize: 12, color: 'var(--accent-11)' }}>
                {t(`${K}.manage`)}
              </Link>
            </Flex>
          </>
        )}
      </Flex>
      {agentKey !== null ? (
        <>
          <ScheduleDrawer
            open={creating}
            onOpenChange={setCreating}
            agents={[{ id: agentKey, name: t(`${K}.thisAgent`) }]}
            initialAgent={agentKey}
            onSubmit={create}
          />
          <WebhookSecretDialog secret={secret} onClose={() => setSecret(null)} />
        </>
      ) : null}
    </Section>
  );
}
