import type { PersonRuns } from '@/lib/contracts/harness/generated/ext/pipeshub/api/person-runs';
import type { ConversationRunState } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-run-state';
import type { ConversationGrants } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-grants';
import { appName } from './app-name';
import type { RequestJobs } from '@/lib/contracts/harness/generated/ext/pipeshub/api/request-jobs';
import { agentName, jobTitle } from './engine-worker';
import { toolLabel } from './step-titles';
import type { JobStatus } from './job-status';
import { JOB_SECTIONS, jobClock, operatorStopWords, sectionOf, type JobSection, type JobView } from './jobs';
import type { SandboxAgent } from './view-models';
import type { Words } from './words';
import { formatDayMonth } from './dates';

/**
 * The Jobs panel across chats (`GET /api/v1/agent-harness/runs?kind=job`, ADR-UI-01 D1), a chat's sidebar dot
 * (`harnessState`, D2) and the "Permissions in this chat" rows (`GET …/grants`, ADR-GRANT-01 D8), as view models.
 * Pure; every word through `t`.
 */

type PersonRun = PersonRuns['runs'][number];

export interface JobRow {
  runId: string;
  sessionId: string;
  agentKey?: string;
  parentRunId?: string;
  title: string;
  agent: SandboxAgent;
  status: JobStatus;
  /** The chat it came from. */
  chatTitle?: string;
  startedAt?: number;
  endedAt?: number;
  /** "An admin stopped this: maintenance". */
  stoppedBy?: string;
}

/** The person list's state as one of the six words: `working` that has not started yet is Up next. */
export function statusOfPersonRun(run: Pick<PersonRun, 'state' | 'status'>): JobStatus {
  switch (run.state) {
    case 'needs_you':
      return 'needsYou';
    case 'finished':
      return 'done';
    case 'failed':
      return 'couldntFinish';
    case 'stopped':
      return 'stopped';
    default:
      return run.status === 'queued' ? 'upNext' : 'working';
  }
}

/** A job's name: its goal's first words when the server sent a title, else its agent. */
const titleOf = (title: string | null | undefined, agent: SandboxAgent, t: Words): string =>
  title?.trim() ? jobTitle(title, t) : agent === 'Background agent' ? t('ui.job.untitled') : agent;

/**
 * A job of the person list, named by the list's own `title` (UI-B5, ADR-UI-01 D7): no brief is read for it. `whole`
 * keeps the goal's whole first line (a list row with room for it, the classic Jobs page) instead of a pill's words.
 */
export function jobRowOfPersonRun(run: PersonRun, t: Words, options: { whole?: boolean } = {}): JobRow {
  const agent = agentName(run.engine);
  return {
    runId: run.runId,
    sessionId: run.sessionId,
    ...(run.agentKey ? { agentKey: run.agentKey } : {}),
    ...(run.parentRunId ? { parentRunId: run.parentRunId } : {}),
    title: options.whole && run.title?.trim() ? run.title.trim() : titleOf(run.title, agent, t),
    agent,
    status: statusOfPersonRun(run),
    ...(run.conversationTitle ? { chatTitle: run.conversationTitle } : {}),
    startedAt: run.createdAt,
    ...(run.endedAt ? { endedAt: run.endedAt } : {}),
    ...(run.operatorStop ? { stoppedBy: operatorStopWords(run.operatorStop.reason, t) } : {}),
  };
}

/** A chat's own jobs, in the same rows. */
export function jobRowOfView(job: JobView, sessionId: string, agentKey?: string): JobRow {
  return {
    runId: job.runId,
    sessionId,
    ...(agentKey ? { agentKey } : {}),
    ...(job.parentRunId ? { parentRunId: job.parentRunId } : {}),
    title: job.title,
    agent: job.agent,
    status: job.status,
    ...(job.startedAt ? { startedAt: job.startedAt } : {}),
    ...(job.finishedAt ? { endedAt: job.finishedAt } : {}),
    ...(job.stoppedBy ? { stoppedBy: job.stoppedBy } : {}),
  };
}

/** Jobs a request handed out together, in one section; a request with one job there is just that job. */
export interface JobGroup {
  key: string;
  /** The request's name (its chat's title), shown only when it holds more than one job. */
  title?: string;
  rows: JobRow[];
}

