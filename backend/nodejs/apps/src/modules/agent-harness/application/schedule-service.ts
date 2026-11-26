import { HarnessRolloutPolicy } from '../ports/harness-rollout';
import {
  createHash,
  randomBytes,
  randomUUID,
  timingSafeEqual,
} from 'node:crypto';
import {
  actorRefOfUser,
  changedPaths,
  ConfigChange,
  ControlPlaneTransaction,
  userScope,
} from '../../control-plane';
import { canonicalDigest } from '../contracts/canonical-json';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import {
  HarnessScheduleRefusedError,
  HarnessTriggerRefusedError,
} from '../domain/errors';
import type { RunInputSnapshot } from '../domain/execution';
import type { RunVia } from '../domain/run-spec';
import { usdMicros } from '../domain/budget-ledger';
import type { RunBudgetCeiling } from '../ports/cost-ledger';
import type { AgentSummary, AgentSummaryReader } from '../ports/agent-names';
import {
  type AgentSchedule,
  type AgentVersionPin,
  assertCron,
  assertScheduleBudget,
  type CatchUpPolicy,
  DEFAULT_COALESCE_S,
  DEFAULT_DEADLINE_S,
  DEFAULT_NOTIFY,
  DEFAULT_OVERLAP,
  MAX_COALESCE_S,
  MAX_DEADLINE_S,
  MAX_SCHEDULES_PER_ACTOR,
  MAX_SCHEDULES_PER_ORG,
  MIN_COALESCE_S,
  MIN_DEADLINE_S,
  nextCronSlot,
  NOTIFY_CHANNELS,
  type NotifyChannel,
  type OverlapPolicy,
  scheduleAgentVersion,
  type ScheduleFiring,
  scheduleNotify,
  scheduleOverlap,
  type ScheduleStatus,
  type ScheduleTrigger,
  signatureMatches,
  WEBHOOK_SIGNATURE_TOLERANCE_S,
  webhookAuth,
  type WebhookAuth,
  webhookSignature,
} from '../domain/schedules';
import type {
  ActorAuthority,
  AgentSlackBots,
  AgentVersionResolver,
  NoticeChannelChoice,
  ScheduledRunsSetting,
  ScheduleReach,
  ScheduleStore,
  TriggerSettings,
} from '../ports/schedules';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import type { ScheduleFirer } from './schedule-firer';

export interface ScheduleCaller {
  orgId: string;
  userId: string;
  via: RunVia;
  /** G-52: the caller's locale (their request's `Accept-Language`); a schedule they make pins it for its runs. */
  locale?: string;
}

/** The run input a schedule fixes: no records, attachments or history (they would go stale). */
export interface ScheduleTemplate {
  query: string;
  chatMode?: string;
  tools?: string[];
  agentCapabilities?: RunInputSnapshot['agentCapabilities'];
  modelKey?: string;
  modelName?: string;
  modelProvider?: string;
  modelFriendlyName?: string;
  reasoningEffort?: string;
  modelAccountId?: string;
}

export type ScheduleTriggerRequest =
  | { kind: 'cron'; cron: string; timeZone: string; catchUp?: CatchUpPolicy }
  | { kind: 'webhook'; coalesceS?: number; auth?: WebhookAuth }
  | { kind: 'connector'; connectorId: string; coalesceS?: number };

export interface CreateScheduleRequest {
  name: string;
  agentKey?: string;
  /** A published version of the agent, or `latest` to follow every publish; absent: the latest one now, pinned. */
  agentVersion?: AgentVersionPin;
  input: ScheduleTemplate;
  trigger: ScheduleTriggerRequest;
  maxCostUsd?: number;
  deadlineS?: number;
  /** While an earlier run is active (TR-06); absent: `skip`. */
  overlap?: OverlapPolicy;
  /** Where the actor hears that a run ended (TR-07); absent: in-app. */
  notify?: NotifyChannel[];
}

/** A webhook request as the route read it: the raw body is what a signature covers. */
export interface WebhookDelivery {
  token?: string;
  signature?: string;
  timestamp?: string;
  body: Buffer;
  contentType?: string;
}

export interface ScheduleView {
  id: string;
  name: string;
  actorId: string;
  agentKey: string | null;
  /** The agent version its firings run (OWD-14); null for the assistant. */
  agentVersion: AgentVersionPin | null;
  trigger: ScheduleTrigger;
  maxCostUsd: number | null;
  deadlineS: number;
  status: ScheduleStatus;
  statusReason: string | null;
  nextFireAt: string | null;
  overlap: OverlapPolicy;
  notify: NotifyChannel[];
  createdAt: string;
  updatedAt: string;
  /** The template; only its actor sees it (it is their prompt). */
  input?: ScheduleTemplate;
}

/**
 * A schedule as the list answers it (ADR-UI-03): with its agent's name and the
 * version live now, from one batched lookup per list, so the page asks
 * nothing per row. Both null for the assistant, an agent nobody can name now,
 * or when the lookup failed.
 */
