'use client';

import { create } from 'zustand';
import { streamSSEGet } from '@/lib/api';
import { StreamError } from '@/lib/api/stream-errors';
import { isPermanentRefusal, reconnectDelayMs, waitToRetry, STREAM_IDLE_TIMEOUT_MS } from '@/chat/durable-run-stream';
import type { JobBrief } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief';
import type { JobBriefHistory } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief-history';
import { runStillActive } from '@/chat/durable-run-ui';
import { emptyJobStream, endsJobStream, reduceJobFrame, type JobStreamState } from './job-stream';
import { JobsApi, jobStreamUrl, type JobTimeline, type SessionRoute } from './jobs-api';
import { refreshJobLists } from './swr-keys';

/**
 * Where each job of the open chat stands, fed by the job's own stream instead of a 3 s timeline poll (ADR-UI-01
 * D4). A module store, so a job keeps its state while its panel opens and closes, and one subscription serves every
 * place that shows the job (its card, the board, the Needs you line, the open agent).
 *
 * At most `MAX_FOLLOWED` streams are open at once (the design's four sandboxes at a time, and well under the six
 * connections a browser gives one origin beside the chat's own stream); the rest wait their turn. An ended job's
 * stream replays its journal and closes, so a reload rebuilds every card from the same frames. Once a job ends, its
 * timeline is read once for what the stream does not carry (an operator's stop, G11).
 *
 * ADR-CJ-01: everything about one job that more than one place shows lives here, keyed by its run id, never in a
 * component: its stream, its one timeline read (shared by its card, its steps and its panel), and how a Stop of it
 * went. A card is drawn again when its answer settles; what it said must not go with the old copy.
 */

export const MAX_FOLLOWED = 4;

export interface JobExtra {
  /** The engine kind its timeline names. */
  engine?: string | null;
  operatorStop?: { reason: string } | null;
  /** The one read after it ended is done. */
  settled?: boolean;
}

/** How a Stop of the job went (D11): sent, refused because it had already ended (409), or failed. */
export type JobStopState = 'sending' | 'sent' | 'gone' | 'failed';

/** The job's timeline as last read, and whether that read saw it ended (no read after that one is needed). */
export interface JobTimelineEntry {
  view: JobTimeline;
  ended: boolean;
}

interface JobsState {
  streams: Record<string, JobStreamState>;
  extras: Record<string, JobExtra>;
  briefs: Record<string, JobBrief | 'loading' | 'missing'>;
  /** How each job's goal changed (its Brief's "How it changed"), read once per opening of its Brief. */
  histories: Record<string, JobBriefHistory | 'loading' | 'missing'>;
  /** Finished jobs cleared from the Jobs panel; their cards stay in the chat. */
  cleared: ReadonlyArray<string>;
  stops: Record<string, JobStopState>;
  timelines: Record<string, JobTimelineEntry>;
  /** The last timeline read of the job failed (and none before it succeeded). */
  timelineFailed: Record<string, boolean>;
  apply: (runId: string, frame: unknown, id?: string) => void;
  setExtra: (runId: string, extra: JobExtra) => void;
  setStop: (runId: string, stop: JobStopState) => void;
  setTimeline: (runId: string, entry: JobTimelineEntry | null) => void;
  setBrief: (runId: string, brief: JobBrief | 'loading' | 'missing') => void;
  setHistory: (runId: string, history: JobBriefHistory | 'loading' | 'missing') => void;
  clear: (runIds: ReadonlyArray<string>) => void;
  unclear: (runIds: ReadonlyArray<string>) => void;
  /** A job to open in its chat's right column once the chat shows it (the rail's Jobs list asked for it). */
  focus: { runId: string; tab: 'brief' | 'activity' | 'machine'; at: number } | null;
  requestFocus: (runId: string, tab?: 'brief' | 'activity' | 'machine') => void;
  takeFocus: () => void;
  /** The rail's Jobs list (every job across your chats) is open. */
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
}

