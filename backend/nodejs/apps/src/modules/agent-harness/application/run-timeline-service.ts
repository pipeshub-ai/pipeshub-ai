import { BudgetAmount } from '../domain/budget-ledger';
import {
  type JobGoalChange,
  jobGoalChanges,
} from '../projection/job-brief-history';
import { HarnessNotFoundError } from '../domain/errors';
import { HarnessRun } from '../domain/execution';
import { Followup } from '../domain/followups';
import { RecalledMemoryView, recalledMemoryView } from '../domain/memory';
import { JournalEvent } from '../domain/journal';
import { BudgetLedgerStore } from '../ports/budget-ledger';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import { JournalStore } from '../ports/journal-store';
import {
  ApprovalView,
  QuestionView,
  childModelLabel,
  ChildView,
  CompactedView,
  ConnectView,
  EngineTaskView,
  foldTimeline,
  ModelLabel,
  TaskView,
  Timeline,
} from '../projection/timeline';
import { buildParts, finalAnswer, MessagePart } from '../projection/transcript';
import {
  answerCitations,
  type RunAnswerView,
} from '../projection/answer-citations';
import {
  EndedRunStatus,
  foldRunDigest,
  RunDigest,
} from '../projection/run-digest';
import {
  foldUnknownEffects,
  UnknownEffectView,
} from '../projection/unknown-effects';
import { MAX_JOURNAL_PAGE } from './execution-service';
import type { RunVerification } from '../domain/run-gates';
import { JournalService } from './journal-service';
import { SessionRunService } from './session-run-service';
import { ChildMergeReport, WorkspaceService } from './workspace-service';

/** A run's journal after `after`, every page, resolved (claim-checked payloads read back). */
export async function readRunEvents(
  journal: Pick<JournalStore<unknown>, 'readRun'>,
  journals: Pick<JournalService, 'resolve'>,
  run: Pick<HarnessRun, 'orgId' | 'runId'>,
  after: number,
): Promise<JournalEvent[]> {
  const events: JournalEvent[] = [];
  let cursor = after;
  for (;;) {
    const page = await journal.readRun(
      run.orgId,
      run.runId,
      cursor,
      MAX_JOURNAL_PAGE,
    );
    events.push(...page);
    if (page.length < MAX_JOURNAL_PAGE) break;
    cursor = page[page.length - 1]?.seq ?? cursor;
  }
  return journals.resolve(events);
}

import { RunBudgetView, RunSpendReader } from './run-spend';
import type { RepositoryUnavailableView } from '../domain/repository-activity';
import type { SecretsHiddenView } from '../domain/secret-activity';
import type { SiteActivityView } from '../domain/site-activity';

export type { RunBudgetView };