export interface ScheduleListItem extends ScheduleView {
  agentName: string | null;
  /** The agent's latest published version: a pinned schedule below it offers the move. */
  agentLiveVersion: number | null;
}

export interface FiringView {
  id: string;
  kind: ScheduleFiring['kind'];
  slotAt: string;
  status: ScheduleFiring['status'];
  reason: string | null;
  runId: string | null;
  conversationId: string | null;
  missed: number;
  attempts: number;
  createdAt: string;
  settledAt: string | null;
}

const iso = (at: number | null): string | null =>
  at === null ? null : new Date(at).toISOString();

export const hashTriggerToken = (token: string): string =>
  createHash('sha256').update(token, 'utf8').digest('hex');

/** What a schedule's audit record digests and diffs: its settings, never its input or webhook secret. */
type ScheduleSettings = Pick<
  AgentSchedule,
  'name' | 'trigger' | 'deadlineS' | 'status'
> & {
  agentKey: string | null;
  agentVersion: AgentVersionPin | null;
  maxCostUsd: number | null;
  overlap: OverlapPolicy;
  notify: NotifyChannel[];
};

const scheduleSettings = (
  schedule: AgentSchedule | null,
): ScheduleSettings | null =>
  schedule === null
    ? null
    : {
        name: schedule.name,
        agentKey: schedule.agentKey ?? null,
        agentVersion: scheduleAgentVersion(schedule),
        trigger: schedule.trigger,
        maxCostUsd: schedule.maxCostUsd ?? null,
        deadlineS: schedule.deadlineS,
        status: schedule.status,
        overlap: scheduleOverlap(schedule),
        notify: scheduleNotify(schedule),
      };

function scheduleChange(
  schedule: AgentSchedule,
  before: AgentSchedule | null,
  after: AgentSchedule | null,
): ConfigChange {
  const settings = scheduleSettings(after);
  return {
    scope: userScope(schedule.actorId),
    object: { type: 'schedule', id: schedule.id },
    revisionFrom: null,
    revisionTo: null,
    changeDigest: canonicalDigest(settings),
    changed: changedPaths(scheduleSettings(before), settings),
  };
}

const notFound = (): HarnessScheduleRefusedError =>
  new HarnessScheduleRefusedError('SCHEDULE_NOT_FOUND', 'Schedule not found');
// Lost a race with another change (or a deletion): nothing changed, nothing is audited; the caller retries.
const found = (schedule: AgentSchedule | null): AgentSchedule => {
  if (schedule === null) throw notFound();
  return schedule;
};
const forbidden = (message: string): HarnessScheduleRefusedError =>
  new HarnessScheduleRefusedError('SCHEDULE_FORBIDDEN', message);
const invalidVersion = (message: string): HarnessScheduleRefusedError =>
  new HarnessScheduleRefusedError('SCHEDULE_INVALID', message, 'agentVersion');
const badToken = (): HarnessScheduleRefusedError =>
  new HarnessScheduleRefusedError(
    'SCHEDULE_TRIGGER_TOKEN_INVALID',
    'The trigger credential is not valid',
  );

/** Whole minutes: the window a webhook's deliveries are counted in (TR-04). */
const RATE_WINDOW_MS = 60_000;

/** The run input a template fixes; `locale`: its owner's (G-52), which every firing answers in. */
function snapshot(
  template: ScheduleTemplate,
  locale?: string,
): RunInputSnapshot {
  return {
    query: template.query,
    ...(locale !== undefined ? { locale } : {}),
    recordIds: [],
    filters: {},
    attachments: [],
    ...(template.tools !== undefined ? { tools: [...template.tools] } : {}),
    ...(template.agentCapabilities !== undefined
      ? { agentCapabilities: { ...template.agentCapabilities } }
      : {}),
    model: {
      ...(template.modelKey !== undefined
        ? { modelKey: template.modelKey }
        : {}),
      ...(template.modelName !== undefined
        ? { modelName: template.modelName }
        : {}),
      ...(template.modelProvider !== undefined
        ? { modelProvider: template.modelProvider }
        : {}),
      ...(template.modelFriendlyName !== undefined
        ? { modelFriendlyName: template.modelFriendlyName }
        : {}),
      ...(template.reasoningEffort !== undefined
        ? { reasoningEffort: template.reasoningEffort }
        : {}),
      ...(template.modelAccountId !== undefined
        ? { accountId: template.modelAccountId }
        : {}),
      chatMode: template.chatMode ?? 'auto',
    },
  };
}

