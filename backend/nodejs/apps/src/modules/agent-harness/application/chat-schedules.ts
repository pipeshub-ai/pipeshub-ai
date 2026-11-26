import { createHash } from 'node:crypto';
import { canonicalDigest } from '../contracts/canonical-json';
import {
  HarnessNotFoundError,
  HarnessScheduleRefusedError,
} from '../domain/errors';
import type { HarnessRun } from '../domain/execution';
import { approvalBindingDigests } from '../domain/interactions';
import type { JournalEvent } from '../domain/journal';
import type { RunVia } from '../domain/run-spec';
import { principalRefFor } from '../domain/run-spec';
import type {
  AgentSchedule,
  NotifyChannel,
  ScheduleOrigin,
  ScheduleTrigger,
} from '../domain/schedules';
import type { ActorAuthority, ScheduleStore } from '../ports/schedules';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import type {
  CreateScheduleRequest,
  ScheduleService,
  ScheduleTemplate,
  ScheduleTriggerRequest,
} from './schedule-service';

/** The platform tool a harness run proposes a schedule with (ADR-SCH-01 D2). */
export const SCHEDULE_CREATE_TOOL_ID = 'pipeshub@schedules/create';

/** The tool's arguments: the schedule as its person saw it on the approval card. */
export interface ScheduleToolArguments {
  name: string;
  ask: string;
  when:
    | { kind: 'once'; at: string; timeZone: string }
    | { kind: 'cron'; cron: string; timeZone: string };
  notify?: NotifyChannel[];
}

export interface ChatScheduleRequest {
  interactionId: string;
  toolId: string;
  arguments: ScheduleToolArguments;
  /**
   * The digest of the call's arguments as the model made it. Needed only when
   * the person edited the call (AP-06): `arguments` are then the edited ones,
   * and the interaction id is the original call's.
   */
  callArgsDigest?: string;
}

export interface ChatScheduleView {
  id: string;
  name: string;
  status: AgentSchedule['status'];
  trigger: ScheduleTrigger;
  nextFireAt: string | null;
}

export interface ChatScheduleOptions {
  /** The run with its input opened, and its journal with payloads resolved. */
  runs: {
    get(orgId: string, runId: string): Promise<HarnessRun | null>;
    runEvents(orgId: string, runId: string): Promise<JournalEvent[]>;
  };
  /** How the run's person reached it: a schedule is a person's, never a service's. */
  vias: { via(orgId: string, runId: string): Promise<RunVia | null> };
  schedules: Pick<ScheduleService, 'create'>;
  store: Pick<ScheduleStore, 'get'>;
  actors: Pick<ActorAuthority, 'authorize'>;
  logger?: HarnessLogger;
}

const notApproved = (why: string): HarnessScheduleRefusedError =>
  new HarnessScheduleRefusedError(
    'SCHEDULE_NOT_APPROVED',
    `This schedule was not approved as asked (${why})`,
  );

/** One id per (org, decision): a second request for the same decision lands on the schedule the first made. */
export const chatScheduleId = (orgId: string, interactionId: string): string =>
  `sch_${createHash('sha256')
    .update(`schedule-from-chat\u0000${orgId}\u0000${interactionId}`, 'utf8')
    .digest('hex')
    .slice(0, 32)}`;

const view = (schedule: AgentSchedule): ChatScheduleView => ({
  id: schedule.id,
  name: schedule.name,
  status: schedule.status,
  trigger: structuredClone(schedule.trigger),
  nextFireAt:
    schedule.nextFireAt === null
      ? null
      : new Date(schedule.nextFireAt).toISOString(),
});

interface Approval {
  /** The request's `bindingDigest`: what the person was asked about. */
  binding: unknown;
  decision: unknown;
  by: unknown;
  channel: unknown;
  editedDigest: string | undefined;
}

function approvalOf(
  events: readonly JournalEvent[],
  runId: string,
  interactionId: string,
): Approval | null {
  const resolved = events.find(
    (event) =>
      event.type === 'interaction.resolved' &&
      (event.runId === undefined || event.runId === runId) &&
      event.data?.['interactionId'] === interactionId,
  );
  if (resolved === undefined) return null;
  const requested = events.find(
    (event) =>
      event.type === 'interaction.requested' &&
      (event.runId === undefined || event.runId === runId) &&
      event.data?.['interactionId'] === interactionId,
  );
  const data = resolved.data ?? {};
  // AP-06: the edit is journaled inline as `answerRef`, its hash the edit's digest; the audit chain's
  // `editedArgsDigest` is that hash, but audit fields are never stored in the journal.
  const ref = data['answerRef'] as { hash?: unknown } | undefined;
  return {
    binding: requested?.data?.['bindingDigest'],
    decision: data['decision'],
    by: data['by'],
    channel: data['channel'],
    editedDigest:
      data['decision'] === 'approved' && typeof ref?.hash === 'string'
        ? ref.hash
        : undefined,
  };
}

/**
 * A schedule a harness chat asked for (ADR-SCH-01 D3). The worker holds no
 * credential of the person, so Node makes it only from what the run's own
 * journal says the person decided: their own `approved` decision of exactly
 * this call (the request it answered was about this tool with these
 * arguments), with the
 * arguments they approved (the edited ones, when they edited it). Then it is
 * the run's person's schedule, made through `ScheduleService.create` with
 * every check the Schedules page's create makes, their authority re-read as a
 * firing re-reads it. One decision makes one schedule.
 */
export class ChatScheduleService {
  private readonly logger: HarnessLogger;