export const useJobsStore = create<JobsState>()((set) => ({
  streams: {},
  extras: {},
  briefs: {},
  histories: {},
  cleared: [],
  stops: {},
  timelines: {},
  timelineFailed: {},
  apply: (runId, frame, id) =>
    set((s) => ({ streams: { ...s.streams, [runId]: reduceJobFrame(s.streams[runId] ?? emptyJobStream(runId), frame, id) } })),
  setExtra: (runId, extra) => set((s) => ({ extras: { ...s.extras, [runId]: { ...s.extras[runId], ...extra } } })),
  setStop: (runId, stop) => set((s) => ({ stops: { ...s.stops, [runId]: stop } })),
  setTimeline: (runId, entry) =>
    set((s) =>
      entry
        ? { timelines: { ...s.timelines, [runId]: entry }, timelineFailed: { ...s.timelineFailed, [runId]: false } }
        : { timelineFailed: { ...s.timelineFailed, [runId]: true } },
    ),
  setBrief: (runId, brief) => set((s) => ({ briefs: { ...s.briefs, [runId]: brief } })),
  setHistory: (runId, history) => set((s) => ({ histories: { ...s.histories, [runId]: history } })),
  clear: (runIds) => set((s) => ({ cleared: [...new Set([...s.cleared, ...runIds])] })),
  unclear: (runIds) => set((s) => ({ cleared: s.cleared.filter((id) => !runIds.includes(id)) })),
  focus: null,
  requestFocus: (runId, tab = 'activity') => set({ focus: { runId, tab, at: Date.now() } }),
  takeFocus: () => set({ focus: null }),
  drawerOpen: false,
  setDrawerOpen: (open) => set({ drawerOpen: open }),
}));

interface Follow {
  session: SessionRoute;
  controller: AbortController;
  users: number;
  started: boolean;
  /** One of its followers may read the job's timeline (its owner): the stream's end brings the one settle read. */
  settle: boolean;
  /** When following began (ms): an end before it is a replay of the journal, not news. */
  since: number;
}

/** Slack for the server's clock against this one when telling a live end from a replayed one. */
const END_SKEW_MS = 5_000;

/** The job ended while this page followed it, so the sidebar's dot and the Jobs list are behind. */
const endedLive = (state: JobStreamState | undefined, since: number): boolean =>
  Boolean(state?.ended) && state?.finishedAt !== undefined && state.finishedAt >= since - END_SKEW_MS;

const follows = new Map<string, Follow>();
const queue: string[] = [];

const openCount = () => [...follows.values()].filter((f) => f.started).length;

const timelineReads = new Map<string, Promise<JobTimeline | null>>();

/**
 * The job's timeline, read through one place (D13): at most one read while it runs and one once it ended, whoever
 * asks (its card's settle read, its steps, its panel and the panel's footer), and never twice at once. `force`: read
 * again now, after something changed it (a decision on one of its asks). Null when it cannot be read.
 */
export function loadJobTimeline(
  session: SessionRoute,
  runId: string,
  options: { ended: boolean; force?: boolean },
): Promise<JobTimeline | null> {
  const known = useJobsStore.getState().timelines[runId];
  if (!options.force && known && (known.ended || !options.ended)) return Promise.resolve(known.view);
  const key = `${runId}|${options.ended ? 'ended' : 'live'}${options.force ? '|force' : ''}`;
  const reading = timelineReads.get(key);
  if (reading) return reading;
  const read = JobsApi.timeline(session, runId)
    .then((view) => {
      // The server says when its view is final. The stream can end before the projection catches up (usage folded
      // in, the run's terminal state), and a view kept as final then would never be read again (R31-AXET-FU-3).
      const ended = !runStillActive(view.status);
      useJobsStore.getState().setTimeline(runId, { view, ended });
      if (ended) {
        useJobsStore.getState().setExtra(runId, {
          settled: true,
          // `loop` is the executor of a run with no engine, not an engine: named as one, a loop job's card read
          // "Done · loop" once it settled, where it read the model while it ran.
          engine: view.codingEngine ?? (view.executor && view.executor !== 'loop' ? view.executor : null),
          operatorStop: view.operatorStop ? { reason: view.operatorStop.reason } : null,
        });
      }
      return view;
    })
    .catch(() => {
      // Whoever shows it keeps what the stream said; only what the timeline adds is missing.
      useJobsStore.getState().setTimeline(runId, null);
      return null;
    })
    .finally(() => timelineReads.delete(key));
  timelineReads.set(key, read);
  return read;
}

/** The one read after the job ended (an operator's stop, its engine); only for a viewer who may read it. */
export async function readSettled(session: SessionRoute, runId: string): Promise<void> {
  const store = useJobsStore.getState();
  if (store.extras[runId]?.settled) return;
  store.setExtra(runId, { settled: true });
  await loadJobTimeline(session, runId, { ended: true });
}

/**
 * Stop the job, one click (D11). Its outcome is the job's, so every place that shows the job words it, and a card
 * drawn again keeps "It had already finished" (a 409) instead of losing it with the copy that sent it.
 */
