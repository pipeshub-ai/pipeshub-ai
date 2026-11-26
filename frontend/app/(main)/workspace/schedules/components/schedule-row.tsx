'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Code, Flex, Link, Text } from '@radix-ui/themes';
import { buildChatHref } from '@/chat/build-chat-url';
import { ConfirmationDialog } from '../../components/confirmation-dialog';
import { formatDateTime } from '../../harness-ui/format';
import { isHarnessError, refusalText, toHarnessError } from '../../harness-ui/refusal';
import { SchedulesApi } from '../api';
import { firingReasonKey, isSystemStatus, SCHEDULE_REFUSALS } from '../copy';
import type { AgentVersionPin, Firing, Schedule } from '../types';
import { ScheduleVersion } from './schedule-version';

const STATUS_COLOR = { active: 'green', paused: 'amber', disabled: 'gray' } as const;

const CATCH_UP_LABEL = {
  skip: 'schedules.create.catchUpSkip',
  run_latest: 'schedules.create.catchUpRunLatest',
  run_all: 'schedules.create.catchUpRunAll',
} as const;

/** The scheduler's misfire grace: a cron firing made later than this after its slot ran after an outage. */
const MISFIRE_GRACE_MS = 5 * 60_000;

/** A cron firing made well after its slot: PipesHub was down then and caught it up (TR-02). */
export const caughtUp = (firing: Firing): boolean => {
  if (firing.kind !== 'cron' || firing.createdAt === null) return false;
  const made = Date.parse(firing.createdAt);
  const slot = Date.parse(firing.slotAt);
  return Number.isFinite(made) && Number.isFinite(slot) && made - slot > MISFIRE_GRACE_MS;
};

function TriggerSummary({ schedule }: { schedule: Schedule }) {
  const { t } = useTranslation();
  const trigger = schedule.trigger;
  if (trigger.kind === 'cron') {
    return (
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t('schedules.trigger.cron')} <Code>{trigger.cron}</Code> · {trigger.timeZone} ·{' '}
        {t(CATCH_UP_LABEL[trigger.catchUp])}
      </Text>
    );
  }
  return (
    <Text size="1" style={{ color: 'var(--slate-11)' }}>
      {t(trigger.kind === 'webhook' ? 'schedules.trigger.webhook' : 'schedules.trigger.connector')}
    </Text>
  );
}

function FiringLine({ firing, agentKey }: { firing: Firing; agentKey: string | null }) {
  const { t, i18n } = useTranslation();
  const when = formatDateTime(firing.slotAt, i18n.language);
  const kind = t(caughtUp(firing) ? 'schedules.firing.kind.cronCaughtUp' : `schedules.firing.kind.${firing.kind}`);
  let detail: React.ReactNode;
  if (firing.status === 'admitted') {
    detail = firing.conversationId ? (
      <Link href={buildChatHref({ agentId: agentKey, conversationId: firing.conversationId })}>
        {t('schedules.firing.openConversation')}
      </Link>
    ) : (
      t('schedules.firing.admitted')
    );
  } else if (firing.status === 'refused') {
    detail = t('schedules.firing.refused', { reason: t(firingReasonKey(firing.reason)) });
  } else if (firing.status === 'skipped') {
    detail =
      firing.reason === 'overlap_skip'
        ? t('schedules.firing.skippedOverlap')
        : firing.reason === 'overlap_queue_full'
          ? t('schedules.firing.skippedQueueFull')
          : t('schedules.firing.skipped', { count: firing.missed });
  } else {
    detail =
      firing.reason === 'overlap_queued' ? t('schedules.firing.waitingForLastRun') : t('schedules.firing.pending');
  }
  return (
    <Flex as="div" role="listitem" gap="2" align="center" wrap="wrap" data-firing-status={firing.status}>
      <Text size="1" style={{ color: 'var(--slate-11)', minWidth: 160 }}>
        {when}
      </Text>
      <Badge variant="soft" color="gray">
        {kind}
      </Badge>
      <Text size="1">{detail}</Text>
    </Flex>
  );
}

/**
 * One schedule with what its viewer may do: the actor pauses, resumes,
 * deletes and runs it now; an admin of the org (`orgView`) never runs it now
 * and never sees its prompt.
 */
