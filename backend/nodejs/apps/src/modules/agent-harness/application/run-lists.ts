import {
  type HarnessRun,
  RunOutcome,
  RunStatus,
  TERMINAL_RUN_STATUSES,
  type RunOperatorStop,
} from '../domain/execution';
import {
  ConversationRunState,
  conversationRunStates,
  DEFAULT_RUN_LIST_LIMIT,
  encodeRunListCursor,
  ListedRun,
  MAX_CONVERSATION_STATES,
  MAX_RUN_LIST_LIMIT,
  RunListCursor,
  RunListKind,
  RunListState,
  runListState,
} from '../domain/run-lists';
import { jobTitle } from '../domain/job-brief';
import type { AgentNameReader } from '../ports/agent-names';
import type { ChatTitleReader } from '../ports/chat-titles';
import type { SessionExecutionScope } from '../ports/chat-session-repository';
import type { ConversationSeenMarks, RunLists } from '../ports/run-lists';
import type { RunInputSeal } from './run-input-seal';
import type { SessionRunService } from './session-run-service';

/** One run or job as a person's list shows it (`person-runs.schema.json`): plain fields, no internals. */
export interface PersonRunView {
  runId: string;
  sessionId: string;
  /** A job's parent run (the answer that handed it the work); null on a chat's own answer. */
  parentRunId: string | null;
  job: boolean;
  agentKey: string | null;
  agentName: string | null;
  conversationTitle: string | null;
  /** UI-B5: a job's title, its goal's first line; absent on a chat's own answer and once erased. */
  title?: string;
  /** The coding engine a job runs on (`claude_code`); null: the native loop. */
  engine: string | null;
  state: RunListState;
  status: RunStatus;
  outcome: RunOutcome;
  createdAt: number;
  updatedAt: number;
  /** When it ended (its last change); null while it runs. */
  endedAt: number | null;
  /** G11: an operator stopped it (scope, reason, when); absent otherwise. */
  operatorStop?: RunOperatorStop;
}

export interface PersonRunPage {
  runs: PersonRunView[];
  /** Pass as `cursor` for the next page; null on the last. */
  nextCursor: string | null;
}

/** What the caller filters by; the org and the person are never the request's. */
export interface PersonRunFilter {
  states?: readonly RunListState[];
  agentKey?: string;
  kind?: RunListKind;
  createdFrom?: number;
  createdTo?: number;
  after?: RunListCursor;
  limit?: number;
}

export interface RunListServiceDeps {
  runs: RunLists;
  seen: ConversationSeenMarks;
  /** Who may open a conversation (owner or shared reader), for the seen mark. */
  access: Pick<SessionRunService, 'assertAccess'>;
  names?: { agents?: AgentNameReader | null; chats?: ChatTitleReader | null };
  /** Opens a job's sealed goal for its title (UI-B5, UIP2-FU-1); absent: jobs list without titles. */
  inputs?: Pick<RunInputSeal, 'open'>;
  now?: () => number;
}

/**
 * ADR-UI-01 D1, D2 (G1, G7): a person's runs and jobs across their conversations (an agent's Runs tab is the same
 * list with its agent), the run state of their chat list, and their "seen up to" mark. Every read is the caller's
 * own: the org and the person come from verified auth, and a filter can only narrow it.
 */
export class RunListService {
  private readonly now: () => number;

  constructor(private readonly deps: RunListServiceDeps) {
    this.now = deps.now ?? Date.now;
  }