export async function stopJob(session: SessionRoute, runId: string): Promise<JobStopState> {
  const store = useJobsStore.getState();
  const now = store.stops[runId];
  if (now === 'sending' || now === 'sent' || now === 'gone') return now;
  store.setStop(runId, 'sending');
  let outcome: JobStopState;
  try {
    outcome = (await JobsApi.stop(session, runId)) ? 'sent' : 'gone';
  } catch {
    outcome = 'failed';
  }
  useJobsStore.getState().setStop(runId, outcome);
  return outcome;
}

async function run(runId: string, follow: Follow): Promise<void> {
  follow.started = true;
  const { signal } = follow.controller;
  let failures = 0;
  while (!signal.aborted) {
    const state = useJobsStore.getState().streams[runId];
    if (state?.ended) break;
    let error: Error | null = null;
    let progressed = false;
    let ended = false;
    await streamSSEGet(jobStreamUrl(follow.session, runId), {
      signal,
      idleTimeoutMs: STREAM_IDLE_TIMEOUT_MS,
      ...(state?.lastEventId ? { headers: { 'Last-Event-ID': state.lastEventId } } : {}),
      onEvent: (event) => {
        if (ended) return;
        progressed = true;
        useJobsStore.getState().apply(runId, event, event.id);
        if (endsJobStream(event, runId)) ended = true;
      },
      onError: (e) => {
        error = e;
      },
    });
    if (ended) break;
    if (signal.aborted) break;
    const status = (error as Error | null) instanceof StreamError ? ((error as unknown) as StreamError).status : undefined;
    if (isPermanentRefusal(status)) break;
    failures = progressed ? 1 : failures + 1;
    await waitToRetry(reconnectDelayMs(failures), signal);
  }
  follow.started = false;
  const final = useJobsStore.getState().streams[runId];
  if (final?.ended && follow.settle) void readSettled(follow.session, runId);
  if (endedLive(final, follow.since)) refreshJobLists();
  if (follows.get(runId) === follow && !signal.aborted) follows.delete(runId);
  pump();
}

function pump(): void {
  while (queue.length && openCount() < MAX_FOLLOWED) {
    const runId = queue.shift() as string;
    const follow = follows.get(runId);
    if (follow && !follow.started) void run(runId, follow);
  }
}

/**
 * Follow one job's stream while something shows it; returns the release. Ended and settled jobs are not re-read.
 * `settle`: this follower may read the job's timeline (D14: a shared chat's viewer may not), so its end is read once.
 */
export function followJob(session: SessionRoute, runId: string, options: { settle?: boolean } = {}): () => void {
  const settle = options.settle ?? true;
  const known = useJobsStore.getState().streams[runId];
  if (known?.ended) {
    if (settle) void readSettled(session, runId);
    return () => undefined;
  }
  let follow = follows.get(runId);
  if (!follow) {
    follow = { session, controller: new AbortController(), users: 0, started: false, settle, since: Date.now() };
    follows.set(runId, follow);
    queue.push(runId);
  }
  follow.settle = follow.settle || settle;
  follow.users += 1;
  pump();
  const mine = follow;
  return () => {
    mine.users -= 1;
    if (mine.users > 0) return;
    mine.controller.abort();
    follows.delete(runId);
    const at = queue.indexOf(runId);
    if (at >= 0) queue.splice(at, 1);
    pump();
  };
}

/**
 * How a job's goal changed. Read each time its Brief opens (a message or a choice can come at any point while it
 * runs), never twice at once; a job whose history cannot be read keeps its brief without the section.
 */
export async function loadHistory(session: SessionRoute, runId: string): Promise<void> {
  if (useJobsStore.getState().histories[runId] === 'loading') return;
  const known = useJobsStore.getState().histories[runId];
  if (!known) useJobsStore.getState().setHistory(runId, 'loading');
  try {
    useJobsStore.getState().setHistory(runId, await JobsApi.briefHistory(session, runId));
  } catch {
    if (!known || known === 'missing') useJobsStore.getState().setHistory(runId, 'missing');
  }
}

/** Each job's brief, read once (its Brief tab; the Jobs lists name jobs from their own `title`). */
export async function loadBrief(session: SessionRoute, runId: string): Promise<void> {
  if (useJobsStore.getState().briefs[runId]) return;
  useJobsStore.getState().setBrief(runId, 'loading');
  try {
    useJobsStore.getState().setBrief(runId, await JobsApi.brief(session, runId));
  } catch {
    // Not a job, or not this person's: the panel names it by its agent and the Brief tab says so.
    useJobsStore.getState().setBrief(runId, 'missing');
  }
}