export interface JobsSection {
  id: JobSection;
  label: string;
  count: number;
  groups: JobGroup[];
}

/** Sections in the design's order (Needs you, Working, Up next, Finished); empty ones are left out. */
export function jobsSections(rows: ReadonlyArray<JobRow>, t: Words, cleared: ReadonlySet<string> = new Set()): JobsSection[] {
  return JOB_SECTIONS.map((id) => {
    const mine = rows.filter((r) => sectionOf(r.status) === id && !(id === 'finished' && cleared.has(r.runId)));
    const groups: JobGroup[] = [];
    for (const row of mine) {
      const key = row.parentRunId ?? row.runId;
      const group = groups.find((g) => g.key === key);
      if (group) group.rows.push(row);
      else groups.push({ key, rows: [row] });
    }
    for (const g of groups) if (g.rows.length > 1) g.title = g.rows[0].chatTitle ?? t('ui.jobs.request');
    return { id, label: t(`ui.jobs.section.${id}`), count: mine.length, groups };
  }).filter((s) => s.count > 0);
}

/** Finished folds while anything still runs (design §6B.42); with nothing else to show it stays open. */
export const finishedFolds = (sections: ReadonlyArray<JobsSection>): boolean => sections.some((s) => s.id !== 'finished');

/** A row's right-hand word: its clock while it works, or the one word that matters. */
export function rowMeta(row: JobRow, now: number, t: Words): string {
  switch (row.status) {
    case 'working':
      return row.startedAt ? jobClock(Math.max(0, now - row.startedAt), t) : t('ui.status.working');
    case 'done':
      return row.startedAt && row.endedAt ? t('ui.job.doneIn', { time: jobClock(row.endedAt - row.startedAt, t) }) : t('ui.status.done');
    default:
      return t(`ui.status.${row.status}`);
  }
}

/** A chat row's dot in the sidebar: needs you first, then working, then finished and not seen yet. */
export type ChatDot = 'needsYou' | 'working' | 'finished';

export function chatDot(state: ConversationRunState | null | undefined): ChatDot | null {
  if (!state) return null;
  if (state.needsYou || state.runState === 'needs_you') return 'needsYou';
  if (state.runState === 'working' || state.activeRun) return 'working';
  return state.finishedUnseen ? 'finished' : null;
}

/** A sidebar row's one mark (D10), with where a finished mark came from, so its words can say which. */
export interface SidebarDot {
  dot: ChatDot;
  /** The server's "finished since you last looked", as opposed to an answer this tab saw finish out of view. */
  unseen?: boolean;
}

/**
 * The server's state merged with what this tab knows: needs you (also an ask this tab's own stream holds open, D10:
 * the row it streams says so over its spinner), then working (also a run streaming here before the list says so),
 * then finished and not seen. The chat open now shows no finished mark: it is being looked at.
 */
export function sidebarDot(
  state: ConversationRunState | null | undefined,
  local: { streaming: boolean; unread: boolean; active: boolean; waiting?: boolean },
): SidebarDot | null {
  const server = chatDot(state);
  if (server === 'needsYou' || local.waiting === true) return { dot: 'needsYou' };
  if (local.streaming || server === 'working') return { dot: 'working' };
  if (local.active) return null;
  if (server === 'finished') return { dot: 'finished', unseen: true };
  return local.unread ? { dot: 'finished' } : null;
}

const DOT_RANK: Record<SidebarDot['dot'], number> = { needsYou: 3, working: 2, finished: 1 };

/**
 * The one mark a folded list shows for the chats it hides (CJ14-FU-2): the strongest of their marks, needs you over
 * working over finished and not seen, so folding Recents never hides that a chat waits on the person.
 */
export function strongestDot(marks: ReadonlyArray<SidebarDot | null>): SidebarDot | null {
  let best: SidebarDot | null = null;
  for (const mark of marks) {
    if (mark === null) continue;
    if (best === null || DOT_RANK[mark.dot] > DOT_RANK[best.dot] || (mark.dot === best.dot && mark.unseen && !best.unseen)) best = mark;
  }
  return best;
}

