/** P8a test support: a schedule builder and fakes of the firer's ports that refuse what the real ones refuse. */
import type { RunStatus } from '../../../../src/modules/agent-harness/domain/execution';
import {
  type AgentSchedule,
  nextCronSlot,
  type ScheduleFiring,
  type ScheduleTrigger,
} from '../../../../src/modules/agent-harness/domain/schedules';
import type {
  ActorAuthority,
  ActorRefusal,
  RunEndedNotice,
  RunStatusReader,
  ScheduledAdmission,
  ScheduledAdmissionOutcome,
  ScheduledRunAdmission,
  ScheduledRunsSetting,
  ScheduleNotifier,
  ScheduleReach,
  TriggerSettings,
} from '../../../../src/modules/agent-harness/ports/schedules';
import type { NotifyChannel } from '../../../../src/modules/agent-harness/domain/schedules';
import type {
  RecordReadAccess,
  SourceReadQuery,
  SourceStatus,
} from '../../../../src/modules/agent-harness/ports/record-access';
import { sourceKey } from '../../../../src/modules/agent-harness/projection/citation-source';

/** The schedules' organisation and another, in the shape every store takes. */
export const ORG = '0000000000000000000000e1';
export const OTHER_ORG = '0000000000000000000000e2';
export const ALICE = 'alice';
export const BOB = 'bob';
export const ADMIN_USER = 'admin';
export const T0 = Date.parse('2026-07-06T06:00:00.000Z');
export const MONDAY_9_BERLIN: ScheduleTrigger = {
  kind: 'cron',
  cron: '0 9 * * 1',
  timeZone: 'Europe/Berlin',
  catchUp: 'skip',
};

let sequence = 0;

export function aSchedule(
  overrides: Partial<AgentSchedule> = {},
  now = T0,
): AgentSchedule {
  sequence += 1;
  const trigger = overrides.trigger ?? MONDAY_9_BERLIN;
  return {
    id: `sch-${String(sequence)}`,
    orgId: ORG,
    actorId: ALICE,
    name: 'Weekly support digest',
    agentKey: 'support-agent',
    input: {
      query: 'Summarise new support tickets to #support-leads',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'auto' },
    },
    trigger,
    deadlineS: 8 * 60 * 60,
    status: 'active',
    statusReason: null,
    nextFireAt:
      trigger.kind === 'cron'
        ? nextCronSlot(trigger.cron, trigger.timeZone, now)
        : null,
    webhookTokenHash: null,
    createdAt: now,
    updatedAt: now,
    ...overrides,
  };
}

export function aFiring(
  schedule: AgentSchedule,
  overrides: Partial<ScheduleFiring> = {},
): ScheduleFiring {
  sequence += 1;
  return {
    id: `fir-${String(sequence)}`,
    orgId: schedule.orgId,
    scheduleId: schedule.id,
    actorId: schedule.actorId,
    slotKey: `manual:${String(sequence)}`,
    slotAt: T0,
    kind: 'manual',
    status: 'pending',
    runId: null,
    sessionId: null,
    reason: null,
    missed: 0,
    attempts: 1,
    leaseUntil: T0 + 60_000,
    createdAt: T0,
    settledAt: null,
    watchUntil: null,
    notifiedAt: null,
    ...overrides,
  };
}

/** Users as the authority reads them now; tokens name the user so a test sees whose was used. */
export class FakeActors implements ActorAuthority {
  readonly refused = new Map<string, ActorRefusal>();
  readonly admins = new Set<string>([ADMIN_USER]);
  readonly minted: string[] = [];
  outage = false;

  async authorize(
    _orgId: string,
    actorId: string,
  ): Promise<{ authorization: string } | { refused: ActorRefusal }> {
    if (this.outage) throw new Error('user store down');
    const refused = this.refused.get(actorId);
    if (refused !== undefined) return { refused };
    this.minted.push(actorId);
    return { authorization: `Bearer token-of-${actorId}` };
  }

  async isOrgAdmin(_orgId: string, userId: string): Promise<boolean> {
    return this.admins.has(userId);
  }
}

export class FakeSetting implements ScheduledRunsSetting {
  on = true;
  async enabled(): Promise<boolean> {
    return this.on;
  }
}

/**
 * Admission as RunAdmissionService answers it: one run per idempotency key
 * (its receipt), the run acting as the request's actor and authorized by the
 * actor's own credential, else `agent_forbidden`.
 */
export class FakeAdmission implements ScheduledRunAdmission {
  readonly requests: ScheduledAdmission[] = [];
  readonly runs = new Map<string, { runId: string; sessionId: string }>();
  /** Reasons to answer, in order, before admitting. */
  readonly refusals: string[] = [];
  forbiddenActors = new Set<string>();
  delayMs = 0;

