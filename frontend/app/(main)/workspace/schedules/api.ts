import { apiClient } from '@/lib/api';
import { uiLanguageHeader } from '@/lib/api/ui-language';
import { harnessCall, newIdempotencyKey } from '../harness-ui/refusal';
import { isObject, listOf, num, oneOf, QUIET, text } from '../harness-ui/pick';
import type {
  AgentVersionPin,
  CatchUpPolicy,
  CreatedSchedule,
  Firing,
  FiringKind,
  FiringStatus,
  NewSchedule,
  NoticeChannelBlock,
  NoticeChannelChoice,
  NotifyChannel,
  OverlapPolicy,
  Schedule,
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
const FIRING_KINDS: readonly FiringKind[] = ['cron', 'manual', 'event'];
const FIRING_STATUSES: readonly FiringStatus[] = ['pending', 'admitted', 'refused', 'skipped'];

function toTrigger(value: unknown): ScheduleTrigger | null {
  if (!isObject(value)) return null;
  if (value.kind === 'cron') {
    const cron = text(value.cron);
    const timeZone = text(value.timeZone);
    if (!cron || !timeZone) return null;
    return { kind: 'cron', cron, timeZone, catchUp: oneOf(value.catchUp, CATCH_UP) ?? 'skip' };
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
  };
}

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
    missed: num(value.missed) ?? 0,
    attempts: num(value.attempts) ?? 0,
    createdAt: text(value.createdAt),
  };
}

function toNoticeChannel(value: unknown): NoticeChannelChoice | null {
  if (!isObject(value)) return null;
  const channel = oneOf(value.channel, NOTIFY_CHANNELS);
  if (!channel) return null;
  const reason = oneOf(value.reason, BLOCKS);
  return { channel, available: value.available === true && reason === null, reason };
}

function createBody(input: NewSchedule): Record<string, unknown> {
  return {
    name: input.name,
    ...(input.agentKey ? { agentKey: input.agentKey } : {}),
    ...(input.agentKey && input.agentVersion ? { agentVersion: input.agentVersion } : {}),
    input: { query: input.query },
    trigger:
      input.trigger.kind === 'cron'
        ? { kind: 'cron', cron: input.trigger.cron, timeZone: input.trigger.timeZone, catchUp: input.trigger.catchUp }
        : { kind: 'webhook', auth: input.trigger.auth },
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
