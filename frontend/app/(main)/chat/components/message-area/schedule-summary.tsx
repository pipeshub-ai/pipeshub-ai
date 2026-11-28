'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { scheduleCallOf, scheduleChangeOf } from '../../approval-preview';
import {
  scheduleCallWords,
  scheduleChangeWords,
  type ScheduleChangeWords,
  type ShownValue,
} from '@/app/(main)/workspace/schedules/schedule-words';
import { SchedulesApi } from '@/app/(main)/workspace/schedules/api';
import type { Schedule } from '@/app/(main)/workspace/schedules/types';
import { MarkedText } from './display-safety';
import { PreviewValue } from './preview-value';

/**
 * The schedule a `schedule_create` call makes (ADR-SCH-01 D4), in plain words: its name, when it runs ("Every
 * weekday at 9:00 AM (America/Los_Angeles)"), what each run is asked and where its notices go. The harness's card and
 * classic chat's card both show it (ADR-SCH-02 D5). Arguments that are not a schedule show `fallback`, the card's
 * own list of any call's arguments.
 */
export function ScheduleSummary({ args, fallback }: { args: string; fallback: React.ReactNode }) {
  const { t, i18n } = useTranslation();
  const call = useMemo(() => scheduleCallOf(args), [args]);
  if (call === null) return <>{fallback}</>;
  const words = scheduleCallWords(t, call, i18n.language);
  return (
    <Flex
      direction="column"
      gap="2"
      data-testid="schedule-summary"
      style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--olive-3)' }}
    >
      <PreviewValue field={{ ...call.name, label: words.labels.name }} />
      <Box>
        <Text as="div" size="1" color="gray" style={{ marginBottom: 2 }}>
          {words.labels.when}
        </Text>
        <Text as="div" size="2" data-testid="schedule-when">
          <MarkedText text={words.when} />
        </Text>
      </Box>
      <PreviewValue field={{ ...call.ask, label: words.labels.ask }} />
      {words.notify !== null ? (
        <Box>
          <Text as="div" size="1" color="gray" style={{ marginBottom: 2 }}>
            {words.labels.notify}
          </Text>
          <Text as="div" size="2">
            <MarkedText text={words.notify} />
          </Text>
        </Box>
      ) : null}
      <Text size="1" color="gray">
        {words.where}
      </Text>
    </Flex>
  );
}

/** A schedule as a change's card reads it, `{id, name, ask, when, notify, status, revision}`: what the classic card is sent. */
export function cardSchedule(schedule: Schedule): Record<string, unknown> {
  const trigger = schedule.trigger;
  const when =
    trigger.kind === 'once'
      ? { kind: 'once', at: trigger.at, timeZone: trigger.timeZone }
      : trigger.kind === 'cron'
        ? { kind: 'cron', cron: trigger.cron, timeZone: trigger.timeZone }
        : {};
  return {
    id: schedule.id,
    name: schedule.name,
    ask: schedule.query ?? '',
    when,
    notify: [...schedule.notify],
    status: schedule.status,
    revision: schedule.revision,
  };
}

/**
 * The current values a schedule change's card shows. The classic card is sent them; a harness card reads the schedule
 * itself, as the person, because the journal that carries a harness ask has no place for them. Undefined until read,
 * and whenever it cannot be (the card then lists the call's arguments).
 */
export function useCardSchedule(tool: 'update' | 'delete' | null, args: string, sent: unknown): unknown {
  const id = useMemo(() => {
    if (tool === null || (sent !== undefined && sent !== null)) return null;
    try {
      const parsed: unknown = JSON.parse(args);
      const value = parsed && typeof parsed === 'object' ? (parsed as { id?: unknown }).id : undefined;
      return typeof value === 'string' && value !== '' ? value : null;
    } catch {
      return null;
    }
  }, [tool, args, sent]);
  const [read, setRead] = useState<{ id: string; schedule: Record<string, unknown> } | null>(null);
  useEffect(() => {
    if (id === null) return undefined;
    let live = true;
    SchedulesApi.get(id).then(
      (schedule) => {
        if (live) setRead({ id, schedule: cardSchedule(schedule) });
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [id]);
  if (sent !== undefined && sent !== null) return sent;
  return read !== null && read.id === id ? read.schedule : undefined;
}

/**
 * A `schedule_update` or `schedule_delete` call in words (ADR-SCH-03 D5), from the call's arguments and the
 * schedule's current values; null when a before and after can't both be told, and the card lists the arguments.
 */
export function useScheduleChangeWords(tool: 'update' | 'delete' | null, args: string, current: unknown): ScheduleChangeWords | null {
  const { t, i18n } = useTranslation();
  return useMemo(() => {
    if (tool === null) return null;
    const change = scheduleChangeOf(tool, args, current);
    return change === null ? null : scheduleChangeWords(t, change, i18n.language);
  }, [tool, args, current, t, i18n.language]);
}

function Shown({ value, open }: { value: ShownValue; open: boolean }) {
  return <MarkedText text={open ? value.full : value.value} />;
}

/** One changed field: "What it asks: a short joke → a short animal joke", with "Show all" where either side was cut. */
function ChangeLine({ label, before, after, testId }: { label: string; before: ShownValue; after: ShownValue; testId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Box data-testid={testId}>
      <Text as="div" size="1" color="gray" style={{ marginBottom: 2 }}>
        {label}
      </Text>
      <Text as="div" size="2" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        <Shown value={before} open={open} />
        {' → '}
        <Shown value={after} open={open} />
      </Text>
      {before.redacted || after.redacted ? (
        <Text as="div" size="1" color="gray">
          {t('approvalCard.flow.redacted')}
        </Text>
      ) : null}
      {before.truncated || after.truncated ? (
        <Button variant="ghost" size="1" onClick={() => setOpen((v) => !v)} aria-expanded={open} style={{ marginTop: 'var(--space-1)' }}>
          {open ? t('approvalCard.flow.showLess') : t('approvalCard.flow.showAll')}
        </Button>
      ) : null}
    </Box>
  );
}

/** What a schedule change does, as `scheduleChangeWords` says it; the card's heading carries its title. */
export function ScheduleChangeSummary({ words }: { words: ScheduleChangeWords }) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      gap="2"
      data-testid="schedule-change"
      style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--olive-3)' }}
    >
      {words.changes.map((change) => (
        <ChangeLine
          key={change.field}
          testId={`schedule-change-${change.field}`}
          label={change.label}
          before={change.before}
          after={change.after}
        />
      ))}
      {words.context.map((line) => (
        <PreviewValue
          key={line.key}
          field={{ key: line.key, label: line.label, ...line.value }}
        />
      ))}
      <Text size="1" color="gray" data-testid="schedule-change-note">
        {words.note}
      </Text>
      <Text size="1" color="gray">
        {t('approvalCard.schedule.manage')}
      </Text>
    </Flex>
  );
}

/** A schedule change's arguments as its card shows them: the change in words, else `fallback` (any call's list). */
export function ScheduleChangeCall({
  tool,
  args,
  current,
  fallback,
}: {
  tool: 'update' | 'delete';
  args: string;
  current: unknown;
  fallback: React.ReactNode;
}) {
  const words = useScheduleChangeWords(tool, args, current);
  return words === null ? <>{fallback}</> : <ScheduleChangeSummary words={words} />;
}