function template(input: RunInputSnapshot): ScheduleTemplate {
  const model = input.model;
  return {
    query: input.query,
    chatMode: model.chatMode,
    ...(input.tools !== undefined ? { tools: input.tools } : {}),
    ...(input.agentCapabilities !== undefined
      ? { agentCapabilities: input.agentCapabilities }
      : {}),
    ...(model.modelKey !== undefined ? { modelKey: model.modelKey } : {}),
    ...(model.modelName !== undefined ? { modelName: model.modelName } : {}),
    ...(model.modelProvider !== undefined
      ? { modelProvider: model.modelProvider }
      : {}),
    ...(model.modelFriendlyName !== undefined
      ? { modelFriendlyName: model.modelFriendlyName }
      : {}),
    ...(model.reasoningEffort !== undefined
      ? { reasoningEffort: model.reasoningEffort }
      : {}),
    ...(model.accountId !== undefined
      ? { modelAccountId: model.accountId }
      : {}),
  };
}

export function scheduleView(
  schedule: AgentSchedule,
  viewerId: string,
): ScheduleView {
  return {
    id: schedule.id,
    name: schedule.name,
    actorId: schedule.actorId,
    agentKey: schedule.agentKey ?? null,
    agentVersion: scheduleAgentVersion(schedule),
    trigger: structuredClone(schedule.trigger),
    maxCostUsd: schedule.maxCostUsd ?? null,
    deadlineS: schedule.deadlineS,
    status: schedule.status,
    statusReason: schedule.statusReason,
    nextFireAt: iso(schedule.nextFireAt),
    overlap: scheduleOverlap(schedule),
    notify: scheduleNotify(schedule),
    createdAt: iso(schedule.createdAt) as string,
    updatedAt: iso(schedule.updatedAt) as string,
    ...(viewerId === schedule.actorId && schedule.input !== null
      ? { input: template(schedule.input) }
      : {}),
  };
}

export const firingView = (firing: ScheduleFiring): FiringView => ({
  id: firing.id,
  kind: firing.kind,
  slotAt: iso(firing.slotAt) as string,
  status: firing.status,
  reason: firing.reason,
  runId: firing.runId,
  conversationId: firing.sessionId,
  missed: firing.missed,
  attempts: firing.attempts,
  createdAt: iso(firing.createdAt) as string,
  settledAt: iso(firing.settledAt),
});

const bounded = (
  value: number | undefined,
  fallback: number,
  min: number,
  max: number,
  field: string,
): number => {
  const chosen = value ?? fallback;
  if (!Number.isInteger(chosen) || chosen < min || chosen > max) {
    throw new HarnessScheduleRefusedError(
      'SCHEDULE_INVALID',
      `${field} must be an integer from ${String(min)} to ${String(max)}`,
      field,
    );
  }
  return chosen;
};

export interface ScheduleServiceOptions {
  store: ScheduleStore;
  firer: Pick<ScheduleFirer, 'runNow' | 'deliver'>;
  actors: Pick<ActorAuthority, 'isOrgAdmin'>;
  setting: ScheduledRunsSetting;
  /** Creates, pauses, resumes and deletions are audited control-plane writes (CPD-1). */
  transaction: ControlPlaneTransaction;
  /**
   * Which agent version a schedule pins (OWD-14). Absent: a schedule pins
   * what its request names, else follows the latest.
   */
  agentVersions?: AgentVersionResolver;
  /**
   * The org's durable rollout (UX3, R1 F14): every firing is a durable run, so
   * with the harness off a schedule would never run. Absent: not checked.
   */
  rollout?: Pick<HarnessRolloutPolicy, 'decideForOrg'>;
  /** Webhook limits and notice channels (TR-04, TR-07). Absent: the release's. */
  triggers?: TriggerSettings;
  /** Which agents have a Slack bot (TR-07). Absent: none has, so Slack is never offered. */
  slackBots?: AgentSlackBots;
  /**
   * The most a schedule's run may budget beyond the release ceiling (UX-07): a budget per run above it is refused on
   * create. Absent: only the release ceiling is checked.
   */
  budgets?: RunBudgetCeiling | null;
  /** The list's agent names and live versions (ADR-UI-03). Absent: listed without them. */
  agents?: AgentSummaryReader | null;
  /** Who may see and act on schedules other people own. Absent: each schedule only by its owner. */
  reach?: ScheduleReach | null;
  logger?: HarnessLogger;
  newId?: () => string;
  newToken?: () => string;
  /** A signed webhook's secret; shown once at creation (TR-04). */
  newSecret?: () => string;
}

/**
 * Who may do what with a schedule (P8a): its actor everything; an org admin
 * may list, read, pause, resume and delete the org's, but never run one now
 * (a run acts as the actor, REPORT 6.12). Anyone else is told it does not exist.
 */
export class ScheduleService {
  private readonly store: ScheduleStore;
  private readonly firer: Pick<ScheduleFirer, 'runNow' | 'deliver'>;
  private readonly actors: Pick<ActorAuthority, 'isOrgAdmin'>;
  private readonly setting: ScheduledRunsSetting;
  private readonly transaction: ControlPlaneTransaction;
  private readonly rollout?: Pick<HarnessRolloutPolicy, 'decideForOrg'>;
  private readonly agentVersions?: AgentVersionResolver;
  private readonly triggers?: TriggerSettings;
  private readonly slackBots?: AgentSlackBots;
  private readonly budgets: RunBudgetCeiling | null;
  private readonly agents: AgentSummaryReader | null;

