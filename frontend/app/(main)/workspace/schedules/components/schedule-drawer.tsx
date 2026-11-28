'use client';

import React, { useContext, useEffect, useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Checkbox, Flex, RadioGroup, Select, Text, TextArea, TextField } from '@radix-ui/themes';
import { selectAgentHarnessOn, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { ConfirmationDialog } from '../../components/confirmation-dialog';
import {
  useWorkspaceDrawerNestedModalHost,
  WorkspaceRightPanel,
  WorkspaceRightPanelBodyPortalContext,
} from '../../components/workspace-right-panel';
import { isHarnessError, refusalText, toHarnessError } from '../../harness-ui/refusal';
import { NOTIFY_CHANNELS, SchedulesApi } from '../api';
import { SCHEDULE_REFUSALS } from '../copy';
import { whenWords } from '../schedule-words';
import type {
  CatchUpPolicy,
  NewSchedule,
  NoticeChannelChoice,
  NotifyChannel,
  OverlapPolicy,
  Schedule,
  SchedulePatch,
} from '../types';

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
  kind: 'cron' | 'once' | 'webhook';
  cron: string;
  /** A one-off's time as the date-time field holds it: local to the browser, `YYYY-MM-DDTHH:mm`. */
  onceAt: string;
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
  onceAt: '',
  timeZone: browserTimeZone(),
  catchUp: 'skip',
  overlap: 'skip',
  notify: ['in_app'],
  tokenAuth: false,
  budget: '',
  deadlineHours: '',
  version: 'pinned',
});

/** ADR-SCH-01 D1: a one-off is made at least a minute and at most 366 days ahead. */
export const ONCE_MIN_AHEAD_MS = 60_000;
export const ONCE_MAX_AHEAD_MS = 366 * 86_400_000;

