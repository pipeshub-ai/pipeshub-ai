'use client';

import React, { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Checkbox, Dialog, Flex, RadioGroup, Select, Text, TextArea, TextField } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { isHarnessError, refusalText, toHarnessError } from '../../harness-ui/refusal';
import { NOTIFY_CHANNELS, SchedulesApi } from '../api';
import { SCHEDULE_REFUSALS } from '../copy';
import type { CatchUpPolicy, NewSchedule, NoticeChannelChoice, NotifyChannel, OverlapPolicy } from '../types';

export interface AgentOption {
  id: string;
  name: string;
}

const ASSISTANT = '__assistant__';

export const browserTimeZone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};

export interface ScheduleForm {
  name: string;
  agent: string;
  query: string;
  kind: 'cron' | 'webhook';
  cron: string;
  timeZone: string;
  catchUp: CatchUpPolicy;
  overlap: OverlapPolicy;
  notify: NotifyChannel[];
  /** The webhook's caller can't sign: it gets a token to send instead (TR-04). */
  tokenAuth: boolean;
  budget: string;
  deadlineHours: string;
  /** OWD-14: keep the version live now, or follow every publish. */
  version: 'pinned' | 'latest';
}

export const emptyForm = (): ScheduleForm => ({
  name: '',
  agent: ASSISTANT,
  query: '',
  kind: 'cron',
  cron: '',
  timeZone: browserTimeZone(),
  catchUp: 'skip',
  overlap: 'skip',
  notify: ['in_app'],
  tokenAuth: false,
  budget: '',
  deadlineHours: '',
  version: 'pinned',
});

/** The form as the create route's body; a blank optional number is absent, not zero. */
export function toNewSchedule(form: ScheduleForm): NewSchedule {
  const budget = form.budget.trim() === '' ? null : Number(form.budget);
  const hours = form.deadlineHours.trim() === '' ? null : Number(form.deadlineHours);
  return {
    name: form.name.trim(),
    agentKey: form.agent === ASSISTANT ? null : form.agent,
    ...(form.agent !== ASSISTANT && form.version === 'latest' ? { agentVersion: 'latest' as const } : {}),
    query: form.query,
    trigger:
      form.kind === 'cron'
        ? { kind: 'cron', cron: form.cron.trim(), timeZone: form.timeZone.trim(), catchUp: form.catchUp }
        : { kind: 'webhook', auth: form.tokenAuth ? 'token' : 'signature' },
    maxCostUsd: budget,
    deadlineS: hours === null ? null : Math.round(hours * 3600),
    overlap: form.overlap,
    notify: NOTIFY_CHANNELS.filter((channel) => form.notify.includes(channel)),
  };
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <Flex direction="column" gap="1">
      <Text as="label" htmlFor={id} size="2" weight="medium">
        {label}
      </Text>
      {children}
      {hint && (
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {hint}
        </Text>
      )}
    </Flex>
  );
}

const CATCH_UP: ReadonlyArray<{ value: CatchUpPolicy; label: string; hint?: string }> = [
  { value: 'skip', label: 'schedules.create.catchUpSkip' },
  { value: 'run_latest', label: 'schedules.create.catchUpRunLatest' },
  { value: 'run_all', label: 'schedules.create.catchUpRunAll', hint: 'schedules.create.catchUpRunAllHint' },
];

const OVERLAP: ReadonlyArray<{ value: OverlapPolicy; label: string; hint?: string }> = [
  { value: 'skip', label: 'schedules.create.overlapSkip' },
  { value: 'queue', label: 'schedules.create.overlapQueue', hint: 'schedules.create.overlapQueueHint' },
  { value: 'allow', label: 'schedules.create.overlapAllow' },
];

const CHANNEL_COPY: Readonly<Record<NotifyChannel, { label: string; hint: string }>> = {
  in_app: { label: 'schedules.create.notifyInApp', hint: 'schedules.create.notifyInAppHint' },
  email: { label: 'schedules.create.notifyEmail', hint: 'schedules.create.notifyEmailHint' },
  slack: { label: 'schedules.create.notifySlack', hint: 'schedules.create.notifySlackHint' },
};