  private readonly reach: ScheduleReach | null;
  private readonly logger: HarnessLogger;
  private readonly newId: () => string;
  private readonly newToken: () => string;
  private readonly newSecret: () => string;

  constructor(options: ScheduleServiceOptions) {
    this.store = options.store;
    this.firer = options.firer;
    this.actors = options.actors;
    this.setting = options.setting;
    this.transaction = options.transaction;
    this.rollout = options.rollout;
    this.agentVersions = options.agentVersions;
    this.logger = options.logger ?? silentHarnessLogger;
    this.newId = options.newId ?? randomUUID;
    this.newToken =
      options.newToken ??
      (() => `pxt_${randomBytes(32).toString('base64url')}`);
    this.newSecret =
      options.newSecret ??
      (() => `pxs_${randomBytes(32).toString('base64url')}`);
    this.triggers = options.triggers;
    this.slackBots = options.slackBots;
    this.budgets = options.budgets ?? null;
    this.agents = options.agents ?? null;
    this.reach = options.reach ?? null;
  }

  /**
   * The notice channels the caller's schedule of `agentKey` (absent: the
   * assistant) may pick (TR-07): each one the org allows, and Slack only when
   * that agent has its own bot, since only that bot sends it. An agent the
   * caller may not run is answered as not found, so the route never says
   * whether someone else's agent has a bot.
   */
  async noticeChannels(
    caller: ScheduleCaller,
    agentKey: string | undefined,
  ): Promise<NoticeChannelChoice[]> {
    await this.assertEnabled(caller.orgId);
    if (
      agentKey !== undefined &&
      (this.agentVersions === undefined ||
        (await this.agentVersions.resolve(
          caller.orgId,
          caller.userId,
          agentKey,
        )) === null)
    ) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_NOT_FOUND',
        'Agent not found',
      );
    }
    const allowed =
      this.triggers === undefined
        ? NOTIFY_CHANNELS
        : await this.triggers.notifyChannels(caller.orgId);
    const slackBot =
      agentKey !== undefined &&
      this.slackBots !== undefined &&
      (await this.slackBots.hasBot(agentKey));
    return NOTIFY_CHANNELS.map((channel) => {
      const reason = !allowed.includes(channel)
        ? 'org_off'
        : channel === 'slack' && !slackBot
          ? 'no_slack_bot'
          : null;
      return { channel, available: reason === null, reason };
    });
  }

  async create(
    caller: ScheduleCaller,
    request: CreateScheduleRequest,
  ): Promise<{
    schedule: ScheduleView;
    webhookToken?: string;
    webhookSecret?: string;
  }> {
    await this.assertEnabled(caller.orgId);
    await this.assertRunnable(caller.orgId);
    if (caller.via === 'service') {
      throw forbidden(
        'A schedule runs as a person; service accounts cannot own one',
      );
    }
    if (
      request.trigger.kind === 'connector' &&
      !(await this.actors.isOrgAdmin(caller.orgId, caller.userId))
    ) {
      throw forbidden('Only org admins can trigger runs from connector events');
    }
    const now = await this.store.now();
    assertScheduleBudget(request.maxCostUsd);
    const deadlineS = bounded(
      request.deadlineS,
      DEFAULT_DEADLINE_S,
      MIN_DEADLINE_S,
      MAX_DEADLINE_S,
      'deadlineS',
    );
    const trigger = this.trigger(request.trigger, now);
    const notify = await this.notifyChannels(caller.orgId, request.notify);
    const agentVersion = await this.pinned(
      caller,
      request.agentKey,
      request.agentVersion,
    );
    // After the agent check: one who may not run the agent must not learn its budget.
    await this.assertWithinBudgetCeiling(caller, request);
    const signed =
      trigger.kind === 'webhook' && webhookAuth(trigger) === 'signature';
    const token =
      trigger.kind === 'webhook' && !signed ? this.newToken() : null;
    const secret = signed ? this.newSecret() : null;
    const schedule: AgentSchedule = {
      id: this.newId(),
      orgId: caller.orgId,
      actorId: caller.userId,
      name: request.name,
      ...(request.agentKey !== undefined ? { agentKey: request.agentKey } : {}),
      ...(agentVersion !== undefined ? { agentVersion } : {}),
      input: snapshot(request.input, caller.locale),
      trigger,
      ...(request.maxCostUsd !== undefined
        ? { maxCostUsd: request.maxCostUsd }
        : {}),
      deadlineS,
      status: 'active',
      statusReason: null,
      nextFireAt:
        trigger.kind === 'cron'
          ? nextCronSlot(trigger.cron, trigger.timeZone, now)
          : null,
      webhookTokenHash: token === null ? null : hashTriggerToken(token),
      ...(secret !== null ? { webhookSigningKey: secret } : {}),
      overlap: request.overlap ?? DEFAULT_OVERLAP,
      notify,
      createdAt: now,
      updatedAt: now,
    };
    const created = await this.audited(caller, schedule, null, schedule, () =>
      this.store.create(schedule, {
        perActor: MAX_SCHEDULES_PER_ACTOR,
        perOrg: MAX_SCHEDULES_PER_ORG,
      }),
    );
    this.logger.info(
      'schedule.created',
      harnessLogMeta('schedule.created', {
        orgId: caller.orgId,
        actorId: caller.userId,
        scheduleId: created.id,
        agentKey: created.agentKey,
        agentVersion: created.agentVersion,
        trigger: created.trigger.kind,
        maxCostUsd: created.maxCostUsd,
        deadlineS,
        overlap: scheduleOverlap(created),
        notify: notify.join(','),
      }),
    );
    return {
      schedule: scheduleView(created, caller.userId),
      ...(token !== null ? { webhookToken: token } : {}),
      ...(secret !== null ? { webhookSecret: secret } : {}),
    };
  }

  /** The caller's own schedules; `everyone`, the org's, for a caller the reach lets see others' schedules. */
  async list(
    caller: ScheduleCaller,
    limit: number,
    everyone = false,
  ): Promise<ScheduleListItem[]> {
    await this.assertEnabled(caller.orgId);
    if (everyone && !(await this.reachesOthers(caller))) {
      throw forbidden("Only the schedules' owners can list them");
    }
    const found = await this.store.list(
      caller.orgId,
      everyone ? {} : { actorId: caller.userId },
      limit,
    );
    const agents = await this.agentSummaries(caller.orgId, found);
    return found.map((s) => {
      const agent =
        s.agentKey !== undefined ? agents.get(s.agentKey) : undefined;
      return {
        ...scheduleView(s, caller.userId),
        agentName: agent?.name ?? null,
        agentLiveVersion: agent?.latestVersion ?? null,
      };
    });
  }

  /** One lookup for every agent the page of schedules holds; a failure lists them unnamed, never fails the list. */
  private async agentSummaries(
    orgId: string,
    schedules: readonly AgentSchedule[],
  ): Promise<ReadonlyMap<string, AgentSummary>> {
    const keys = [
      ...new Set(
        schedules.flatMap((s) =>
          s.agentKey !== undefined ? [s.agentKey] : [],
        ),
      ),
    ];
    if (this.agents === null || keys.length === 0) return new Map();
    try {
      return await this.agents.summaries(orgId, keys);
    } catch (error) {
      this.logger.warn(
        'schedule.agent_names_failed',
        harnessLogMeta('schedule.agent_names_failed', {
          orgId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return new Map();
    }
  }

  async get(caller: ScheduleCaller, id: string): Promise<ScheduleView> {
    await this.assertEnabled(caller.orgId);
    const { schedule } = await this.manageable(caller, id);
    return scheduleView(schedule, caller.userId);
  }

  async firings(
    caller: ScheduleCaller,
    id: string,
    limit: number,
  ): Promise<FiringView[]> {
    await this.assertEnabled(caller.orgId);
    await this.manageable(caller, id);
    return (await this.store.firings(caller.orgId, id, limit)).map(firingView);
  }

  async pause(caller: ScheduleCaller, id: string): Promise<ScheduleView> {
    await this.assertEnabled(caller.orgId);
    const { schedule, asActor } = await this.manageable(caller, id);
    if (schedule.status === 'paused')
      return scheduleView(schedule, caller.userId);
    if (schedule.status === 'disabled') throw this.disabled();
    const now = await this.store.now();
    const changed = await this.audited(
      caller,
      schedule,
      schedule,
      { ...schedule, status: 'paused' },
      () =>
        this.store
          .setStatus(
            caller.orgId,
            id,
            {
              from: ['active'],
              to: 'paused',
              reason: asActor ? 'by_actor' : 'by_admin',
              nextFireAt: null,
            },
            now,
          )
          .then(found),
    );
    return this.changed(caller, changed, 'schedule.paused');
  }

  async resume(caller: ScheduleCaller, id: string): Promise<ScheduleView> {
    await this.assertEnabled(caller.orgId);
    const { schedule } = await this.manageable(caller, id);
    if (schedule.status === 'active')
      return scheduleView(schedule, caller.userId);
    if (schedule.status === 'disabled') throw this.disabled();
    const now = await this.store.now();
    // A pause is not an outage: the paused period's slots never fire.
    const changed = await this.audited(
      caller,
      schedule,
      schedule,
      { ...schedule, status: 'active' },
      () =>
        this.store
          .setStatus(
            caller.orgId,
            id,
            {
              from: ['paused'],
              to: 'active',
              reason: null,
              nextFireAt:
                schedule.trigger.kind === 'cron'
                  ? nextCronSlot(
                      schedule.trigger.cron,
                      schedule.trigger.timeZone,
                      now,
                    )
                  : null,
            },
            now,
          )
          .then(found),
    );
    return this.changed(caller, changed, 'schedule.resumed');
  }

  /**
   * Moves the agent version a schedule runs (OWD-14): a published version, or
   * `latest`. Only its owner may, since its runs act as them; a version they
   * may not run is refused here, not at the next firing.
   */
  async setAgentVersion(
    caller: ScheduleCaller,
    id: string,
    agentVersion: AgentVersionPin,
  ): Promise<ScheduleView> {
    await this.assertEnabled(caller.orgId);
    const { schedule, asActor } = await this.manageable(caller, id);
    if (!asActor) {
      throw forbidden(
        "A schedule runs as its owner: only they can change its agent's version",
      );
    }
    if (schedule.agentKey === undefined) {
      throw invalidVersion(
        'Only an agent schedule runs a version of its agent',
      );
    }
    if (schedule.status === 'disabled') throw this.disabled();
    const pin = await this.pinned(caller, schedule.agentKey, agentVersion);
    if (pin === undefined) throw notFound();
    const now = await this.store.now();
    const changed = await this.audited(
      caller,
      schedule,
      schedule,
      { ...schedule, agentVersion: pin },
      () => this.store.setAgentVersion(caller.orgId, id, pin, now),
    );
    if (changed === null) throw notFound();
    this.logger.info(
      'schedule.agent_version_set',
      harnessLogMeta('schedule.agent_version_set', {
        orgId: caller.orgId,
        actorId: caller.userId,
        scheduleId: id,
        agentKey: schedule.agentKey,
        agentVersion: pin,
      }),
    );
    return scheduleView(changed, caller.userId);
  }

  /** Allowed while the flag is off, so a user can always remove what runs as them. */
  async delete(caller: ScheduleCaller, id: string): Promise<void> {
    const { schedule } = await this.manageable(caller, id);
    await this.audited(caller, schedule, schedule, null, () =>
      this.store.delete(caller.orgId, id),
    );
    this.logger.info(
      'schedule.deleted',
      harnessLogMeta('schedule.deleted', {
        orgId: caller.orgId,
        actorId: caller.userId,
        scheduleId: id,
      }),
    );
  }

  async runNow(
    caller: ScheduleCaller,
    id: string,
    key: string,
  ): Promise<FiringView> {
    await this.assertEnabled(caller.orgId);
    const { schedule, asActor } = await this.manageable(caller, id);
    if (!asActor) {
      throw forbidden('A schedule runs as its owner: only they can run it now');
    }
    if (schedule.status === 'disabled') throw this.disabled();
    return firingView(await this.firer.runNow(schedule, key));
  }

  /**
   * A webhook delivery (TR-04). The caller proves it holds the schedule's
   * secret: a signed webhook by an HMAC of the timestamp and the raw body,
   * the timestamp within `WEBHOOK_SIGNATURE_TOLERANCE_S` of now and each
   * signed delivery accepted once (a captured request cannot be replayed);
   * an older one by its token. A missing
   * schedule and a wrong credential answer alike. Then the org's body cap and
   * the schedule's per-minute rate, counted across replicas. The body never
   * reaches the run: its size, type and digest do (TR-03).
   */
  async webhook(
    scheduleId: string,
    delivery: WebhookDelivery,
  ): Promise<{ firing: FiringView | null; ignored?: 'paused' | 'duplicate' }> {
    const schedule = await this.store.get(null, scheduleId);
    if (schedule?.trigger.kind !== 'webhook') throw badToken();
    const now = await this.store.now();
    if (!this.authentic(schedule, delivery, now)) throw badToken();
    await this.assertEnabled(schedule.orgId);
    if (schedule.status !== 'active')
      return { firing: null, ignored: 'paused' };
    const maxBytes =
      (await this.triggers?.webhookMaxBytes(schedule.orgId)) ??
      RELEASE_DEFAULTS.triggers.webhookMaxBytes;
    if (delivery.body.length > maxBytes) {
      throw new HarnessTriggerRefusedError(
        'TRIGGER_PAYLOAD_TOO_LARGE',
        `The webhook body is larger than ${String(maxBytes)} bytes`,
      );
    }
    const perMinute =
      (await this.triggers?.webhookPerMinute(schedule.orgId)) ??
      RELEASE_DEFAULTS.triggers.webhookPerMinute;
    const windowStart = Math.floor(now / RATE_WINDOW_MS) * RATE_WINDOW_MS;
    const counted = await this.store.countDelivery(
      schedule.orgId,
      schedule.id,
      windowStart,
      windowStart + 2 * RATE_WINDOW_MS,
    );
    if (counted > perMinute) {
      this.logger.warn(
        'schedule.webhook_rate_limited',
        harnessLogMeta('schedule.webhook_rate_limited', {
          orgId: schedule.orgId,
          scheduleId: schedule.id,
          perMinute,
        }),
      );
      throw new HarnessTriggerRefusedError(
        'TRIGGER_RATE_LIMITED',
        'This webhook was called too often; try again shortly',
        Math.max(1, Math.ceil((windowStart + RATE_WINDOW_MS - now) / 1000)),
      );
    }
    const nonce = this.deliveryNonce(schedule, delivery);
    if (
      nonce !== null &&
      !(await this.store.firstDelivery(
        schedule.orgId,
        schedule.id,
        nonce,
        now,
        now + 2 * WEBHOOK_SIGNATURE_TOLERANCE_S * 1000,
      ))
    ) {
      this.logger.warn(
        'schedule.webhook_replayed',
        harnessLogMeta('schedule.webhook_replayed', {
          orgId: schedule.orgId,
          scheduleId: schedule.id,
        }),
      );
      return { firing: null, ignored: 'duplicate' };
    }
    const [firing] = await this.firer.deliver(
      {
        orgId: schedule.orgId,
        kind: 'webhook',
        at: now,
        webhook: {
          bodyBytes: delivery.body.length,
          bodySha256: `sha256:${createHash('sha256').update(delivery.body).digest('hex')}`,
          ...(delivery.contentType !== undefined
            ? { contentType: delivery.contentType }
            : {}),
        },
      },
      schedule,
    );
    return { firing: firing === undefined ? null : firingView(firing) };
  }

  /**
   * A signed delivery's identity (SEC-C-13): the digest of the signature the
   * server computes, not of the header, so extra or reordered `v1=` values do
   * not make a captured request new. A token webhook has none: its caller
   * signs nothing that could tell two deliveries apart.
   */
  private deliveryNonce(
    schedule: AgentSchedule,
    delivery: WebhookDelivery,
  ): string | null {
    if (schedule.trigger.kind !== 'webhook') return null;
    if (webhookAuth(schedule.trigger) !== 'signature') return null;
    const key = schedule.webhookSigningKey;
    if (typeof key !== 'string' || delivery.timestamp === undefined)
      return null;
    return createHash('sha256')
      .update(webhookSignature(key, delivery.timestamp, delivery.body))
      .digest('hex');
  }

  private authentic(
    schedule: AgentSchedule,
    delivery: WebhookDelivery,
    now: number,
  ): boolean {
    if (schedule.trigger.kind !== 'webhook') return false;
    if (webhookAuth(schedule.trigger) === 'token') {
      return (
        delivery.token !== undefined &&
        schedule.webhookTokenHash !== null &&
        sameHash(schedule.webhookTokenHash, hashTriggerToken(delivery.token))
      );
    }
    const key = schedule.webhookSigningKey;
    const { signature, timestamp } = delivery;
    if (
      typeof key !== 'string' ||
      signature === undefined ||
      timestamp === undefined ||
      !/^\d{1,12}$/.test(timestamp) ||
      Math.abs(now / 1000 - Number(timestamp)) > WEBHOOK_SIGNATURE_TOLERANCE_S
    ) {
      return false;
    }
    return signatureMatches(
      webhookSignature(key, timestamp, delivery.body),
      signature,
    );
  }

  /**
   * Refuses a budget per run above the ceiling its runs are held to (UX-07): every firing's reservation would be
   * refused, so the schedule could never run. A read that fails does not block the save: the ledger holds each run
   * at its reservation either way.
   */
  private async assertWithinBudgetCeiling(
    caller: ScheduleCaller,
    request: CreateScheduleRequest,
  ): Promise<void> {
    if (this.budgets === null || request.maxCostUsd === undefined) return;
    let ceiling: Awaited<ReturnType<RunBudgetCeiling['ceiling']>>;
    try {
      ceiling = await this.budgets.ceiling(
        caller.orgId,
        request.agentKey ?? null,
      );
    } catch (error) {
      this.logger.warn('schedule.budgets_unreadable', {
        orgId: caller.orgId,
        errorType: error instanceof Error ? error.name : typeof error,
      });
      return;
    }
    if (
      ceiling === null ||
      usdMicros(request.maxCostUsd) <= ceiling.usdMicros
    ) {
      return;
    }
    throw new HarnessScheduleRefusedError(
      'SCHEDULE_BUDGET_EXCEEDS_CEILING',
      `A run's budget can be at most USD ${String(ceiling.usdMicros / 1_000_000)}, ${ceiling.why}`,
      'maxCostUsd',
    );
  }

  /**
   * A schedule's notice channels (TR-07): the ones asked for, in-app when none
   * were, each one the org allows; a channel the org does not allow is
   * refused here rather than dropped at the run's end.
   */
  private async notifyChannels(
    orgId: string,
    requested: NotifyChannel[] | undefined,
  ): Promise<NotifyChannel[]> {
    const wanted = [...new Set(requested ?? DEFAULT_NOTIFY)];
    if (wanted.some((channel) => !NOTIFY_CHANNELS.includes(channel))) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_INVALID',
        'Unknown notice channel',
        'notify',
      );
    }
    if (this.triggers === undefined) return wanted;
    const allowed = await this.triggers.notifyChannels(orgId);
    if (wanted.some((channel) => !allowed.includes(channel))) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_INVALID',
        'Your organization does not allow notices on this channel',
        'notify',
      );
    }
    return wanted;
  }

  /**
   * The version an agent schedule pins: the one asked for once the caller may
   * run it, `latest` as asked, else the latest one now. Undefined for the
   * assistant, which has no versions.
   */
  private async pinned(
    caller: ScheduleCaller,
    agentKey: string | undefined,
    requested: AgentVersionPin | undefined,
  ): Promise<AgentVersionPin | undefined> {
    if (agentKey === undefined) {
      if (requested !== undefined) {
        throw invalidVersion(
          'Only an agent schedule runs a version of its agent',
        );
      }
      return undefined;
    }
    if (requested === 'latest') return 'latest';
    if (this.agentVersions === undefined) return requested ?? 'latest';
    const resolved = await this.agentVersions.resolve(
      caller.orgId,
      caller.userId,
      agentKey,
      requested,
    );
    if (resolved === null) {
      throw invalidVersion(
        requested === undefined
          ? 'You cannot run this agent'
          : 'This agent has no such version, or you cannot run it',
      );
    }
    return resolved;
  }

  private trigger(
    request: ScheduleTriggerRequest,
    now: number,
  ): ScheduleTrigger {
    if (request.kind === 'cron') {
      assertCron(request.cron, request.timeZone, now);
      return {
        kind: 'cron',
        cron: request.cron.trim(),
        timeZone: request.timeZone,
        catchUp: request.catchUp ?? 'skip',
      };
    }
    const coalesceS = bounded(
      request.coalesceS,
      DEFAULT_COALESCE_S,
      MIN_COALESCE_S,
      MAX_COALESCE_S,
      'coalesceS',
    );
    // A new webhook is signed unless its caller cannot sign (TR-04).
    return request.kind === 'webhook'
      ? { kind: 'webhook', coalesceS, auth: request.auth ?? 'signature' }
      : { kind: 'connector', connectorId: request.connectorId, coalesceS };
  }

  /** One store write as an audited control-plane write: the write is the transaction's effect. */
  private async audited<R>(
    caller: ScheduleCaller,
    schedule: AgentSchedule,
    before: AgentSchedule | null,
    after: AgentSchedule | null,
    write: () => Promise<R>,
  ): Promise<R> {
    const { result } = await this.transaction.run(
      { orgId: caller.orgId, actor: actorRefOfUser(caller.userId) },
      (tx) =>
        Promise.resolve({
          result: tx.external(write),
          change: scheduleChange(schedule, before, after),
        }),
    );
    return result.value;
  }

  private changed(
    caller: ScheduleCaller,
    changed: AgentSchedule | null,
    event: string,
  ): ScheduleView {
    // Lost a race with another change (or a deletion): the caller retries.
    if (changed === null) throw notFound();
    this.logger.info(
      event,
      harnessLogMeta(event, {
        orgId: caller.orgId,
        actorId: caller.userId,
        scheduleId: changed.id,
        ownerId: changed.actorId,
        status: changed.status,
        reason: changed.statusReason,
      }),
    );
    return scheduleView(changed, caller.userId);
  }

  private disabled(): HarnessScheduleRefusedError {
    return new HarnessScheduleRefusedError(
      'SCHEDULE_DISABLED',
      'This schedule is disabled: its owner can no longer run it. Delete it and create a new one',
    );
  }

  private async manageable(
    caller: ScheduleCaller,
    id: string,
  ): Promise<{ schedule: AgentSchedule; asActor: boolean }> {
    const schedule = await this.store.get(caller.orgId, id);
    if (schedule === null) throw notFound();
    if (schedule.actorId === caller.userId) return { schedule, asActor: true };
    if (await this.reachesOthers(caller)) return { schedule, asActor: false };
    throw notFound();
  }

  private async reachesOthers(caller: ScheduleCaller): Promise<boolean> {
    if (caller.via === 'service' || this.reach === null) return false;
    return this.reach.reachesOthers(caller.orgId, caller.userId);
  }

  /** A schedule is only made where its firings can run: durable agent chat on for the org. */
  private async assertRunnable(orgId: string): Promise<void> {
    if (this.rollout === undefined) return;
    const decided = await this.rollout.decideForOrg(orgId).catch(() => null);
    if (decided?.useHarness !== true) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULED_RUNS_UNAVAILABLE',
        'Scheduled runs need durable agent chat, which is off for this organization',
      );
    }
  }

  private async assertEnabled(orgId: string): Promise<void> {
    let on: boolean;
    try {
      on = await this.setting.enabled(orgId);
    } catch {
      on = false;
    }
    if (!on) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULED_RUNS_DISABLED',
        'Scheduled agent runs are not enabled for this organization',
      );
    }
  }
}

function sameHash(stored: string, given: string): boolean {
  const a = Buffer.from(stored, 'hex');
  const b = Buffer.from(given, 'hex');
  return a.length === b.length && timingSafeEqual(a, b);
}
