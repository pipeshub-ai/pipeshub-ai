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
  ScheduleChangedFrom,
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
  ScheduleCaller,
  ScheduleListItem,
  ScheduleView,
  ScheduleService,
  ScheduleTemplate,
  ScheduleTriggerRequest,
} from './schedule-service';

/** The platform tool a harness run proposes a schedule with (ADR-SCH-01 D2). */
export const SCHEDULE_CREATE_TOOL_ID = 'pipeshub@schedules/create';
/** The schedules toolset's other tools (ADR-SCH-03 D1): find reads, update and delete ask every time. */
export const SCHEDULE_FIND_TOOL_ID = 'pipeshub@schedules/find';
export const SCHEDULE_UPDATE_TOOL_ID = 'pipeshub@schedules/update';
export const SCHEDULE_DELETE_TOOL_ID = 'pipeshub@schedules/delete';

/** `schedule_update`'s arguments (`schedule-update-arguments.schema.json`). */
export interface ScheduleUpdateToolArguments {
  id: string;
  revision: number;
  name?: string;
  ask?: string;
  when?: ScheduleToolArguments['when'];
  notify?: NotifyChannel[];
  action?: 'pause' | 'resume' | 'skip_next' | 'run_now';
}

/** `schedule_delete`'s arguments (`schedule-delete-arguments.schema.json`). */
export interface ScheduleDeleteToolArguments {
  id: string;
  revision: number;
}

/** `schedule_find`'s arguments (`schedule-find-arguments.schema.json`). */
export interface ScheduleFindToolArguments {
  query?: string;
  status?: 'active' | 'paused' | 'disabled';
  madeHere?: boolean;
}

/** The most schedules `schedule_find` answers (ADR-SCH-03 D2). */
export const SCHEDULE_FIND_LIMIT = 20;

/**
 * The one rule of `schedule_update` the generated models cannot say (ADR-SCH-03 D4): field changes, at least one, or
 * one action, never both. Null when the call keeps it.
 */
export function updateArgumentsProblem(
  args: ScheduleUpdateToolArguments,
): string | null {
  const fields = (['name', 'ask', 'when', 'notify'] as const).filter(
    (field) => args[field] !== undefined,
  );
  if (args.action !== undefined && fields.length > 0) {
    return 'an action is not combined with changes';
  }
  if (args.action === undefined && fields.length === 0) {
    return 'nothing to change';
  }
  return null;
}
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
  /**
   * The name the model's `call_tool` wrapper named, when the tool was reached through it (ADR-SCH-03 D8): the
   * call's digest is then the wrapper's, `{name, arguments}`, over the same arguments.
   */
  calledAs?: string;
}

export interface ChatScheduleView {
  id: string;
  name: string;
  status: AgentSchedule['status'];
  trigger: ScheduleTrigger;
  nextFireAt: string | null;
}

/** A change a chat made (ADR-SCH-03 D4): the schedule as it is now, and whether this call changed it. */
export interface ChatScheduleChange {
  schedule: ScheduleView | null;
  changed: boolean;
}