  constructor(private readonly options: ChatScheduleOptions) {
    this.logger = options.logger ?? silentHarnessLogger;
  }

  async create(
    orgId: string,
    runId: string,
    request: ChatScheduleRequest,
  ): Promise<{ schedule: ChatScheduleView; created: boolean }> {
    const run = await this.options.runs.get(orgId, runId);
    if (run === null) throw new HarnessNotFoundError();
    await this.assertApproved(run, request);
    const id = chatScheduleId(orgId, request.interactionId);
    const made = await this.existing(orgId, id, request.interactionId);
    if (made !== null) return { schedule: view(made), created: false };
    const actor = await this.options.actors.authorize(orgId, run.actorId);
    if ('refused' in actor) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_FORBIDDEN',
        'The person this chat acts for can no longer make schedules',
      );
    }
    const via = (await this.options.vias.via(orgId, runId)) ?? 'session';
    const origin: ScheduleOrigin = {
      conversationId: run.sessionId,
      runId,
      interactionId: request.interactionId,
    };
    try {
      await this.options.schedules.create(
        {
          orgId,
          userId: run.actorId,
          via,
          ...(run.input?.locale !== undefined
            ? { locale: run.input.locale }
            : {}),
        },
        scheduleRequest(run, request.arguments),
        { id, origin },
      );
    } catch (error) {
      // Two requests of one decision raced: the other one made it.
      const raced = await this.existing(orgId, id, request.interactionId);
      if (raced !== null) return { schedule: view(raced), created: false };
      throw error;
    }
    const created = await this.options.store.get(orgId, id);
    if (created === null) throw new HarnessNotFoundError();
    this.logger.info(
      'schedule.created_from_chat',
      harnessLogMeta('schedule.created_from_chat', {
        orgId,
        runId,
        sessionId: run.sessionId,
        scheduleId: id,
        trigger: created.trigger.kind,
      }),
    );
    return { schedule: view(created), created: true };
  }

  private async existing(
    orgId: string,
    id: string,
    interactionId: string,
  ): Promise<AgentSchedule | null> {
    const found = await this.options.store.get(orgId, id);
    return found?.origin?.interactionId === interactionId ? found : null;
  }

  private async assertApproved(
    run: HarnessRun,
    request: ChatScheduleRequest,
  ): Promise<void> {
    if (request.toolId !== SCHEDULE_CREATE_TOOL_ID) {
      throw notApproved('not a schedule call');
    }
    const events = await this.options.runs.runEvents(run.orgId, run.runId);
    const approval = approvalOf(events, run.runId, request.interactionId);
    if (approval === null) throw notApproved('no decision');
    if (approval.decision !== 'approved') throw notApproved('not approved');
    // Only the person the run acts for decides on their own schedule, now: never an approver for them, a held
    // grant Node applied (`system`), or an org rule.
    if (
      approval.by !== principalRefFor(run.actorId) ||
      approval.channel === 'system'
    ) {
      throw notApproved('not the person’s own decision');
    }
    const digest = canonicalDigest(request.arguments);
    const callDigest = request.callArgsDigest ?? digest;
    if (approval.editedDigest !== undefined) {
      if (digest !== approval.editedDigest) {
        throw notApproved('not the arguments as edited');
      }
    } else if (callDigest !== digest) {
      throw notApproved('other arguments');
    }
    if (
      typeof approval.binding !== 'string' ||
      !approvalBindingDigests(
        request.interactionId,
        request.toolId,
        callDigest,
      ).includes(approval.binding)
    ) {
      throw notApproved('another call');
    }
  }
}

/** The schedule's template: the ask, with the agent, model and chat mode the chat itself ran with. */
function scheduleRequest(
  run: HarnessRun,
  args: ScheduleToolArguments,
): CreateScheduleRequest {
  const input = run.input ?? null;
  const model = input?.model;
  const template: ScheduleTemplate = {
    query: args.ask,
    ...(model?.chatMode !== undefined ? { chatMode: model.chatMode } : {}),
    ...(input?.tools !== undefined ? { tools: [...input.tools] } : {}),
    ...(input?.agentCapabilities !== undefined
      ? { agentCapabilities: { ...input.agentCapabilities } }
      : {}),
    ...(model?.modelKey !== undefined ? { modelKey: model.modelKey } : {}),
    ...(model?.modelName !== undefined ? { modelName: model.modelName } : {}),
    ...(model?.modelProvider !== undefined
      ? { modelProvider: model.modelProvider }
      : {}),
    ...(model?.modelFriendlyName !== undefined
      ? { modelFriendlyName: model.modelFriendlyName }
      : {}),
    ...(model?.reasoningEffort !== undefined
      ? { reasoningEffort: model.reasoningEffort }
      : {}),
    ...(model?.accountId !== undefined
      ? { modelAccountId: model.accountId }
      : {}),
  };
  const trigger: ScheduleTriggerRequest =
    args.when.kind === 'once'
      ? { kind: 'once', at: args.when.at, timeZone: args.when.timeZone }
      : { kind: 'cron', cron: args.when.cron, timeZone: args.when.timeZone };
  return {
    name: args.name,
    ...(run.agentKey !== undefined ? { agentKey: run.agentKey } : {}),
    ...(run.agentKey !== undefined && run.agentVersion !== undefined
      ? { agentVersion: run.agentVersion }
      : {}),
    input: template,
    trigger,
    ...(args.notify !== undefined ? { notify: [...args.notify] } : {}),
  };
}