/** A radio group whose options can carry a second line. */
function Choice<T extends string>({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: T;
  options: ReadonlyArray<{ value: T; label: string; hint?: string }>;
  onChange: (value: T) => void;
}) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="1">
      <Text as="span" id={`${id}-label`} size="2" weight="medium">
        {label}
      </Text>
      <RadioGroup.Root
        aria-labelledby={`${id}-label`}
        value={value}
        onValueChange={(next) => {
          const found = options.find((option) => option.value === next);
          if (found) onChange(found.value);
        }}
      >
        {options.map((option) => (
          <RadioGroup.Item
            key={option.value}
            value={option.value}
            aria-describedby={option.hint ? `${id}-${option.value}-hint` : undefined}
          >
            <Flex direction="column">
              <Text size="2">{t(option.label)}</Text>
              {option.hint && (
                <Text id={`${id}-${option.value}-hint`} size="1" style={{ color: 'var(--slate-11)' }}>
                  {t(option.hint)}
                </Text>
              )}
            </Flex>
          </RadioGroup.Item>
        ))}
      </RadioGroup.Root>
    </Flex>
  );
}

/** Why a channel can't be picked, in words; Slack's reason depends on whether an agent was chosen. */
function blockedKey(choice: NoticeChannelChoice | undefined, assistant: boolean): string | null {
  if (choice === undefined) return 'schedules.create.notifyUnknown';
  if (choice.available) return null;
  if (choice.reason === 'no_slack_bot') {
    return assistant ? 'schedules.create.notifyNoSlackBotAssistant' : 'schedules.create.notifyNoSlackBot';
  }
  return 'schedules.create.notifyOrgOff';
}

/**
 * Which notice channels this agent's schedule may use (TR-07), read when the
 * dialog opens or the agent changes. Until it answers, or if it fails, only
 * "In PipesHub" is offered.
 */