export interface RunTimelineView {
  runId: string;
  parentRunId: string | null;
  status: HarnessRun['status'];
  parts: MessagePart[];
  engine: EngineTaskView | null;
  approvals: Array<Omit<ApprovalView, 'afterTurns' | 'seq'>>;
  /** Calls waiting for the run's owner to connect an item (G-12), and how each ended. */
  connects: Array<Omit<ConnectView, 'afterTurns' | 'seq'>>;
  children: Array<
    Omit<ChildView, 'afterTurns' | 'seq' | 'model'> & {
      merge: ChildMergeReport | null;
      /** The model the child calls (MA1): its role's (`role` set), else this run's. */
      model?: ModelLabel & { role?: string };
    }
  >;
  /** The model this run calls, as its own journal names it (MA1). */
  model?: ModelLabel;
  /** A child run's own merge into its parent's workspace (P6.4); null for a root run. */
  merge: ChildMergeReport | null;
  budget: RunBudgetView | null;
  /** What governed memory gave the run (P7.3); absent when it was given none. */
  memory?: RecalledMemoryView;
  /** Suggested next questions after its answer, from the run document (UX5); the conversation's messages never carry them. */
  followups: Followup[];
  /** The run's plan (UXR-B1); a `tool_call` part names its step as `stepId`. */
  tasks: TaskView[];
  /** What runs the turn (`loop` or the engine's kind) and the effort it pinned (null: the model's default), for `/status`. */
  executor?: string;
  reasoningEffort?: string | null;
  toolsGranted?: number;
  /** The coding engine the run's `code_task` hands off to; null: none (CMDUI). */
  codingEngine?: string | null;
  /** What the person's `/compact` did before the run's first turn (SLASH2 C2): the chat's saved row. */
  compacted?: CompactedView;
  /** The run gates its spec pins (G-03..G-05); absent: none. */
  gates?: { dryRun: boolean; planApproval: boolean; verify: boolean };
  /** What its verify gate found (G-05); absent: not checked. */
  verification?: RunVerification;
  /** The end-of-run summary (G-07), once the run has ended; absent while it runs. */
  digest?: RunDigest;
  /** G11: an operator stopped it (scope, reason, when); a job's panel says so after a reload. */
  operatorStop?: HarnessRun['operatorStop'];
  /** WP-63: the run's reach outside its sandbox, as plain lines; absent: none. */
  sites?: SiteActivityView[];
  /** WP-63: clones refused because the org does not list the host; absent: none. */
  repositoriesUnavailable?: RepositoryUnavailableView[];
  /** WP103-FU-1: the credential shapes hidden from the run's model calls, by kind, never a value; absent: none. */
  secretsHidden?: SecretsHiddenView;
  /**
   * BGUI-12: the sources its answer cites, so its own reply draws each `[N]` as the chat's answer does; absent
   * while it has not answered.
   */
  answer?: RunAnswerView;
}

const ENDED: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'cancelled',
]);

export const isEndedStatus = (status: string): status is EndedRunStatus =>
  ENDED.has(status);

/** Each tool call's plan step, on this view only: the saved conversation's parts are unchanged. */
export function withSteps(
  parts: MessagePart[],
  timeline: Pick<Timeline, 'turns'>,
): MessagePart[] {
  const steps = new Map<string, string>();
  for (const call of timeline.turns.flatMap((turn) => turn.calls)) {
    if (call.stepId !== undefined) steps.set(call.callId, call.stepId);
  }
  return parts.map((part) => {
    const stepId =
      part['type'] === 'tool_call'
        ? steps.get(String(part['toolCallId']))
        : undefined;
    return stepId === undefined ? part : { ...part, stepId };
  });
}

export interface RunTimelineServiceOptions {
  runs: Pick<SessionRunService, 'streamAccess'>;
  journal: Pick<JournalStore<unknown>, 'readRun'>;
  journals: Pick<JournalService, 'resolve'>;
  workspaces: Pick<WorkspaceService, 'childMerge'> | null;
  ledger: Pick<BudgetLedgerStore, 'account' | 'remaining'> | null;
  /** The conversation's spend (SLASH2 `/usage`); absent: every conversation shows none. */
  usage?: {
    access: Pick<SessionRunService, 'assertAccess'>;
    runs: SessionUsageRuns;
  };
}

/** A session's root harness-v2 runs one actor started (SLASH2 `/usage`). */
export interface SessionUsageRuns {
  ownRootRunsOf(
    orgId: string,
    sessionId: string,
    actorId: string,
  ): Promise<string[]>;
}

/** The caller's spend over a conversation: their root runs' accounts, each counting its children. */
export interface SessionUsageView {
  runs: number;
  committed: BudgetAmount;
}

/**
 * One durable run as its session's owner reads it (U3): the parts its
 * conversation shows, an engine's task, approvals, children with what their
 * merge did, and its spend. A child's come from its own stream, which the
 * parent's carries nothing of. Other viewers are refused: these are the
 * owner's parts, unfiltered.
 */