  get admitted(): number {
    return this.runs.size;
  }

  async admit(request: ScheduledAdmission): Promise<ScheduledAdmissionOutcome> {
    this.requests.push(structuredClone(request));
    if (this.delayMs > 0) await new Promise((r) => setTimeout(r, this.delayMs));
    const refusal = this.refusals.shift();
    if (refusal !== undefined) return { kind: 'refused', reason: refusal };
    if (request.authorization !== `Bearer token-of-${request.actorId}`) {
      return { kind: 'refused', reason: 'agent_forbidden' };
    }
    if (this.forbiddenActors.has(request.actorId)) {
      return { kind: 'refused', reason: 'agent_forbidden' };
    }
    const key = JSON.stringify([request.orgId, request.actorId, request.idempotencyKey]);
    let run = this.runs.get(key);
    if (run === undefined) {
      run = {
        runId: `run-${String(this.runs.size + 1)}`,
        sessionId: `ses-${String(this.runs.size + 1)}`,
      };
      this.runs.set(key, run);
    }
    return { kind: 'admitted', ...run };
  }
}

export class FakeRuns implements RunStatusReader {
  readonly statuses = new Map<string, RunStatus>();
  async status(_orgId: string, runId: string): Promise<RunStatus | null> {
    return this.statuses.get(runId) ?? 'running';
  }
}

export class FakeNotifier implements ScheduleNotifier {
  readonly notices: Array<Parameters<ScheduleNotifier['approvalWaiting']>[0]> = [];
  readonly refusals: Array<Parameters<ScheduleNotifier['firingRefused']>[0]> = [];
  async approvalWaiting(
    notice: Parameters<ScheduleNotifier['approvalWaiting']>[0],
  ): Promise<void> {
    this.notices.push(notice);
  }
  async firingRefused(
    notice: Parameters<ScheduleNotifier['firingRefused']>[0],
  ): Promise<void> {
    this.refusals.push(notice);
  }
  readonly ends: RunEndedNotice[] = [];
  async runEnded(notice: RunEndedNotice): Promise<void> {
    this.ends.push(structuredClone(notice));
  }
}

/** Record access as the authority answers it: per user (from the token), the records they may read. */
export class FakeRecordAccess implements RecordReadAccess {
  readonly readable = new Map<string, Set<string>>();
  readonly queries: SourceReadQuery[] = [];
  outage = false;
  async decide(query: SourceReadQuery): Promise<ReadonlyMap<string, SourceStatus>> {
    this.queries.push(structuredClone(query));
    if (this.outage) throw new Error('record authority down');
    const user = query.authorization.replace('Bearer token-of-', '');
    const mine = this.readable.get(user) ?? new Set<string>();
    return new Map(
      query.sources.map((source) => [
        sourceKey(source),
        'recordId' in source && mine.has(source.recordId) ? 'readable' : 'denied',
      ]),
    );
  }
}

/** The org's trigger settings as the policy store would give them. */
export class FakeTriggerSettings implements TriggerSettings {
  maxBytes = 262_144;
  perMinute = 30;
  channels: NotifyChannel[] = ['in_app', 'email', 'slack'];
  async webhookMaxBytes(): Promise<number> {
    return this.maxBytes;
  }
  async webhookPerMinute(): Promise<number> {
    return this.perMinute;
  }
  async notifyChannels(): Promise<readonly NotifyChannel[]> {
    return this.channels;
  }
}

/** A clock the in-memory store reads; tests move it. */
export class TestClock {
  constructor(public value = T0) {}
  now(): number {
    return this.value;
  }
  set(iso: string): void {
    this.value = Date.parse(iso);
  }
  advance(ms: number): void {
    this.value += ms;
  }
}

export interface RecordedLog {
  level: string;
  event: string;
  meta: Record<string, unknown>;
}

export function recordingLogger(lines: RecordedLog[]) {
  const record =
    (level: string) =>
    (event: string, meta?: Record<string, unknown>): void =>
      void lines.push({ level, event, meta: meta ?? {} });
  return {
    debug: record('debug'),
    info: record('info'),
    warn: record('warn'),
    error: record('error'),
  };
}

/** A reach that lets the org's admins see and act on everyone's schedules, as a build that offers it would. */
export const adminReach = (actors: Pick<ActorAuthority, 'isOrgAdmin'>): ScheduleReach => ({
  reachesOthers: (orgId, userId) => actors.isOrgAdmin(orgId, userId),
});