function useNoticeChannels(open: boolean, agentKey: string | null) {
  const [choices, setChoices] = useState<NoticeChannelChoice[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!open) return;
    let live = true;
    setFailed(false);
    void SchedulesApi.noticeChannels(agentKey)
      .then((found) => {
        if (live) setChoices(found);
      })
      .catch(() => {
        if (!live) return;
        setChoices(null);
        setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [open, agentKey]);
  return { choices, failed };
}

/** A new schedule: what runs (agent, prompt), when (cron in a zone, or a webhook) and its limits. */
export function CreateScheduleDialog({
  open,
  onOpenChange,
  agents,
  initialAgent,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agents: AgentOption[];
  /** The agent chosen when the dialog opens (the agent card's "Add a schedule"); absent: the assistant. */
  initialAgent?: string;
  /** Rejects with a `HarnessError`; the dialog stays open and says why. */
  onSubmit: (input: NewSchedule) => Promise<void>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [form, setForm] = useState<ScheduleForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setForm(initialAgent ? { ...emptyForm(), agent: initialAgent } : emptyForm());
      setError(null);
    }
  }, [open, initialAgent]);

  const set = <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => setForm((prev) => ({ ...prev, [key]: value }));
  const assistant = form.agent === ASSISTANT;
  const { choices, failed } = useNoticeChannels(open, assistant ? null : form.agent);
  const choiceOf = (channel: NotifyChannel): NoticeChannelChoice | undefined =>
    choices?.find((choice) => choice.channel === channel) ??
    (channel === 'in_app' && choices === null ? { channel, available: true, reason: null } : undefined);

  // A channel that became unavailable (another agent, the org's setting) is not sent.
  useEffect(() => {
    if (choices === null) return;
    setForm((prev) => {
      const notify = prev.notify.filter((channel) => choices.some((c) => c.channel === channel && c.available));
      return notify.length === prev.notify.length ? prev : { ...prev, notify };
    });
  }, [choices]);
  const complete =
    form.name.trim() !== '' &&
    form.query.trim() !== '' &&
    (form.kind === 'webhook' || (form.cron.trim() !== '' && form.timeZone.trim() !== ''));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!complete || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(toNewSchedule(form));
    } catch (failure) {
      setError(refusalText(t, SCHEDULE_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure, true)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <Dialog.Content style={{ maxWidth: '36rem' }} aria-describedby={undefined}>
        <Dialog.Title>{t('schedules.create.title')}</Dialog.Title>
        <form onSubmit={(event) => void submit(event)}>
          <Flex direction="column" gap="3">
            <Field id={`${id}-name`} label={t('schedules.create.name')}>
              <TextField.Root id={`${id}-name`} value={form.name} maxLength={200} onChange={(e) => set('name', e.target.value)} />
            </Field>
            <Flex direction="column" gap="1">
              <Text as="label" id={`${id}-agent-label`} size="2" weight="medium">
                {t('schedules.create.agent')}
              </Text>
              <Select.Root value={form.agent} onValueChange={(value) => set('agent', value)}>
                <Select.Trigger aria-labelledby={`${id}-agent-label`} />
                <Select.Content>
                  <Select.Item value={ASSISTANT}>{t('schedules.assistant')}</Select.Item>
                  {agents.map((agent) => (
                    <Select.Item key={agent.id} value={agent.id}>
                      {agent.name}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
            </Flex>
            {form.agent !== ASSISTANT && (
              <Flex direction="column" gap="1">
                <Text as="span" id={`${id}-version-label`} size="2" weight="medium">
                  {t('schedules.version.label')}
                </Text>
                <RadioGroup.Root
                  aria-labelledby={`${id}-version-label`}
                  value={form.version}
                  onValueChange={(value) => set('version', value === 'latest' ? 'latest' : 'pinned')}
                >
                  <RadioGroup.Item value="pinned">{t('schedules.version.createPinned')}</RadioGroup.Item>
                  <RadioGroup.Item value="latest">{t('schedules.version.createLatest')}</RadioGroup.Item>
                </RadioGroup.Root>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {t(form.version === 'latest' ? 'schedules.version.latestHint' : 'schedules.version.pinnedHint')}
                </Text>
              </Flex>
            )}
            <Field id={`${id}-query`} label={t('schedules.create.prompt')} hint={t('schedules.create.promptHint')}>
              <TextArea id={`${id}-query`} value={form.query} maxLength={10000} rows={3} onChange={(e) => set('query', e.target.value)} />
            </Field>

            <Flex direction="column" gap="1">
              <Text as="span" id={`${id}-kind-label`} size="2" weight="medium">
                {t('schedules.create.when')}
              </Text>
              <RadioGroup.Root
                aria-labelledby={`${id}-kind-label`}
                value={form.kind}
                onValueChange={(value) => set('kind', value === 'webhook' ? 'webhook' : 'cron')}
              >
                <RadioGroup.Item value="cron">{t('schedules.create.kindCron')}</RadioGroup.Item>
                <RadioGroup.Item value="webhook">{t('schedules.create.kindWebhook')}</RadioGroup.Item>
              </RadioGroup.Root>
            </Flex>

            {form.kind === 'cron' ? (
              <>
                <Field id={`${id}-cron`} label={t('schedules.create.cron')} hint={t('schedules.create.cronHint')}>
                  <TextField.Root
                    id={`${id}-cron`}
                    value={form.cron}
                    placeholder="0 9 * * 1"
                    maxLength={120}
                    style={{ fontFamily: 'monospace' }}
                    onChange={(e) => set('cron', e.target.value)}
                  />
                </Field>
                <Field id={`${id}-zone`} label={t('schedules.create.timeZone')}>
                  <TextField.Root id={`${id}-zone`} value={form.timeZone} maxLength={64} onChange={(e) => set('timeZone', e.target.value)} />
                </Field>
                <Choice
                  id={`${id}-catchup`}
                  label={t('schedules.create.catchUp')}
                  value={form.catchUp}
                  options={CATCH_UP}
                  onChange={(value) => set('catchUp', value)}
                />
              </>
            ) : (
              <Flex direction="column" gap="2">
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t('schedules.create.webhookHint')}
                </Text>
                <Flex gap="2" align="start">
                  <Checkbox
                    id={`${id}-token`}
                    checked={form.tokenAuth}
                    onCheckedChange={(value) => set('tokenAuth', value === true)}
                    aria-describedby={`${id}-token-hint`}
                    style={{ marginTop: 3 }}
                  />
                  <Flex direction="column">
                    <Text as="label" htmlFor={`${id}-token`} size="2">
                      {t('schedules.create.webhookCantSign')}
                    </Text>
                    <Text id={`${id}-token-hint`} size="1" style={{ color: 'var(--slate-11)' }}>
                      {t('schedules.create.webhookCantSignHint')}
                    </Text>
                  </Flex>
                </Flex>
              </Flex>
            )}

            <Choice
              id={`${id}-overlap`}
              label={t('schedules.create.overlap')}
              value={form.overlap}
              options={OVERLAP}
              onChange={(value) => set('overlap', value)}
            />

            <Flex direction="column" gap="2" role="group" aria-labelledby={`${id}-notify-label`}>
              <Text as="span" id={`${id}-notify-label`} size="2" weight="medium">
                {t('schedules.create.notify')}
              </Text>
              {NOTIFY_CHANNELS.map((channel) => {
                const blocked = blockedKey(choiceOf(channel), assistant);
                const copy = CHANNEL_COPY[channel];
                return (
                  <Flex key={channel} gap="2" align="start" data-channel={channel} data-available={blocked === null}>
                    <Checkbox
                      id={`${id}-notify-${channel}`}
                      checked={blocked === null && form.notify.includes(channel)}
                      disabled={blocked !== null}
                      aria-describedby={`${id}-notify-${channel}-hint`}
                      onCheckedChange={(value) =>
                        set('notify', value === true ? [...form.notify, channel] : form.notify.filter((c) => c !== channel))
                      }
                      style={{ marginTop: 3 }}
                    />
                    <Flex direction="column">
                      <Text
                        as="label"
                        htmlFor={`${id}-notify-${channel}`}
                        size="2"
                        style={blocked === null ? undefined : { color: 'var(--slate-10)' }}
                      >
                        {t(copy.label)}
                      </Text>
                      <Text
                        id={`${id}-notify-${channel}-hint`}
                        size="1"
                        style={{ color: blocked === null ? 'var(--slate-11)' : 'var(--amber-11)' }}
                      >
                        {t(blocked ?? copy.hint)}
                      </Text>
                    </Flex>
                  </Flex>
                );
              })}
              {failed && (
                <Text size="1" role="note" style={{ color: 'var(--slate-11)' }}>
                  {t('schedules.create.notifyCheckFailed')}
                </Text>
              )}
            </Flex>

            <Flex gap="3" wrap="wrap">
              <Field id={`${id}-budget`} label={t('schedules.create.budget')} hint={t('schedules.create.budgetHint')}>
                <TextField.Root
                  id={`${id}-budget`}
                  type="number"
                  min="0"
                  step="0.01"
                  inputMode="decimal"
                  value={form.budget}
                  onChange={(e) => set('budget', e.target.value)}
                />
              </Field>
              <Field id={`${id}-deadline`} label={t('schedules.create.deadline')} hint={t('schedules.create.deadlineHint')}>
                <TextField.Root
                  id={`${id}-deadline`}
                  type="number"
                  min="0"
                  step="0.5"
                  inputMode="decimal"
                  value={form.deadlineHours}
                  onChange={(e) => set('deadlineHours', e.target.value)}
                />
              </Field>
            </Flex>

            {error && (
              <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
                {error}
              </Text>
            )}

            <Flex justify="end" gap="2" mt="2">
              <Dialog.Close>
                <Button type="button" variant="soft" color="gray" disabled={busy}>
                  {t('common.cancel')}
                </Button>
              </Dialog.Close>
              <LoadingButton type="submit" loading={busy} disabled={!complete}>
                {t('schedules.create.submit')}
              </LoadingButton>
            </Flex>
          </Flex>
        </form>
      </Dialog.Content>
    </Dialog.Root>
  );
}