export function ScheduleRow({
  schedule,
  agentName,
  ownerName,
  orgView,
  onAction,
  onPin,
}: {
  schedule: Schedule;
  agentName: string;
  ownerName?: string;
  orgView: boolean;
  /** Rejects with a `HarnessError`. */
  onAction: (action: 'pause' | 'resume' | 'remove') => Promise<void>;
  /** OWD-14: move the schedule to another agent version. Rejects with a `HarnessError`. */
  onPin?: (pin: AgentVersionPin) => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [ran, setRan] = useState<Firing | null>(null);
  const [firings, setFirings] = useState<Firing[] | null>(null);
  const [firingsError, setFiringsError] = useState<string | null>(null);

  const say = (failure: unknown) =>
    setError(refusalText(t, SCHEDULE_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure, true)));

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (failure) {
      say(failure);
    } finally {
      setBusy(false);
    }
  };

  const toggleFirings = async () => {
    if (firings !== null) {
      setFirings(null);
      return;
    }
    setFiringsError(null);
    try {
      setFirings(await SchedulesApi.firings(schedule.id));
    } catch (failure) {
      setFiringsError(refusalText(t, SCHEDULE_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure)));
    }
  };

  const systemStatus = isSystemStatus(schedule.status, schedule.statusReason);

  return (
    <Flex
      direction="column"
      gap="2"
      role="group"
      aria-label={schedule.name}
      data-testid="schedule-row"
      style={{ borderBottom: '1px solid var(--olive-3)', paddingBottom: 'var(--space-3)' }}
    >
      <Flex justify="between" align="start" gap="3" direction={{ initial: 'column', sm: 'row' }}>
        <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
          <Flex gap="2" align="center">
            <Text size="2" weight="medium">
              {schedule.name}
            </Text>
            <Badge variant="soft" color={STATUS_COLOR[schedule.status]}>
              {t(`schedules.status.${schedule.status}`)}
            </Badge>
          </Flex>
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {agentName}
            {ownerName ? ` · ${t('schedules.runsAs', { name: ownerName })}` : ''}
          </Text>
          <TriggerSummary schedule={schedule} />
          {onPin ? <ScheduleVersion schedule={schedule} orgView={orgView} onPin={onPin} /> : null}
          {schedule.query !== null && !orgView && (
            <Text size="1" style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap' }}>
              {schedule.query}
            </Text>
          )}
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {schedule.nextFireAt
              ? t('schedules.nextRun', { date: formatDateTime(schedule.nextFireAt, i18n.language) })
              : t('schedules.noNextRun')}
            {' · '}
            {schedule.maxCostUsd !== null
              ? t('schedules.budget', { amount: schedule.maxCostUsd })
              : t('schedules.budgetDefault')}
          </Text>
          {systemStatus && (
            <Text size="1" role="status" style={{ color: 'var(--amber-11)' }}>
              {t(schedule.status === 'disabled' ? 'schedules.disabledBecause' : 'schedules.pausedBecause', {
                reason: t(firingReasonKey(schedule.statusReason)),
              })}
            </Text>
          )}
        </Flex>
        <Flex gap="2" wrap="wrap">
          {schedule.status === 'active' && (
            <Button type="button" variant="soft" disabled={busy} onClick={() => void run(() => onAction('pause'))}>
              {t('schedules.actions.pause')}
            </Button>
          )}
          {schedule.status === 'paused' && (
            <Button type="button" variant="soft" disabled={busy} onClick={() => void run(() => onAction('resume'))}>
              {t('schedules.actions.resume')}
            </Button>
          )}
          {!orgView && schedule.status !== 'disabled' && (
            <Button
              type="button"
              variant="soft"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setRan(await SchedulesApi.runNow(schedule.id));
                })
              }
            >
              {t('schedules.actions.runNow')}
            </Button>
          )}
          <Button type="button" variant="soft" onClick={() => void toggleFirings()} aria-expanded={firings !== null}>
            {t('schedules.actions.history')}
          </Button>
          <Button type="button" variant="soft" color="red" disabled={busy} onClick={() => setConfirmDelete(true)}>
            {t('schedules.actions.delete')}
          </Button>
        </Flex>
      </Flex>

      {ran && (
        <Text size="1" role="status">
          {ran.status === 'admitted' && ran.conversationId ? (
            <>
              {t('schedules.started')}{' '}
              <Link href={buildChatHref({ agentId: schedule.agentKey, conversationId: ran.conversationId })}>
                {t('schedules.firing.openConversation')}
              </Link>
            </>
          ) : ran.status === 'refused' ? (
            t('schedules.firing.refused', { reason: t(firingReasonKey(ran.reason)) })
          ) : (
            t('schedules.firing.pending')
          )}
        </Text>
      )}
      {error && (
        <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
          {error}
        </Text>
      )}
      {firingsError && (
        <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
          {firingsError}
        </Text>
      )}
      {firings !== null && (
        <Flex direction="column" gap="1" role="list" aria-label={t('schedules.historyLabel', { name: schedule.name })}>
          {firings.length === 0 ? (
            <Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t('schedules.firing.none')}
            </Text>
          ) : (
            firings.map((firing) => <FiringLine key={firing.id} firing={firing} agentKey={schedule.agentKey} />)
          )}
        </Flex>
      )}

      <ConfirmationDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t('schedules.delete.title', { name: schedule.name })}
        message={t('schedules.delete.message')}
        confirmLabel={t('schedules.actions.delete')}
        isLoading={busy}
        onConfirm={() =>
          void run(async () => {
            setConfirmDelete(false);
            await onAction('remove');
          })
        }
      />
    </Flex>
  );
}
