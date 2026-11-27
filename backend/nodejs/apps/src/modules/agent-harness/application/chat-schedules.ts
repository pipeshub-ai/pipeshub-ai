import { createHash } from 'node:crypto';
import { canonicalDigest } from '../contracts/canonical-json';
import {
  HarnessNotFoundError,
  HarnessScheduleRefusedError,
} from '../domain/errors';
import { type HarnessRun, journalStreamId } from '../domain/execution';
import { approvalBindingDigests } from '../domain/interactions';
import type { JournalEvent } from '../domain/journal';
import type { JournalPreparer, JournalStore } from '../ports/journal-store';
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
  ScheduleView,
  ScheduleService,
  ScheduleTemplate,
  ScheduleTriggerRequest,
} from './schedule-service';

/** The platform tool a harness run proposes a schedule with (ADR-SCH-01 D2). */
export const SCHEDULE_CREATE_TOOL_ID = 'pipeshub@schedules/create';
/** Node's audit fact in the run's journal of the schedule an approval made; single use is the store's, not this. */
export const SCHEDULE_CREATED_EVENT = 'x.pipeshub.schedule.created';

/** Where Node notes that an approval made its schedule. */
export interface ScheduleMarks {
  record(
    run: HarnessRun,
    attemptId: string,
    made: { interactionId: string; scheduleId: string },
  ): Promise<void>;
}

/** The note as a Node event of the run's own journal (sealed, audited), as every Node run event. */
export class JournalScheduleMarks<Tx> implements ScheduleMarks {
  constructor(
    private readonly store: Pick<JournalStore<Tx>, 'append'>,
    private readonly journals: JournalPreparer,
    private readonly transaction: <T>(
      work: (tx: Tx) => Promise<T>,
    ) => Promise<T>,
  ) {}

  async record(
    run: HarnessRun,
    attemptId: string,
    made: { interactionId: string; scheduleId: string },
  ): Promise<void> {
    const prepared = await this.journals.prepare({
      orgId: run.orgId,
      sessionId: run.sessionId,
      streamId: journalStreamId(run),
      runId: run.runId,
      attemptId,
      writer: 'node',
      events: [
        {
          type: SCHEDULE_CREATED_EVENT,
          schemaVersion: 1,
          critical: false,
          visibility: 'audit',
          data: { ...made },
        },
      ],
    });
    await this.transaction((tx) => this.store.append(prepared, tx));
  }
}

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
  marks: ScheduleMarks;
  logger?: HarnessLogger;
}

const notApproved = (why: string): HarnessScheduleRefusedError =>
  new HarnessScheduleRefusedError(
    'SCHEDULE_NOT_APPROVED',
    `This schedule was not approved as asked (${why})`,
  );

/**
 * One id per (org, run, decision): a second request for the same decision lands on the schedule the first made.
 * The run is part of it because an interaction id is unique only within its run: the loop's first approval is
 * `ix.approve.t1.0` in every run, so an id of the org and the interaction alone gave every run the first one's schedule.
 */
export const chatScheduleId = (
  orgId: string,
  runId: string,
  interactionId: string,
): string =>
  `sch_${createHash('sha256')
    .update(
      `schedule-from-chat\u0000${orgId}\u0000${runId}\u0000${interactionId}`,
      'utf8',
    )
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
 * firing re-reads it. One decision makes one schedule, once: its id derives from
 * the decision, and deleting it leaves a tombstone under that id in the
 * schedule store, so a schedule its person deleted is never made again.
 */
export class ChatScheduleService {
  private readonly logger: HarnessLogger;

  constructor(private readonly options: ChatScheduleOptions) {
    this.logger = options.logger ?? silentHarnessLogger;
  }

  async create(
    orgId: string,
    runId: string,
    attemptId: string,
    request: ChatScheduleRequest,
  ): Promise<{ schedule: ChatScheduleView; created: boolean }> {
    const run = await this.options.runs.get(orgId, runId);
    if (run === null) throw new HarnessNotFoundError();
    const events = await this.options.runs.runEvents(orgId, runId);
    this.assertApproved(run, events, request);
    const id = chatScheduleId(orgId, run.runId, request.interactionId);
    const made = await this.existing(orgId, id, run, request.interactionId);
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
    let created: ScheduleView;
    try {
      ({ schedule: created } = await this.options.schedules.create(
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
      ));
    } catch (error) {
      // The store's insert under the id this decision derives is what makes it once: a live schedule there is the
      // one a racing request made; a tombstone (SCHEDULE_APPROVAL_USED) is one its person deleted.
      const raced = await this.existing(orgId, id, run, request.interactionId);
      if (raced !== null) return { schedule: view(raced), created: false };
      throw error;
    }
    await this.mark(run, attemptId, request.interactionId, id);
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
    return {
      schedule: {
        id: created.id,
        name: created.name,
        status: created.status,
        trigger: created.trigger,
        nextFireAt: created.nextFireAt,
      },
      created: true,
    };
  }

  /** The audit fact of what the decision made; nothing depends on it, so a failure to journal it is only logged. */
  private async mark(
    run: HarnessRun,
    attemptId: string,
    interactionId: string,
    scheduleId: string,
  ): Promise<void> {
    try {
      await this.options.marks.record(run, attemptId, {
        interactionId,
        scheduleId,
      });
    } catch (error) {
      this.logger.warn(
        'schedule.created_from_chat_unjournaled',
        harnessLogMeta('schedule.created_from_chat_unjournaled', {
          orgId: run.orgId,
          runId: run.runId,
          scheduleId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /** The schedule this decision already made, only if it is this run's and this person's: never anyone else's. */
  private async existing(
    orgId: string,
    id: string,
    run: HarnessRun,
    interactionId: string,
  ): Promise<AgentSchedule | null> {
    const found = await this.options.store.get(orgId, id);
    if (found === null) return null;
    const origin = found.origin;
    if (
      origin?.interactionId === interactionId &&
      origin.runId === run.runId &&
      found.actorId === run.actorId
    ) {
      return found;
    }
    throw new HarnessScheduleRefusedError(
      'SCHEDULE_FORBIDDEN',
      'That schedule is not this chat’s to answer with',
    );
  }

  private assertApproved(
    run: HarnessRun,
    events: readonly JournalEvent[],
    request: ChatScheduleRequest,
  ): void {
    if (request.toolId !== SCHEDULE_CREATE_TOOL_ID) {
      throw notApproved('not a schedule call');
    }
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
