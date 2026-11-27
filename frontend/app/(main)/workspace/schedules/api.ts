import { apiClient } from '@/lib/api';
import { uiLanguageHeader } from '@/lib/api/ui-language';
import { harnessCall, newIdempotencyKey } from '../harness-ui/refusal';
import { isObject, listOf, num, oneOf, QUIET, text } from '../harness-ui/pick';
import type {
  AgentVersionPin,
  CatchUpPolicy,
  ChatChange,
  CreatedSchedule,
  Firing,
  FiringKind,
  FiringStatus,
  NewSchedule,
  NewTrigger,
  NoticeChannelBlock,
  NoticeChannelChoice,
  NotifyChannel,
  OverlapPolicy,
  Schedule,
  SchedulePatch,
  ScheduleScope,
  ScheduleStatus,
  ScheduleTrigger,
} from './types';

const SCHEDULES = '/api/v1/agent-harness/schedules';
const STATUSES: readonly ScheduleStatus[] = ['active', 'paused', 'disabled'];
const CATCH_UP: readonly CatchUpPolicy[] = ['skip', 'run_latest', 'run_all'];
const OVERLAP: readonly OverlapPolicy[] = ['skip', 'queue', 'allow'];
export const NOTIFY_CHANNELS: readonly NotifyChannel[] = ['in_app', 'email', 'slack'];
const BLOCKS: readonly NoticeChannelBlock[] = ['org_off', 'no_slack_bot'];
const FIRING_KINDS: readonly FiringKind[] = ['cron', 'once', 'manual', 'event'];
const FIRING_STATUSES: readonly FiringStatus[] = ['pending', 'admitted', 'refused', 'skipped'];

function toTrigger(value: unknown): ScheduleTrigger | null {
  if (!isObject(value)) return null;
  if (value.kind === 'cron') {
    const cron = text(value.cron);
    const timeZone = text(value.timeZone);
    if (!cron || !timeZone) return null;
    return { kind: 'cron', cron, timeZone, catchUp: oneOf(value.catchUp, CATCH_UP) ?? 'skip' };
  }
  if (value.kind === 'once') {
    const at = text(value.at);
    const timeZone = text(value.timeZone);
    if (!at || Number.isNaN(Date.parse(at))) return null;
    return { kind: 'once', at, timeZone: timeZone ?? 'UTC' };
  }
  if (value.kind === 'webhook') {
    // A webhook made before signatures has no `auth`: it takes the token.
    return { kind: 'webhook', coalesceS: num(value.coalesceS) ?? 0, auth: value.auth === 'signature' ? 'signature' : 'token' };
  }
  if (value.kind === 'connector') {
    const connectorId = text(value.connectorId);
    return connectorId ? { kind: 'connector', connectorId, coalesceS: num(value.coalesceS) ?? 0 } : null;
  }
  return null;
}

function toPin(value: unknown): AgentVersionPin | null {
  if (value === 'latest') return 'latest';
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 ? value : null;
}

const toLive = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 1 ? value : null;

/** Field by field; the prompt only when the server sent it (to the schedule's actor). */
export function toSchedule(value: unknown): Schedule | null {
  if (!isObject(value)) return null;
  const id = text(value.id);
  const name = text(value.name);
  const actorId = text(value.actorId);
  const trigger = toTrigger(value.trigger);
  const status = oneOf(value.status, STATUSES);
  if (!id || !name || !actorId || !trigger || !status) return null;
  return {
    id,
    name,
    actorId,
    agentKey: text(value.agentKey),
    agentVersion: toPin(value.agentVersion),
    agentName: text(value.agentName),
    agentLiveVersion: toLive(value.agentLiveVersion),
    trigger,
    maxCostUsd: num(value.maxCostUsd),
    deadlineS: num(value.deadlineS) ?? 0,
    status,
    statusReason: text(value.statusReason),
    nextFireAt: text(value.nextFireAt),
    createdAt: text(value.createdAt) ?? '',
    query: isObject(value.input) ? text(value.input.query) : null,
    overlap: oneOf(value.overlap, OVERLAP) ?? 'skip',
    notify: Array.isArray(value.notify) ? NOTIFY_CHANNELS.filter((c) => (value.notify as unknown[]).includes(c)) : ['in_app'],
    lastChangedFrom: toChatChange(value.lastChangedFrom),
    skipNextAt: instant(value.skipNextAt),
    revision: typeof value.revision === 'number' && Number.isInteger(value.revision) && value.revision >= 0 ? value.revision : 0,
  };
}

function toChatChange(value: unknown): ChatChange | null {
  if (!isObject(value)) return null;
  const conversationId = text(value.conversationId);
  if (!conversationId) return null;
  return { conversationId, agentKey: text(value.agentKey), at: instant(value.at) };
}

const instant = (value: unknown): string | null => {
  const at = text(value);
  return at !== null && !Number.isNaN(Date.parse(at)) ? at : null;
};

export function toFiring(value: unknown): Firing | null {
  if (!isObject(value)) return null;
  const id = text(value.id);
  const kind = oneOf(value.kind, FIRING_KINDS);
  const status = oneOf(value.status, FIRING_STATUSES);
  const slotAt = text(value.slotAt);
  if (!id || !kind || !status || !slotAt) return null;
  return {
    id,
    kind,
    slotAt,
    status,
    reason: text(value.reason),
    runId: text(value.runId),
    conversationId: text(value.conversationId),
    runner: oneOf(value.runner, RUNNERS),
    costCapIgnored: value.costCapIgnored === true,
    missed: num(value.missed) ?? 0,
    attempts: num(value.attempts) ?? 0,
    createdAt: text(value.createdAt),
  };
}

