'use client';

import React, { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, RadioGroup, Text } from '@radix-ui/themes';
import { isHarnessError, refusalText, toHarnessError } from '../../harness-ui/refusal';
import { SCHEDULE_REFUSALS } from '../copy';
import type { AgentVersionPin, Schedule } from '../types';

type Choice = 'keep' | 'move' | 'latest';

/** The choices a schedule's owner has, given what it runs now and the version live now (null: unknown). */
export function versionChoices(pin: AgentVersionPin, live: number | null): Choice[] {
  if (pin === 'latest') return live !== null ? ['latest', 'keep'] : ['latest'];
  return live !== null && live !== pin ? ['keep', 'move', 'latest'] : ['keep', 'latest'];
}

/** The pin a choice sets. */
export function pinOf(choice: Choice, pin: AgentVersionPin, live: number | null): AgentVersionPin {
  if (choice === 'latest') return 'latest';
  if (choice === 'move') return live ?? pin;
  // "Keep": a schedule that followed every publish keeps the version live now.
  return pin === 'latest' ? (live ?? 'latest') : pin;
}

/**
 * Which agent version a schedule runs (OWD-14): it keeps the version it pinned until its owner moves it, or follows
 * every publish. Only the schedule's own user changes it; an admin of the org sees it read-only.
 */
export function ScheduleVersion({
  schedule,
  orgView,
  onPin,
}: {
  schedule: Schedule;
  orgView: boolean;
  /** Rejects with a `HarnessError`. */
  onPin: (pin: AgentVersionPin) => Promise<void>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const pin = schedule.agentVersion;
  const agentKey = schedule.agentKey;
  // ADR-UI-03: the list answers the version live now with each schedule, so a row asks nothing of its own.
  const live = schedule.agentLiveVersion;
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<Choice>(pin === 'latest' ? 'latest' : 'keep');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setChoice(pin === 'latest' ? 'latest' : 'keep');
      setError(null);
    }
  }, [open, pin]);

  if (!agentKey || pin === null) return null;

  const behind = typeof pin === 'number' && live !== null && live > pin;
  const current: Choice = pin === 'latest' ? 'latest' : 'keep';
  const labels: Record<Choice, { title: string; hint: string }> = {
    keep: {
      title: t('schedules.version.keep', { version: pin === 'latest' ? live : pin }),
      hint: t('schedules.version.keepHint'),
    },
    move: { title: t('schedules.version.move', { version: live }), hint: t('schedules.version.moveHint') },
    latest: { title: t('schedules.version.latest'), hint: t('schedules.version.latestHint') },
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await onPin(pinOf(choice, pin, live));
      setOpen(false);
    } catch (failure) {
      setError(refusalText(t, SCHEDULE_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure, true)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Flex direction="column" gap="2" data-testid="schedule-version">
      <Flex gap="2" align="center" wrap="wrap">
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {pin === 'latest' ? t('schedules.version.runsLatest') : t('schedules.version.runs', { version: pin })}
        </Text>
        {behind ? (
          <Badge size="1" color="amber" variant="soft">
            {t('schedules.version.liveIs', { version: live })}
          </Badge>
        ) : null}
        {orgView ? (
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('schedules.version.ownerOnly')}
          </Text>
        ) : (
          <Button type="button" size="1" variant="ghost" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {t('schedules.version.change')}
          </Button>
        )}
      </Flex>
      {open && !orgView ? (
        <Flex
          direction="column"
          gap="2"
          role="group"
          aria-labelledby={`${id}-title`}
          style={{ border: '1px solid var(--olive-4)', borderRadius: 'var(--radius-3)', padding: 'var(--space-3)', maxWidth: 520 }}
        >
          <Text id={`${id}-title`} size="2" weight="medium">
            {t('schedules.version.label')}
          </Text>
          <RadioGroup.Root
            aria-labelledby={`${id}-title`}
            value={choice}
            onValueChange={(value) => setChoice(value as Choice)}
          >
            {versionChoices(pin, live).map((option) => (
              <RadioGroup.Item key={option} value={option} style={{ alignItems: 'flex-start', minHeight: 44 }}>
                <Flex direction="column">
                  <Text size="2" weight="medium">
                    {labels[option].title}
                  </Text>
                  <Text size="1" style={{ color: 'var(--slate-11)' }}>
                    {labels[option].hint}
                  </Text>
                </Flex>
              </RadioGroup.Item>
            ))}
          </RadioGroup.Root>
          {error ? (
            <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
              {error}
            </Text>
          ) : null}
          <Flex gap="2">
            <Button type="button" size="2" disabled={busy || choice === current} onClick={() => void save()}>
              {t('schedules.version.save')}
            </Button>
            <Button type="button" size="2" variant="soft" color="gray" disabled={busy} onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </Button>
          </Flex>
        </Flex>
      ) : null}
    </Flex>
  );
}