/** One "Allow for this chat" the conversation holds, as the "Permissions in this chat" row lists it. */
export interface GrantView {
  id: string;
  /** "Slack: send message to #sales". */
  title: string;
  detail: string;
  /** ADR-GRANT-01 D9: a call a person declined for their runs here, rather than one they allowed. */
  declined: boolean;
}

/**
 * Who allowed it is named only where it can differ: in a shared chat ("Allowed by Ana", "Allowed by you"). In a
 * chat nobody else takes part in, it was always the owner, so the row says only when. Without `who`, the name is
 * always given.
 */
export function grantViews(grants: ConversationGrants['grants'], t: Words, locale?: string, who?: { me: string | null; shared: boolean }): GrantView[] {
  return grants.map((g) => {
    const tool = toolLabel(g.tool.name);
    const action = g.destination ? t('ui.grants.toolTo', { tool, destination: g.destination }) : tool;
    const when = formatDayMonth(g.grantedAt, locale);
    const byMe = who?.me != null && g.grantedBy === who.me;
    const named = who ? who.shared && (byMe || !!g.grantedByName) : !!g.grantedByName;
    const declined = g.decision === 'declined';
    const [on, byYou, by] = declined
      ? (['ui.grants.declinedOn', 'ui.grants.declinedByYou', 'ui.grants.declinedBy'] as const)
      : (['ui.grants.allowedOn', 'ui.grants.allowedByYou', 'ui.grants.allowedBy'] as const);
    return {
      id: g.grantId,
      title: `${appName(g.tool.app)}: ${action}`,
      detail: !named ? t(on, { when }) : byMe ? t(byYou, { when }) : t(by, { name: g.grantedByName ?? '', when }),
      declined,
    };
  });
}

/** A request with several jobs, opened from its name in the Jobs list (design §6B.42; UI-B5, ADR-UI-01 D6). */
export interface RequestView {
  /** What the person asked; null once erased. */
  ask: string | null;
  /** "Ran 2 jobs". */
  ran: string;
  /** "1 of 2 back" while they run, "All back" once every one ended. */
  progress: string;
  done: boolean;
  /** Oldest first; a job that continues another sits one level under it. */
  rows: Array<JobRow & { depth: number }>;
}

export function requestView(req: RequestJobs, t: Words, agentKey?: string): RequestView {
  const rows: Array<JobRow & { depth: number }> = [];
  const known = new Set(req.jobs.map((j) => j.runId));
  const depthOf = new Map<string, number>();
  const place = (job: RequestJobs['jobs'][number]) => {
    const agent = agentName(job.engine);
    const parent = job.continuesRunId && known.has(job.continuesRunId) ? job.continuesRunId : null;
    const depth = parent && depthOf.has(parent) ? (depthOf.get(parent) ?? 0) + 1 : 0;
    depthOf.set(job.runId, depth);
    const row = {
      runId: job.runId,
      sessionId: req.request.sessionId,
      ...(agentKey ? { agentKey } : {}),
      parentRunId: req.request.runId,
      title: titleOf(job.title, agent, t),
      agent,
      status: statusOfPersonRun(job),
      startedAt: job.createdAt,
      ...(job.endedAt ? { endedAt: job.endedAt } : {}),
      depth,
    };
    // A replacement goes right under the job it replaced (after that job's own replacements).
    const from = parent ? rows.findIndex((r) => r.runId === parent) : -1;
    if (from < 0) rows.push(row);
    else {
      let at = from;
      while (at + 1 < rows.length && rows[at + 1].depth > rows[from].depth) at += 1;
      rows.splice(at + 1, 0, row);
    }
  };
  // Oldest first, so a job is placed before any job that continues it.
  for (const job of [...req.jobs].sort((a, b) => a.createdAt - b.createdAt)) place(job);
  const done = req.total > 0 && req.back >= req.total;
  return {
    ask: req.request.ask,
    ran: t('ui.jobs.requestView.ran', { count: req.total }),
    progress: done ? t('ui.jobs.requestView.allBack') : t('ui.jobs.requestView.back', { back: req.back, total: req.total }),
    done,
    rows,
  };
}
