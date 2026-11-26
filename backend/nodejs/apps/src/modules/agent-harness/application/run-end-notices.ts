import { randomUUID } from 'node:crypto';
import {
  detachedInterval,
  forEnumeratedOrg,
} from '../../../libs/context/orgContext';
import { isPaused, runEndVerdict, type RunEndVerdict } from '../domain/inbox';
import type { HarnessRun } from '../domain/execution';
import type { NotifyChannel } from '../domain/schedules';
import type { ChatTitleReader } from '../ports/chat-titles';
import type {
  InboxPreferenceStore,
  RunEndNoticeClaims,
  RunEndNotifier,
  RunWatchLeases,
} from '../ports/inbox';
import type { TriggerSettings } from '../ports/schedules';
import { harnessLogMeta } from './harness-log';

const NOTICE_JOB = 'agent-harness.inbox-run-notice';
const WATCH_JOB = 'agent-harness.inbox-watch';

/**
 * Who a Slack bot's open stream watches as (ADR-OWD-09 D-SLACK-ASK): the person's Slack thread, a viewer of its own
 * beside the person's web app, so a run that ends while only the web app watches is still posted to the thread.
 */
export const slackThreadViewer = (userId: string): string => `slack:${userId}`;

/** How long one watch lease lasts; an open stream renews it at half that. */
export const WATCH_LEASE_MS = 60_000;

interface Logger {
  info(message: string, meta?: Record<string, unknown>): void;
  warn(message: string, meta?: Record<string, unknown>): void;
}

export type RunEndNoticeOutcome =
  | 'noticed'
  | 'watched'
  | 'claimed_elsewhere'
  | 'failed'
  | Extract<RunEndVerdict, { notify: false }>['reason'];

/**
 * Finished-run notices (G2, ADR-INBOX-01 §Finished runs). A person's own interactive run that ends while they are not
 * watching its conversation leaves a notice in their Inbox ("finished" or "couldn't finish"), and by email or Slack
 * where they opted in and the org allows the channel. Told of every run end by the execution service; never throws.
 */
export class RunEndNoticeService {
  constructor(
    private readonly deps: {
      leases: RunWatchLeases;
      claims: RunEndNoticeClaims;
      preferences: InboxPreferenceStore;
      notifier: RunEndNotifier;
      /** The org's allowed channels beyond the Inbox (the schedules' setting); absent: the Inbox only. */
      channels?: Pick<TriggerSettings, 'notifyChannels'>;
      titles?: ChatTitleReader;
      /** After a notice is written, so the person's open sessions recount. */
      noticed?: (orgId: string, userId: string) => void;
      now?: () => number;
      logger?: Logger;
    },
  ) {}

  private now(): number {
    return this.deps.now?.() ?? Date.now();
  }

  async runEnded(run: HarnessRun): Promise<RunEndNoticeOutcome> {
    try {
      // The run names its org; the end may be told from a sweep or a worker's commit with none bound.
      return await forEnumeratedOrg(run.orgId, NOTICE_JOB, () =>
        this.decide(run),
      );
    } catch (error) {
      this.deps.logger?.warn(
        'inbox.run_notice_failed',
        harnessLogMeta('inbox.run_notice_failed', {
          orgId: run.orgId,
          runId: run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return 'failed';
    }
  }

  private async decide(run: HarnessRun): Promise<RunEndNoticeOutcome> {
    const verdict = runEndVerdict(run);
    if (!verdict.notify) return verdict.reason;
    const now = this.now();
    if (
      await this.deps.leases.watching(
        run.orgId,
        run.actorId,
        run.sessionId,
        now,
      )
    )
      return 'watched';
    if (
      !(await this.deps.claims.claimRunNotice(
        run.orgId,
        run.runId,
        run.actorId,
        now,
      ))
    )
      return 'claimed_elsewhere';
    const [prefs, allowed, titles] = await Promise.all([
      this.deps.preferences.preferences(run.orgId, run.actorId),
      this.allowedChannels(run.orgId),
      this.deps.titles
        ?.titles(run.orgId, [run.sessionId])
        .catch(() => undefined),
    ]);
    const quiet = isPaused(prefs, now);
    const channels: NotifyChannel[] = ['in_app'];
    if (!quiet) {
      if (prefs.runNotices.email && allowed.includes('email'))
        channels.push('email');
      if (prefs.runNotices.slack && allowed.includes('slack'))
        channels.push('slack');
    }
    await this.deps.notifier.runFinished({
      orgId: run.orgId,
      userId: run.actorId,
      sessionId: run.sessionId,
      ...(run.agentKey !== undefined && run.agentKey.length > 0
        ? { agentKey: run.agentKey }
        : {}),
      runId: run.runId,
      finished: verdict.finished,
      cutShort: verdict.cutShort,
      chatTitle: titles?.get(run.sessionId) ?? '',
      channels,
      quiet,
    });
    this.deps.noticed?.(run.orgId, run.actorId);
    this.deps.logger?.info(
      'inbox.run_noticed',
      harnessLogMeta('inbox.run_noticed', {
        orgId: run.orgId,
        runId: run.runId,
        finished: verdict.finished,
        channels: channels.join(','),
      }),
    );
    return 'noticed';
  }

  private async allowedChannels(
    orgId: string,
  ): Promise<readonly NotifyChannel[]> {
    if (this.deps.channels === undefined) return ['in_app'];
    try {
      return await this.deps.channels.notifyChannels(orgId);
    } catch {
      return ['in_app'];
    }
  }
}

/**
 * Who is watching which conversation (ADR-INBOX-01 §Watching): an open stream holds a lease, renewed at half its
 * length while the stream is open and given back when it closes. A lost renewal only means a notice may be written
 * for a run the person did watch end: never the other way round.
 */
export class RunWatchRegistry {
  constructor(
    private readonly leases: RunWatchLeases,
    private readonly options: {
      now?: () => number;
      leaseMs?: number;
      newId?: () => string;
    } = {},
  ) {}

  /** Starts watching; the returned function stops. Never throws: watching is a hint, the stream goes on without it. */
  watch(viewer: {
    orgId: string;
    userId: string;
    sessionId: string;
  }): () => void {
    const leaseMs = this.options.leaseMs ?? WATCH_LEASE_MS;
    const watcherId = this.options.newId?.() ?? randomUUID();
    const now = (): number => this.options.now?.() ?? Date.now();
    // The renewals tick detached from the request that opened the stream, so each binds the viewer's org itself.
    const asViewer = <T>(work: () => Promise<T>): Promise<T> =>
      forEnumeratedOrg(viewer.orgId, WATCH_JOB, work);
    const renew = (): void => {
      void asViewer(() =>
        this.leases.watch({ ...viewer, watcherId, until: now() + leaseMs }),
      ).catch(() => undefined);
    };
    renew();
    const timer = detachedInterval(
      renew,
      Math.max(1_000, Math.floor(leaseMs / 2)),
    );
    let stopped = false;
    return () => {
      if (stopped) return;
      stopped = true;
      clearInterval(timer);
      void asViewer(() => this.leases.unwatch(viewer.orgId, watcherId)).catch(
        () => undefined,
      );
    };
  }
}