/** A date-time field's value (`YYYY-MM-DDTHH:mm`, the browser's zone) for an instant. */
export function localInputValue(at: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

export type OnceProblem = 'missing' | 'notExisting' | 'tooSoon' | 'tooFar';

/**
 * The one-off's instant from the field, or why it can't be made: blank, a time the clocks skip (a DST gap, which
 * `Date` would move an hour on without a word), too soon or too far ahead.
 */
export function onceInstant(value: string, now: Date = new Date()): { at: string } | { problem: OnceProblem } {
  const fields = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?$/.exec(value.trim());
  if (fields === null) return { problem: 'missing' };
  const parsed = new Date(value.trim());
  if (Number.isNaN(parsed.getTime())) return { problem: 'missing' };
  const [year, month, day, hour, minute] = fields.slice(1).map(Number);
  const kept =
    parsed.getFullYear() === year &&
    parsed.getMonth() + 1 === month &&
    parsed.getDate() === day &&
    parsed.getHours() === hour &&
    parsed.getMinutes() === minute;
  if (!kept) return { problem: 'notExisting' };
  const ahead = parsed.getTime() - now.getTime();
  if (ahead < ONCE_MIN_AHEAD_MS) return { problem: 'tooSoon' };
  if (ahead > ONCE_MAX_AHEAD_MS) return { problem: 'tooFar' };
  return { at: parsed.toISOString() };
}

const ONCE_PROBLEM_KEYS: Readonly<Record<Exclude<OnceProblem, 'missing'>, string>> = {
  notExisting: 'schedules.create.onceNotExisting',
  tooSoon: 'schedules.create.onceTooSoon',
  tooFar: 'schedules.create.onceTooFar',
};

/** The form's trigger, or why a one-off's time can't be made, from one check against `now`. */
function triggerOf(form: ScheduleForm, now: Date): { trigger: NewSchedule['trigger'] } | { problem: OnceProblem } {
  if (form.kind === 'cron') {
    return { trigger: { kind: 'cron', cron: form.cron.trim(), timeZone: form.timeZone.trim(), catchUp: form.catchUp } };
  }
  if (form.kind === 'once') {
    const instant = onceInstant(form.onceAt, now);
    if ('problem' in instant) return instant;
    // The field is in the browser's zone, so that is the zone the schedule is read in.
    return { trigger: { kind: 'once', at: instant.at, timeZone: browserTimeZone() } };
  }
  return { trigger: { kind: 'webhook', auth: form.tokenAuth ? 'token' : 'signature' } };
}

/**
 * The form as the create route's body, checked against the clock once, at submit: a time that was a minute ahead
 * when picked may not be by then. A blank optional number is absent, not zero.
 */
export function checkedSchedule(form: ScheduleForm, now: Date = new Date()): { schedule: NewSchedule } | { problem: OnceProblem } {
  const when = triggerOf(form, now);
  if ('problem' in when) return when;
  return { schedule: scheduleBody(form, when.trigger) };
}

/** `checkedSchedule` for a form known to be valid; a one-off's time that is not throws, it is never sent blank. */
export function toNewSchedule(form: ScheduleForm, now: Date = new Date()): NewSchedule {
  const checked = checkedSchedule(form, now);
  if ('problem' in checked) throw new Error(`The one-off's time can't be made: ${checked.problem}`);
  return checked.schedule;
}

function scheduleBody(form: ScheduleForm, trigger: NewSchedule['trigger']): NewSchedule {
  const budget = form.budget.trim() === '' ? null : Number(form.budget);
  const hours = form.deadlineHours.trim() === '' ? null : Number(form.deadlineHours);
  return {
    name: form.name.trim(),
    agentKey: form.agent === ASSISTANT ? null : form.agent,
    ...(form.agent !== ASSISTANT && form.version === 'latest' ? { agentVersion: 'latest' as const } : {}),
    query: form.query,
    trigger,
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

/** The form as a schedule fills it, for its edit (ADR-SCH-03 D4). */
export function formOf(schedule: Schedule): ScheduleForm {
  const trigger = schedule.trigger;
  return {
    ...emptyForm(),
    name: schedule.name,
    agent: schedule.agentKey ?? ASSISTANT,
    query: schedule.query ?? '',
    kind: trigger.kind === 'cron' || trigger.kind === 'once' ? trigger.kind : 'webhook',
    cron: trigger.kind === 'cron' ? trigger.cron : '',
    onceAt: trigger.kind === 'once' ? localInputValue(new Date(trigger.at)) : '',
    timeZone: trigger.kind === 'cron' ? trigger.timeZone : browserTimeZone(),
    catchUp: trigger.kind === 'cron' ? trigger.catchUp : 'skip',
    overlap: schedule.overlap,
    notify: [...schedule.notify],
    tokenAuth: trigger.kind === 'webhook' && trigger.auth === 'token',
    budget: schedule.maxCostUsd === null ? '' : String(schedule.maxCostUsd),
    deadlineHours: schedule.deadlineS > 0 ? String(schedule.deadlineS / 3600) : '',
    version: schedule.agentVersion === 'latest' ? 'latest' : 'pinned',
  };
}

const sameForm = (a: ScheduleForm, b: ScheduleForm): boolean => JSON.stringify(a) === JSON.stringify(b);

/**
 * What an edit changes, as `PATCH /schedules/{id}` takes it: only the fields that differ from the schedule, a
 * changed one-off's time checked against `now` as a create's is. The agent, its version, the budget, the deadline,
 * overlap and catch-up are set when a schedule is made and are not part of an edit.
 */
export function checkedPatch(
  schedule: Schedule,
  form: ScheduleForm,
  now: Date = new Date(),
): { patch: SchedulePatch } | { problem: OnceProblem } {
  const before = formOf(schedule);
  const patch: SchedulePatch = { revision: schedule.revision };
  if (form.name.trim() !== before.name.trim()) patch.name = form.name.trim();
  if (form.query !== before.query) patch.query = form.query;
  if (form.kind === 'cron' && (before.kind !== 'cron' || form.cron.trim() !== before.cron.trim() || form.timeZone.trim() !== before.timeZone.trim())) {
    patch.trigger = { kind: 'cron', cron: form.cron.trim(), timeZone: form.timeZone.trim() };
  }
  if (form.kind === 'once' && (before.kind !== 'once' || form.onceAt !== before.onceAt)) {
    const instant = onceInstant(form.onceAt, now);
    if ('problem' in instant) return instant;
    patch.trigger = { kind: 'once', at: instant.at, timeZone: browserTimeZone() };
  }
  const notify = NOTIFY_CHANNELS.filter((channel) => form.notify.includes(channel));
  if (notify.join(',') !== NOTIFY_CHANNELS.filter((channel) => before.notify.includes(channel)).join(',')) patch.notify = notify;
  return { patch };
}

const changes = (patch: SchedulePatch): boolean => Object.keys(patch).some((key) => key !== 'revision');

/**
 * Making a schedule, and changing one (ADR-SCH-03 D4), in the workspace's side drawer: what runs (agent, prompt),
 * when (cron in a zone, once, or a webhook), its notices and its limits. Closing with unsaved changes asks first.
 */
export function ScheduleDrawer({
  open,
  onOpenChange,
  agents,
  initialAgent,
  schedule,
  onSubmit,
  onUpdate,
  onStale,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agents: AgentOption[];
  /** The agent chosen when the drawer opens (the agent card's "Add a schedule"); absent: the assistant. */
  initialAgent?: string;
  /** Present: the drawer edits this schedule, prefilled. */
  schedule?: Schedule;
  /** Rejects with a `HarnessError`; the drawer stays open and says why. */
  onSubmit: (input: NewSchedule) => Promise<void>;
  /** An edit's save. Rejects with a `HarnessError`; the drawer stays open and says why. */
  onUpdate?: (id: string, patch: SchedulePatch) => Promise<void>;
  /** The schedule as it is now, after an edit was refused because it changed (`SCHEDULE_CHANGED`); null: gone. */
  onStale?: (id: string) => Promise<Schedule | null>;
}) {
  const { t, i18n } = useTranslation();
  const id = useId();
  const editing = schedule !== undefined;
  const [base, setBase] = useState<Schedule | undefined>(schedule);
  const [initial, setInitial] = useState<ScheduleForm>(emptyForm);
  const [form, setForm] = useState<ScheduleForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  // A one-off's time found stale at submit; any change to the form asks again.
  const [submitProblem, setSubmitProblem] = useState<Exclude<OnceProblem, 'missing'> | null>(null);
  // ADR-SCH-02 D6: a cost cap is the harness's budget ledger; a classic run has none to hold it to.
  const costCap = useFeatureFlagsStore(selectAgentHarnessOn);
  const modalHost = useWorkspaceDrawerNestedModalHost(open);

  const start = (from: ScheduleForm) => {
    setInitial(from);
    setForm(from);
    setSubmitProblem(null);
  };

  useEffect(() => {
    if (!open) return;
    setBase(schedule);
    start(schedule ? formOf(schedule) : initialAgent ? { ...emptyForm(), agent: initialAgent } : emptyForm());
    setError(null);
    setConfirmClose(false);
  }, [open, initialAgent, schedule]);

  const set = <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => {
    setSubmitProblem(null);
    setForm((prev) => ({ ...prev, [key]: value }));
  };
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
  const dirty = !sameForm(form, initial);
  const once = form.kind === 'once' ? onceInstant(form.onceAt) : null;
  // An edit that leaves a one-off's time as it was does not check it again: only a changed time must be ahead.
  const onceKept = editing && initial.kind === 'once' && form.kind === 'once' && form.onceAt === initial.onceAt;
  const onceProblem =
    submitProblem ?? (once !== null && !onceKept && 'problem' in once && once.problem !== 'missing' ? once.problem : null);
  const complete =
    submitProblem === null &&
    form.name.trim() !== '' &&
    form.query.trim() !== '' &&
    (form.kind === 'webhook' ||
      (form.kind === 'once'
        ? onceKept || (once !== null && 'at' in once)
        : form.cron.trim() !== '' && form.timeZone.trim() !== '')) &&
    (!editing || dirty);
  const cronSaid = useMemo(
    () =>
      form.kind === 'cron' && form.cron.trim().split(/\s+/).length === 5
        ? whenWords(t, { kind: 'cron', cron: form.cron.trim(), timeZone: form.timeZone.trim() }, i18n.language)
        : null,
    [form.kind, form.cron, form.timeZone, t, i18n.language],
  );

  const requestClose = (next: boolean) => {
    if (next || busy || confirmClose) return;
    if (dirty) setConfirmClose(true);
    else onOpenChange(false);
  };

  const failWith = (failure: unknown) =>
    setError(refusalText(t, SCHEDULE_REFUSALS, isHarnessError(failure) ? failure : toHarnessError(failure, true)));

  const save = async () => {
    if (!complete || busy) return;
    const now = new Date();
    if (base !== undefined) {
      const checked = checkedPatch(base, form, now);
      if ('problem' in checked) {
        setSubmitProblem(checked.problem === 'missing' ? 'tooSoon' : checked.problem);
        return;
      }
      if (!changes(checked.patch) || onUpdate === undefined) return;
      setBusy(true);
      setError(null);
      try {
        await onUpdate(base.id, checked.patch);
      } catch (failure) {
        failWith(failure);
        // ADR-SCH-03 D5: it changed since it was read; show it as it is now, to be changed again from there.
        if (isHarnessError(failure) && failure.code === 'SCHEDULE_CHANGED' && onStale !== undefined) {
          const fresh = await onStale(base.id).catch(() => null);
          if (fresh !== null) {
            setBase(fresh);
            start(formOf(fresh));
          }
        }
      } finally {
        setBusy(false);
      }
      return;
    }
    const checked = checkedSchedule(costCap ? form : { ...form, budget: '' }, now);
    if ('problem' in checked) {
      setSubmitProblem(checked.problem === 'missing' ? 'tooSoon' : checked.problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(checked.schedule);
    } catch (failure) {
      failWith(failure);
    } finally {
      setBusy(false);
    }
  };

  const agentName = base?.agentKey ? (base.agentName ?? base.agentKey) : t('schedules.assistant');
  // An edit changes a cron's or a one-off's time, never a webhook's or a connector's trigger.
  const timed = !editing || initial.kind !== 'webhook';

  return (
    <>
      <WorkspaceRightPanel
        open={open}
        onOpenChange={requestClose}
        title={editing ? t('schedules.edit.title') : t('schedules.create.title')}
        icon="schedule"
        iconSize={20}
        primaryLabel={editing ? t('schedules.edit.submit') : t('schedules.create.submit')}
        secondaryLabel={t('common.cancel')}
        primaryDisabled={!complete}
        primaryLoading={busy}
        onPrimaryClick={() => void save()}
      >
        <form
          aria-label={editing ? t('schedules.edit.title') : t('schedules.create.title')}
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <Flex direction="column" gap="3">
            <Field id={`${id}-name`} label={t('schedules.create.name')}>
              <TextField.Root id={`${id}-name`} value={form.name} maxLength={200} onChange={(e) => set('name', e.target.value)} />
            </Field>
            {editing ? (
              <Field id={`${id}-agent`} label={t('schedules.create.agent')}>
                <Text id={`${id}-agent`} size="2">
                  {agentName}
                </Text>
              </Field>
            ) : (
              <AgentPicker id={id} value={form.agent} agents={agents} onChange={(value) => set('agent', value)} />
            )}
            {!editing && form.agent !== ASSISTANT && (
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

            {timed && (
              <Flex direction="column" gap="1">
                <Text as="span" id={`${id}-kind-label`} size="2" weight="medium">
                  {t('schedules.create.when')}
                </Text>
                <RadioGroup.Root
                  aria-labelledby={`${id}-kind-label`}
                  value={form.kind}
                  onValueChange={(value) => set('kind', value === 'webhook' || value === 'once' ? value : 'cron')}
                >
                  <RadioGroup.Item value="cron">{t('schedules.create.kindCron')}</RadioGroup.Item>
                  <RadioGroup.Item value="once">{t('schedules.create.kindOnce')}</RadioGroup.Item>
                  {!editing && <RadioGroup.Item value="webhook">{t('schedules.create.kindWebhook')}</RadioGroup.Item>}
                </RadioGroup.Root>
              </Flex>
            )}

            {form.kind === 'cron' ? (
              <>
                <Field id={`${id}-cron`} label={t('schedules.create.cron')} hint={t('schedules.create.cronHint')}>
                  <TextField.Root
                    id={`${id}-cron`}
                    value={form.cron}
                    placeholder="0 9 * * 1"
                    maxLength={120}
                    style={{ fontFamily: 'monospace' }}
                    aria-describedby={cronSaid !== null ? `${id}-cron-said` : undefined}
                    onChange={(e) => set('cron', e.target.value)}
                  />
                  {cronSaid !== null && (
                    <Text id={`${id}-cron-said`} size="1" data-testid="schedule-cron-said" style={{ color: 'var(--slate-12)' }}>
                      {cronSaid}
                    </Text>
                  )}
                </Field>
                <Field id={`${id}-zone`} label={t('schedules.create.timeZone')}>
                  <TextField.Root id={`${id}-zone`} value={form.timeZone} maxLength={64} onChange={(e) => set('timeZone', e.target.value)} />
                </Field>
                {!editing && (
                  <Choice
                    id={`${id}-catchup`}
                    label={t('schedules.create.catchUp')}
                    value={form.catchUp}
                    options={CATCH_UP}
                    onChange={(value) => set('catchUp', value)}
                  />
                )}
              </>
            ) : form.kind === 'once' ? (
              <Field
                id={`${id}-once`}
                label={t('schedules.create.onceAt')}
                hint={t('schedules.create.onceAtHint', { timeZone: browserTimeZone() })}
              >
                {/* No native min or max: `onceInstant` alone bounds the time, against the clock when it checks, so a
                    bound fixed when the drawer opened can never block a time it accepts. */}
                <TextField.Root
                  id={`${id}-once`}
                  type="datetime-local"
                  value={form.onceAt}
                  aria-invalid={onceProblem !== null}
                  onChange={(e) => set('onceAt', e.target.value)}
                />
                {onceProblem !== null && (
                  <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
                    {t(ONCE_PROBLEM_KEYS[onceProblem])}
                  </Text>
                )}
              </Field>
            ) : editing ? null : (
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

            {!editing && form.kind !== 'once' && (
              <Choice
                id={`${id}-overlap`}
                label={t('schedules.create.overlap')}
                value={form.overlap}
                options={OVERLAP}
                onChange={(value) => set('overlap', value)}
              />
            )}

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

            {editing ? (
              <Text size="1" role="note" data-testid="schedule-edit-fixed" style={{ color: 'var(--slate-11)' }}>
                {t('schedules.edit.fixedHint')}
              </Text>
            ) : (
              <Flex gap="3" wrap="wrap">
                {costCap && (
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
                )}
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
            )}

            {error && (
              <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
                {error}
              </Text>
            )}
            {/* Enter in a field saves, as the footer's button does. */}
            <button type="submit" hidden aria-hidden tabIndex={-1} />
          </Flex>
        </form>
      </WorkspaceRightPanel>
      <ConfirmationDialog
        open={confirmClose}
        onOpenChange={setConfirmClose}
        title={editing ? t('schedules.edit.discardTitle') : t('schedules.create.discardTitle')}
        message={t('schedules.create.discardMessage')}
        confirmLabel={t('schedules.create.discardConfirm')}
        cancelLabel={t('schedules.create.keepEditing')}
        container={modalHost}
        onConfirm={() => {
          setConfirmClose(false);
          onOpenChange(false);
        }}
      />
    </>
  );
}

/** The agent a new schedule runs; its menu opens inside the drawer so it stacks above it. */
function AgentPicker({
  id,
  value,
  agents,
  onChange,
}: {
  id: string;
  value: string;
  agents: AgentOption[];
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const container = useContext(WorkspaceRightPanelBodyPortalContext) ?? undefined;
  return (
    <Flex direction="column" gap="1">
      <Text as="label" id={`${id}-agent-label`} size="2" weight="medium">
        {t('schedules.create.agent')}
      </Text>
      <Select.Root value={value} onValueChange={onChange}>
        <Select.Trigger aria-labelledby={`${id}-agent-label`} />
        <Select.Content container={container}>
          <Select.Item value={ASSISTANT}>{t('schedules.assistant')}</Select.Item>
          {agents.map((agent) => (
            <Select.Item key={agent.id} value={agent.id}>
              {agent.name}
            </Select.Item>
          ))}
        </Select.Content>
      </Select.Root>
    </Flex>
  );
}