const RUNNERS = ['harness', 'classic'] as const;

function toNoticeChannel(value: unknown): NoticeChannelChoice | null {
  if (!isObject(value)) return null;
  const channel = oneOf(value.channel, NOTIFY_CHANNELS);
  if (!channel) return null;
  const reason = oneOf(value.reason, BLOCKS);
  return { channel, available: value.available === true && reason === null, reason };
}

function triggerBody(trigger: NewTrigger): Record<string, unknown> {
  if (trigger.kind === 'cron') return { kind: 'cron', cron: trigger.cron, timeZone: trigger.timeZone, catchUp: trigger.catchUp };
  if (trigger.kind === 'once') return { kind: 'once', at: trigger.at, timeZone: trigger.timeZone };
  return { kind: 'webhook', auth: trigger.auth };
}

function createBody(input: NewSchedule): Record<string, unknown> {
  return {
    name: input.name,
    ...(input.agentKey ? { agentKey: input.agentKey } : {}),
    ...(input.agentKey && input.agentVersion ? { agentVersion: input.agentVersion } : {}),
    input: { query: input.query },
    trigger: triggerBody(input.trigger),
    ...(input.maxCostUsd !== null ? { maxCostUsd: input.maxCostUsd } : {}),
    ...(input.deadlineS !== null ? { deadlineS: input.deadlineS } : {}),
    overlap: input.overlap,
    notify: input.notify,
  };
}

const at = (id: string) => `${SCHEDULES}/${encodeURIComponent(id)}`;

const scheduleFrom = (data: unknown): Schedule => {
  const schedule = toSchedule(data);
  if (schedule === null) throw new Error('Unexpected schedule');
  return schedule;
};

export const SchedulesApi = {
  list: (scope: ScheduleScope) =>
    harnessCall(async () => {
      const { data } = await apiClient.get(SCHEDULES, { ...QUIET, params: { scope } });
      return listOf(isObject(data) ? data.schedules : null, toSchedule);
    }),

  create: (input: NewSchedule) =>
    harnessCall(async (): Promise<CreatedSchedule> => {
      // G-52: the schedule's runs answer in the language its owner uses PipesHub in (Node pins it).
      const { data } = await apiClient.post(SCHEDULES, createBody(input), { ...QUIET, headers: uiLanguageHeader() });
      const body = isObject(data) ? data : {};
      return {
        schedule: scheduleFrom(body.schedule),
        webhookToken: text(body.webhookToken),
        webhookSecret: text(body.webhookSecret),
      };
    }, true),

  /** One schedule of the person's, as it is now: a harness change's card reads it for its "before" (ADR-SCH-03 D5). */
  get: (id: string) => harnessCall(async () => scheduleFrom((await apiClient.get(at(id), QUIET)).data)),

  /** ADR-SCH-03 D4: an edit; refused `SCHEDULE_CHANGED` when the schedule moved past `revision` since it was read. */
  update: (id: string, patch: SchedulePatch) =>
    harnessCall(async () => {
      const body = {
        revision: patch.revision,
        ...(patch.name !== undefined ? { name: patch.name } : {}),
        ...(patch.query !== undefined ? { input: { query: patch.query } } : {}),
        ...(patch.trigger !== undefined ? { trigger: patch.trigger } : {}),
        ...(patch.notify !== undefined ? { notify: patch.notify } : {}),
      };
      return scheduleFrom((await apiClient.patch(at(id), body, QUIET)).data);
    }, true),

  pause: (id: string) =>
    harnessCall(async () => scheduleFrom((await apiClient.post(`${at(id)}/pause`, undefined, QUIET)).data), true),

  resume: (id: string) =>
    harnessCall(async () => scheduleFrom((await apiClient.post(`${at(id)}/resume`, undefined, QUIET)).data), true),

  remove: (id: string) =>
    harnessCall(async () => {
      await apiClient.delete(at(id), QUIET);
    }, true),

  /** OWD-14: move the schedule to another agent version, or to follow every publish. Only its own user. */
  setAgentVersion: (id: string, agentVersion: AgentVersionPin) =>
    harnessCall(
      async () => scheduleFrom((await apiClient.put(`${at(id)}/agent-version`, { agentVersion }, QUIET)).data),
      true,
    ),

  /** Each click is a new run: a fresh key. A retry of the same click would reuse it. */
  runNow: (id: string, idempotencyKey: string = newIdempotencyKey()) =>
    harnessCall(async () => {
      const { data } = await apiClient.post(`${at(id)}/run`, undefined, {
        ...QUIET,
        headers: { 'Idempotency-Key': idempotencyKey },
      });
      const firing = toFiring(data);
      if (firing === null) throw new Error('Unexpected firing');
      return firing;
    }, true),

  /** The notice channels a new schedule of this agent (null: the assistant) may use (TR-07). */
  noticeChannels: (agentKey: string | null) =>
    harnessCall(async (): Promise<NoticeChannelChoice[]> => {
      const { data } = await apiClient.get(`${SCHEDULES}/notify-channels`, {
        ...QUIET,
        params: agentKey === null ? {} : { agentKey },
      });
      return listOf(isObject(data) ? data.channels : null, toNoticeChannel);
    }),

  firings: (id: string) =>
    harnessCall(async () => {
      const { data } = await apiClient.get(`${at(id)}/firings`, QUIET);
      return listOf(isObject(data) ? data.firings : null, toFiring);
    }),
};