  async personRuns(
    caller: { orgId: string; actorId: string },
    filter: PersonRunFilter,
  ): Promise<PersonRunPage> {
    const limit = Math.min(
      Math.max(1, filter.limit ?? DEFAULT_RUN_LIST_LIMIT),
      MAX_RUN_LIST_LIMIT,
    );
    const listed = await this.deps.runs.personRuns({
      orgId: caller.orgId,
      actorId: caller.actorId,
      ...(filter.states !== undefined ? { states: filter.states } : {}),
      ...(filter.agentKey !== undefined ? { agentKey: filter.agentKey } : {}),
      ...(filter.kind !== undefined ? { kind: filter.kind } : {}),
      ...(filter.createdFrom !== undefined
        ? { createdFrom: filter.createdFrom }
        : {}),
      ...(filter.createdTo !== undefined
        ? { createdTo: filter.createdTo }
        : {}),
      ...(filter.after !== undefined ? { after: filter.after } : {}),
      limit: limit + 1,
    });
    const page = listed.slice(0, limit);
    const last = page[page.length - 1];
    const runs = page.map((item) => item.run);
    const [agents, chats, titles] = await Promise.all([
      this.deps.names?.agents?.names(caller.orgId, [
        ...new Set(
          runs.flatMap((run) =>
            run.agentKey !== undefined ? [run.agentKey] : [],
          ),
        ),
      ]) ?? new Map<string, string>(),
      this.deps.names?.chats?.titles(caller.orgId, [
        ...new Set(runs.map((run) => run.sessionId)),
      ]) ?? new Map<string, string>(),
      this.jobTitles(runs),
    ]);
    return {
      runs: page.map((item) => personRunView(item, agents, chats, titles)),
      nextCursor:
        listed.length > limit && last !== undefined
          ? encodeRunListCursor({
              createdAt: last.run.createdAt,
              runId: last.run.runId,
            })
          : null,
    };
  }

  /**
   * Each job's title from its goal, so the Jobs panel needs no brief per row. The goal is the caller's own
   * conversation's content (the list is theirs alone); one that cannot be opened lists without a title.
   */
  private async jobTitles(
    runs: readonly HarnessRun[],
  ): Promise<Map<string, string>> {
    const inputs = this.deps.inputs;
    const titles = new Map<string, string>();
    if (inputs === undefined) return titles;
    await Promise.all(
      runs
        .filter((run) => run.parent !== undefined && run.input != null)
        .map(async (run) => {
          try {
            const title = jobTitle((await inputs.open(run)).input?.query);
            if (title !== undefined) titles.set(run.runId, title);
          } catch {
            // Sealed under a key this node cannot open: listed without its title.
          }
        }),
    );
    return titles;
  }

  /** The run state of the caller's chat list, one entry per conversation asked for (at most `MAX_CONVERSATION_STATES`). */
  async conversationStates(
    caller: { orgId: string; actorId: string },
    sessionIds: readonly string[],
  ): Promise<ConversationRunState[]> {
    const ids = [...new Set(sessionIds)].slice(0, MAX_CONVERSATION_STATES);
    if (ids.length === 0) return [];
    const [open, ended, seen] = await Promise.all([
      this.deps.runs.openRunsOfSessions(caller.orgId, ids),
      this.deps.runs.lastEndedRuns(caller.orgId, caller.actorId, ids),
      this.deps.seen.read(caller.orgId, caller.actorId, ids),
    ]);
    return conversationRunStates(caller.actorId, ids, open, ended, seen);
  }

  /**
   * The caller opened the conversation: their mark moves to now. A conversation they may not open is not found
   * (the same answer as one that does not exist).
   */
  async markSeen(scope: SessionExecutionScope): Promise<{ seenUpTo: number }> {
    await this.deps.access.assertAccess(scope);
    const held = await this.deps.seen.mark(
      scope.orgId,
      scope.sessionId,
      scope.actorId,
      this.now(),
    );
    return { seenUpTo: held ?? this.now() };
  }
}

function personRunView(
  { run, engine }: ListedRun,
  agents: ReadonlyMap<string, string>,
  chats: ReadonlyMap<string, string>,
  titles: ReadonlyMap<string, string>,
): PersonRunView {
  const ended = TERMINAL_RUN_STATUSES.has(run.status);
  const title = titles.get(run.runId);
  return {
    runId: run.runId,
    sessionId: run.sessionId,
    parentRunId: run.parent?.runId ?? null,
    job: run.parent !== undefined,
    agentKey: run.agentKey ?? null,
    agentName:
      run.agentKey !== undefined ? (agents.get(run.agentKey) ?? null) : null,
    conversationTitle: chats.get(run.sessionId) || null,
    ...(title !== undefined ? { title } : {}),
    engine,
    state: runListState(run),
    status: run.status,
    outcome: run.outcome ?? null,
    createdAt: run.createdAt,
    updatedAt: run.updatedAt,
    endedAt: ended ? run.updatedAt : null,
    ...(run.operatorStop !== undefined
      ? { operatorStop: { ...run.operatorStop } }
      : {}),
  };
}