export class RunTimelineService {
  private readonly runs: Pick<SessionRunService, 'streamAccess'>;
  private readonly journal: Pick<JournalStore<unknown>, 'readRun'>;
  private readonly journals: Pick<JournalService, 'resolve'>;
  private readonly workspaces: Pick<WorkspaceService, 'childMerge'> | null;
  private readonly spend: RunSpendReader;
  private readonly ledger: Pick<
    BudgetLedgerStore,
    'account' | 'remaining'
  > | null;
  private readonly usageSource: NonNullable<
    RunTimelineServiceOptions['usage']
  > | null;

  constructor(options: RunTimelineServiceOptions) {
    this.runs = options.runs;
    this.journal = options.journal;
    this.journals = options.journals;
    this.workspaces = options.workspaces;
    this.ledger = options.ledger;
    this.spend = new RunSpendReader(options.ledger);
    this.usageSource = options.usage ?? null;
  }

  /** The caller's own spend over the session (SLASH2 `/usage`); a session they may not watch is not found. */
  async usage(scope: SessionExecutionScope): Promise<SessionUsageView> {
    const committed = { usdMicros: 0, tokens: 0 };
    if (this.usageSource === null) return { runs: 0, committed };
    await this.usageSource.access.assertAccess(scope);
    if (this.ledger === null) return { runs: 0, committed };
    const ledger = this.ledger;
    const runIds = await this.usageSource.runs.ownRootRunsOf(
      scope.orgId,
      scope.sessionId,
      scope.actorId,
    );
    const accounts = await Promise.all(
      runIds.map((runId) => ledger.account(scope.orgId, runId)),
    );
    let runs = 0;
    for (const account of accounts) {
      if (account === null) continue;
      runs += 1;
      committed.usdMicros += account.committed.usdMicros;
      committed.tokens += account.committed.tokens;
    }
    return { runs, committed };
  }