export interface ChatScheduleOptions {
  /** The run with its input opened, and its journal with payloads resolved. */
  runs: {
    get(orgId: string, runId: string): Promise<HarnessRun | null>;
    runEvents(orgId: string, runId: string): Promise<JournalEvent[]>;
  };
  /** How the run's person reached it: a schedule is a person's, never a service's. */
  vias: { via(orgId: string, runId: string): Promise<RunVia | null> };
  schedules: Pick<
    ScheduleService,
    | 'create'
    | 'update'
    | 'skipNext'
    | 'pause'
    | 'resume'
    | 'runNow'
    | 'delete'
    | 'get'
    | 'list'
    | 'assertRevision'
  >;
  store: Pick<ScheduleStore, 'get'>;
  actors: Pick<ActorAuthority, 'authorize'>;
  marks: ScheduleMarks;
  /**
   * The coding engine the chat chose (an engine kind or `none`), which a schedule made there keeps: its runs open
   * new chats, which would otherwise take the person's default engine. Absent, or no choice: the default.
   */
  engines?: {
    sessionChoice(orgId: string, sessionId: string): Promise<string | null>;
  };
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
      ...(run.agentKey !== undefined ? { agentKey: run.agentKey } : {}),
    };
    const engine = await this.chatEngine(orgId, run.sessionId);
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
        {
          ...scheduleRequest(run, request.arguments),
          ...(engine !== null ? { codingEngine: engine } : {}),
        },
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

  /** The run's person's own schedules, as `schedule_find` reads them (ADR-SCH-03 D2): this chat's first. */
  async find(
    orgId: string,
    runId: string,
    args: ScheduleFindToolArguments,
  ): Promise<ScheduleListItem[]> {
    const { run, caller } = await this.personOf(orgId, runId);
    return this.options.schedules.list(caller, SCHEDULE_FIND_LIMIT, false, {
      conversationId: run.sessionId,
      ...(args.query !== undefined ? { query: args.query } : {}),
      ...(args.status !== undefined ? { status: args.status } : {}),
      ...(args.madeHere !== undefined ? { madeHere: args.madeHere } : {}),
    });
  }

  /** One of the run's person's schedules, as a card shows its current values before asking (ADR-SCH-03 D5). */
  async read(
    orgId: string,
    runId: string,
    scheduleId: string,
  ): Promise<ScheduleView> {
    const { caller } = await this.personOf(orgId, runId);
    const view = await this.options.schedules.get(caller, scheduleId);
    if (view.actorId !== caller.userId) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_NOT_FOUND',
        'Schedule not found',
      );
    }
    return view;
  }

  /**
   * A change the run's person approved (ADR-SCH-03 D6): only on their own `approved` decision of exactly this call,
   * as for a create, then made as them with every check the Schedules page makes. The schedule records this chat
   * and the call, so a repeat of the same decision changes nothing again.
   */
  async update(
    orgId: string,
    runId: string,
    scheduleId: string,
    request: {
      interactionId: string;
      toolId: string;
      arguments: ScheduleUpdateToolArguments;
      callArgsDigest?: string;
      calledAs?: string;
    },
  ): Promise<ChatScheduleChange> {
    const args = request.arguments;
    if (args.id !== scheduleId) throw notApproved('another schedule');
    const problem = updateArgumentsProblem(args);
    if (problem !== null) {
      throw new HarnessScheduleRefusedError('SCHEDULE_INVALID', problem);
    }
    const run = await this.approvedRun(orgId, runId, {
      ...request,
      expected: SCHEDULE_UPDATE_TOOL_ID,
    });
    const { caller } = await this.personOf(orgId, runId, run);
    const current = await this.options.store.get(orgId, scheduleId);
    if (current === null || current.actorId !== caller.userId) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_NOT_FOUND',
        'Schedule not found',
      );
    }
    if (
      current.lastChangedFrom?.interactionId === request.interactionId &&
      current.lastChangedFrom.runId === runId
    ) {
      return {
        schedule: await this.options.schedules.get(caller, scheduleId),
        changed: false,
      };
    }
    const from: ScheduleChangedFrom = {
      conversationId: run.sessionId,
      agentKey: run.agentKey ?? null,
      at: Date.now(),
      interactionId: request.interactionId,
      runId,
    };
    const schedules = this.options.schedules;
    let schedule: ScheduleView;
    switch (args.action) {
      case 'pause':
      case 'resume':
        schedule = await schedules[args.action](
          caller,
          scheduleId,
          from,
          args.revision,
        );
        break;
      case 'skip_next':
        schedule = await schedules.skipNext(
          caller,
          scheduleId,
          args.revision,
          from,
        );
        break;
      case 'run_now':
        await schedules.assertRevision(caller, scheduleId, args.revision);
        await schedules.runNow(
          caller,
          scheduleId,
          `chat:${runId}:${request.interactionId}`,
        );
        schedule = await schedules.get(caller, scheduleId);
        break;
      case undefined:
        schedule = await schedules.update(
          caller,
          scheduleId,
          {
            revision: args.revision,
            ...(args.name !== undefined ? { name: args.name } : {}),
            ...(args.ask !== undefined ? { query: args.ask } : {}),
            ...(args.when !== undefined
              ? {
                  trigger:
                    args.when.kind === 'once'
                      ? {
                          kind: 'once',
                          at: args.when.at,
                          timeZone: args.when.timeZone,
                        }
                      : {
                          kind: 'cron',
                          cron: args.when.cron,
                          timeZone: args.when.timeZone,
                        },
                }
              : {}),
            ...(args.notify !== undefined ? { notify: [...args.notify] } : {}),
          },
          from,
        );
        break;
    }
    this.logger.info(
      'schedule.changed_from_chat',
      harnessLogMeta('schedule.changed_from_chat', {
        orgId,
        runId,
        sessionId: run.sessionId,
        scheduleId,
        action: args.action ?? 'edit',
      }),
    );
    return { schedule, changed: true };
  }

  /** A delete the run's person approved (ADR-SCH-03 D6); a repeat once it is gone answers that it is gone. */
  async delete(
    orgId: string,
    runId: string,
    scheduleId: string,
    request: {
      interactionId: string;
      toolId: string;
      arguments: ScheduleDeleteToolArguments;
      callArgsDigest?: string;
      calledAs?: string;
    },
  ): Promise<ChatScheduleChange> {
    if (request.arguments.id !== scheduleId) {
      throw notApproved('another schedule');
    }
    const run = await this.approvedRun(orgId, runId, {
      ...request,
      expected: SCHEDULE_DELETE_TOOL_ID,
    });
    const { caller } = await this.personOf(orgId, runId, run);
    const current = await this.options.store.get(orgId, scheduleId);
    if (current === null) return { schedule: null, changed: false };
    if (current.actorId !== caller.userId) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_NOT_FOUND',
        'Schedule not found',
      );
    }
    await this.options.schedules.delete(
      caller,
      scheduleId,
      request.arguments.revision,
    );
    this.logger.info(
      'schedule.deleted_from_chat',
      harnessLogMeta('schedule.deleted_from_chat', {
        orgId,
        runId,
        sessionId: run.sessionId,
        scheduleId,
      }),
    );
    return { schedule: null, changed: true };
  }

  /** The run and its journal's approval of this call, for a change (the create's own check, for another tool). */
  private async approvedRun(
    orgId: string,
    runId: string,
    request: {
      interactionId: string;
      toolId: string;
      arguments: unknown;
      callArgsDigest?: string;
      calledAs?: string;
      expected: string;
    },
  ): Promise<HarnessRun> {
    const run = await this.options.runs.get(orgId, runId);
    if (run === null) throw new HarnessNotFoundError();
    const events = await this.options.runs.runEvents(orgId, runId);
    this.assertApprovedCall(run, events, request, request.expected);
    return run;
  }

  /** The person the run acts for, as a schedule caller, their authority re-read now. */
  private async personOf(
    orgId: string,
    runId: string,
    known?: HarnessRun,
  ): Promise<{ run: HarnessRun; caller: ScheduleCaller }> {
    const run = known ?? (await this.options.runs.get(orgId, runId));
    if (run === null) throw new HarnessNotFoundError();
    const actor = await this.options.actors.authorize(orgId, run.actorId);
    if ('refused' in actor) {
      throw new HarnessScheduleRefusedError(
        'SCHEDULE_FORBIDDEN',
        'The person this chat acts for can no longer manage schedules',
      );
    }
    const via = (await this.options.vias.via(orgId, runId)) ?? 'session';
    return {
      run,
      caller: {
        orgId,
        userId: run.actorId,
        via,
        ...(run.input?.locale !== undefined
          ? { locale: run.input.locale }
          : {}),
      },
    };
  }

  /** The chat's engine choice; a failed read is the default, logged, since the schedule is made either way. */
  private async chatEngine(
    orgId: string,
    sessionId: string,
  ): Promise<string | null> {
    if (this.options.engines === undefined) return null;
    try {
      return await this.options.engines.sessionChoice(orgId, sessionId);
    } catch (error) {
      this.logger.warn(
        'schedule.chat_engine_unread',
        harnessLogMeta('schedule.chat_engine_unread', {
          orgId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return null;
    }
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
    this.assertApprovedCall(run, events, request, SCHEDULE_CREATE_TOOL_ID);
  }

  /** ADR-SCH-01 D3, for any of the schedules tools that writes: the person's own approval of exactly this call. */
  private assertApprovedCall(
    run: HarnessRun,
    events: readonly JournalEvent[],
    request: {
      interactionId: string;
      toolId: string;
      arguments: unknown;
      callArgsDigest?: string;
      calledAs?: string;
    },
    expected: string,
  ): void {
    if (request.toolId !== expected) {
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
    // Reached through `call_tool`, the call the person approved was the wrapper over these same arguments: its
    // digest is recomputed here from the arguments sent, never taken on trust.
    const wrapped =
      request.calledAs !== undefined
        ? canonicalDigest({
            name: request.calledAs,
            arguments: request.arguments,
          })
        : undefined;
    if (approval.editedDigest !== undefined) {
      if (digest !== approval.editedDigest) {
        throw notApproved('not the arguments as edited');
      }
    } else if (callDigest !== digest && callDigest !== wrapped) {
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
