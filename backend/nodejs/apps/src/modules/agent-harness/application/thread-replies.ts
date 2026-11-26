import type { HarnessRun } from '../domain/execution';
import { conversationLink } from '../domain/run-end-notice';
import type { ConversationReader } from '../ports/conversation-reader';
import type { RunEndNoticeClaims, RunWatchLeases } from '../ports/inbox';
import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import { harnessLogMeta } from './harness-log';
import { slackThreadViewer } from './run-end-notices';

const REPLY_JOB = 'agent-harness.thread-reply';

/** How a run ended, as its thread is told. */
export type ThreadReplyEnd = 'answered' | 'failed' | 'cancelled';

/** Posts a run's end into the Slack thread it came from, as the bot that answers the thread. */
export interface SlackThreadPoster {
  post(reply: {
    orgId: string;
    botId: string;
    channel: string;
    threadTs: string;
    end: ThreadReplyEnd;
    /** The finished conversation, as a stream's last frame carries it; null when it has none to show. */
    conversation: Record<string, unknown> | null;
    /** The conversation in the app (`/chat/?…`). */
    link: string;
  }): Promise<'posted' | 'skipped'>;
}

export type ThreadReplyOutcome =
  | 'posted'
  | 'skipped'
  | 'no_thread'
  | 'not_ended'
  | 'watched'
  | 'claimed_elsewhere'
  | 'failed';

interface Logger {
  info(message: string, meta?: Record<string, unknown>): void;
  warn(message: string, meta?: Record<string, unknown>): void;
}

const ENDS: Partial<Record<HarnessRun['status'], ThreadReplyEnd>> = {
  completed: 'answered',
  failed: 'failed',
  cancelled: 'cancelled',
};

/**
 * ADR-OWD-09 D-SLACK-ASK: a Slack turn whose stream ended before its run did (it handed off at an approval card,
 * or the bot went away) gets the run's end in its thread, once. The bot's open stream holds a watch lease as the
 * person's Slack thread (`slackThreadViewer`), so a run that ends while the bot still reads it is the bot's to post;
 * the person's web app watching does not count, since the thread would never hear. Told of every run end; never
 * throws.
 */
export class ThreadReplyService {
  constructor(
    private readonly deps: {
      leases: Pick<RunWatchLeases, 'watching'>;
      claims: RunEndNoticeClaims;
      conversations: ConversationReader;
      poster: SlackThreadPoster;
      now?: () => number;
      logger?: Logger;
    },
  ) {}

  async runEnded(run: HarnessRun): Promise<ThreadReplyOutcome> {
    const thread = run.input?.reply?.slack;
    if (thread === undefined || run.parent !== undefined) return 'no_thread';
    const end = ENDS[run.status];
    if (end === undefined) return 'not_ended';
    try {
      return await forEnumeratedOrg(run.orgId, REPLY_JOB, () =>
        this.reply(run, thread, end),
      );
    } catch (error) {
      this.deps.logger?.warn(
        'thread_reply.failed',
        harnessLogMeta('thread_reply.failed', {
          orgId: run.orgId,
          runId: run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return 'failed';
    }
  }

  private async reply(
    run: HarnessRun,
    thread: { botId: string; channel: string; threadTs: string },
    end: ThreadReplyEnd,
  ): Promise<ThreadReplyOutcome> {
    const now = this.deps.now?.() ?? Date.now();
    const viewer = slackThreadViewer(run.actorId);
    if (
      await this.deps.leases.watching(run.orgId, viewer, run.sessionId, now)
    ) {
      return 'watched';
    }
    if (
      !(await this.deps.claims.claimRunNotice(
        run.orgId,
        run.runId,
        viewer,
        now,
      ))
    ) {
      return 'claimed_elsewhere';
    }
    const conversation =
      end === 'answered'
        ? await this.deps.conversations.finishedConversation(run)
        : null;
    const outcome = await this.deps.poster.post({
      orgId: run.orgId,
      ...thread,
      end,
      conversation,
      link: conversationLink(run.sessionId, run.agentKey),
    });
    this.deps.logger?.info(
      'thread_reply.sent',
      harnessLogMeta('thread_reply.sent', {
        orgId: run.orgId,
        runId: run.runId,
        end,
        outcome,
      }),
    );
    return outcome;
  }
}