  async timeline(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<RunTimelineView> {
    const { run, access } = await this.runs.streamAccess(scope, runId);
    if (access.audience !== 'owner') throw new HarnessNotFoundError();
    const events = await this.events(run);
    const timeline = foldTimeline(events, run.runId);
    const memory = recalledMemoryView(events, run.runId);
    const merge = (childRunId: string): Promise<ChildMergeReport | null> =>
      this.workspaces === null
        ? Promise.resolve(null)
        : this.workspaces.childMerge(run, childRunId);
    const children = await Promise.all(
      timeline.children.map(
        async ({ afterTurns: _a, seq: _s, model: _m, ...child }) => {
          const model = childModelLabel(timeline, { model: _m });
          return {
            ...child,
            merge: await merge(child.runId),
            ...(model !== undefined ? { model } : {}),
          };
        },
      ),
    );
    const budget = await this.budget(run);
    const answer = finalAnswer(timeline);
    const digest = isEndedStatus(run.status)
      ? foldRunDigest(
          events,
          run.runId,
          run.status,
          budget === null ? null : budget.committed,
          budget !== null && budget.estimated > 0,
        )
      : undefined;
    return {
      runId: run.runId,
      parentRunId: run.parent?.runId ?? null,
      status: run.status,
      parts: withSteps(buildParts(timeline), timeline),
      engine: timeline.engine,
      approvals: timeline.approvals.map(
        ({ afterTurns: _a, seq: _s, ...approval }) => approval,
      ),
      connects: timeline.connects.map(
        ({ afterTurns: _a, seq: _s, ...connect }) => connect,
      ),
      children,
      merge: run.parent ? await merge(run.runId) : null,
      budget,
      ...(memory !== null ? { memory } : {}),
      followups: run.followups ?? [],
      tasks: timeline.tasks,
      ...(timeline.model !== undefined ? { model: timeline.model } : {}),
      ...(timeline.compacted !== undefined
        ? { compacted: timeline.compacted }
        : {}),
      ...(timeline.gates !== undefined ? { gates: timeline.gates } : {}),
      ...(timeline.verification !== undefined
        ? { verification: timeline.verification }
        : {}),
      ...(timeline.executor !== undefined
        ? {
            executor: timeline.executor,
            reasoningEffort: timeline.reasoningEffort ?? null,
            toolsGranted: timeline.toolsGranted ?? 0,
            codingEngine: timeline.codingEngine ?? null,
          }
        : {}),
      ...(digest !== undefined ? { digest } : {}),
      ...(run.operatorStop !== undefined
        ? { operatorStop: { ...run.operatorStop } }
        : {}),
      ...(timeline.sites !== undefined ? { sites: timeline.sites } : {}),
      ...(timeline.repositoriesUnavailable !== undefined
        ? { repositoriesUnavailable: timeline.repositoriesUnavailable }
        : {}),
      ...(timeline.secretsHidden !== undefined
        ? { secretsHidden: timeline.secretsHidden }
        : {}),
      ...(answer !== null
        ? { answer: { citations: answerCitations(answer.citations) } }
        : {}),
    };
  }

  /**
   * The end-of-run summary (G-07) of a run that ended with `status`, for a
   * reader the caller already authorized (a schedule's completion notice).
   */
  async digest(
    orgId: string,
    runId: string,
    status: EndedRunStatus,
  ): Promise<RunDigest> {
    const run = { orgId, runId };
    const [events, cost] = await Promise.all([
      readRunEvents(this.journal, this.journals, run, 0),
      this.committed(run),
    ]);
    return foldRunDigest(events, runId, status, cost.amount, cost.estimated);
  }

  private async committed(
    run: Pick<HarnessRun, 'orgId' | 'runId'>,
  ): Promise<{ amount: RunDigest['cost']; estimated: boolean }> {
    if (this.ledger === null) return { amount: null, estimated: false };
    const account = await this.ledger.account(run.orgId, run.runId);
    return account === null
      ? { amount: null, estimated: false }
      : {
          amount: { ...account.committed },
          estimated: (account.estimated ?? 0) > 0,
        };
  }

  /** The run's approvals as its timeline folds them (U3), for a reader the caller already authorized (P2.11e's queue). */
  async approvals(run: HarnessRun): Promise<ApprovalView[]> {
    return foldTimeline(await this.events(run), run.runId).approvals;
  }

  /** The run's connect waits (G-12, WP-64) as its timeline folds them, for a reader the caller already authorized. */
  async connects(run: HarnessRun): Promise<ConnectView[]> {
    return foldTimeline(await this.events(run), run.runId).connects;
  }

  /**
   * C10.05 (WP-89): what a run has spent so far and its status, for the chat's live cost beside the elapsed time.
   * Owner only, as the timeline; it reads the ledger alone, never the journal, so a chat can poll it while the run
   * works.
   */
  async cost(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<{ status: string; budget: RunBudgetView | null }> {
    const { run, access } = await this.runs.streamAccess(scope, runId);
    if (access.audience !== 'owner') throw new HarnessNotFoundError();
    return { status: run.status, budget: await this.budget(run) };
  }

  /** The questions the run asked its person (G-13's inbox), for a reader the caller already authorized. */
  async questions(run: HarnessRun): Promise<QuestionView[]> {
    return foldTimeline(await this.events(run), run.runId).questions;
  }

  /** The run's unknown effects as its journal tells them (P2.9), for a reader the caller already authorized. */
  async unknownEffects(run: HarnessRun): Promise<UnknownEffectView[]> {
    return foldUnknownEffects(await this.events(run), run.runId);
  }

  /** How a job's goal changed (UI-B5, ADR-UI-01 D5), from its own journal, for a reader the caller already authorized. */
  async goalChanges(run: HarnessRun): Promise<JobGoalChange[]> {
    return jobGoalChanges(await this.events(run), run.runId);
  }

  private events(run: HarnessRun): Promise<JournalEvent[]> {
    return readRunEvents(this.journal, this.journals, run, 0);
  }

  private budget(
    run: Pick<HarnessRun, 'orgId' | 'runId'>,
  ): Promise<RunBudgetView | null> {
    return this.spend.read(run);
  }
}
