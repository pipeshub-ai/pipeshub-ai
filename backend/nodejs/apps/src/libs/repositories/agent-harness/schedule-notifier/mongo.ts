import mongoose from 'mongoose';
import {
  conversationLink,
  digestLine,
  MAX_NOTICE_ARTIFACTS,
  type NoticeArtifact,
  recordLink,
  runEndDigest,
  runEndLine,
  runEndTitle,
} from '../../../../modules/agent-harness/domain/run-end-notice';
import type { NotifyChannel } from '../../../../modules/agent-harness/domain/schedules';
import { runNoticeWords } from '../../../../modules/agent-harness/domain/inbox';
import type {
  InteractiveRunNotice,
  RunEndNotifier,
} from '../../../../modules/agent-harness/ports/inbox';
import type {
  RunEndedNotice,
  ScheduleNotifier,
} from '../../../../modules/agent-harness/ports/schedules';
import type { PersonNoticeChannels } from '../../../../modules/agent-harness/ports/person-notices';
import { NotificationContainer } from '../../../../modules/notification/container/notification.container';
import { Notifications } from '../../../../modules/notification/schema/notification.schema';
import { Users } from '../../../../modules/user_management/schema/users.schema';
import { ChatSessionMessage } from '../../../../modules/enterprise_search/schema/chat.session.message.schema';
import type { NoticePerson, NoticeRow, ScheduleNoticeRecords } from './records';

export const SCHEDULE_APPROVAL_NOTIFICATION = 'AGENT_SCHEDULE_APPROVAL_WAITING';
export const SCHEDULE_FIRING_REFUSED_NOTIFICATION =
  'AGENT_SCHEDULE_FIRING_REFUSED';
export const SCHEDULE_RUN_ENDED_NOTIFICATION = 'AGENT_SCHEDULE_RUN_ENDED';

/** The chat a run of the schedule lives in: an agent's conversation opens in that agent (UX3, R1 F14). */
export const scheduledRunLink = conversationLink;

export type { NoticePerson };

/** The email path the app already sends with (the mail service's `sendMail`); one template per notice. */
export interface ScheduleMailSender {
  sendRunEnded(mail: {
    orgId: string;
    to: NoticePerson;
    subject: string;
    scheduleName: string;
    outcome: string;
    link: string;
  }): Promise<void>;
  /** A mail through the mail service's template `template` (`PersonNoticeChannels.mail`). */
  sendTemplate(mail: {
    orgId: string;
    to: NoticePerson;
    subject: string;
    template: string;
    data: Readonly<Record<string, unknown>>;
  }): Promise<void>;
}

/** A time in a notice that leaves the app: the reader's zone is unknown, so it is said in UTC. */
export function noticeTime(ms: number): string {
  return new Intl.DateTimeFormat('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: 'UTC',
  })
    .format(new Date(ms))
    .replace(',', '')
    .concat(' UTC');
}

/**
 * A direct message from the schedule's agent's own Slack bot (the bot the
 * agent answers Slack mentions with) to the actor, found by their email.
 * `skipped` when the agent has no bot or the actor no Slack account.
 */
export interface SlackDirectMessages {
  send(message: {
    agentKey?: string;
    email: string;
    text: string;
  }): Promise<'sent' | 'skipped'>;
}

export interface HarnessScheduleNotifierOptions {
  /** The run's saved answer (the newest answer of its conversation); null when there is none. */
  answer?: (orgId: string, sessionId: string) => Promise<string | null>;
  person?: (orgId: string, userId: string) => Promise<NoticePerson | null>;
  mail?: ScheduleMailSender;
  slack?: SlackDirectMessages;
  /** The app's public origin, for links that leave the app (mail, Slack). */
  frontendUrl?: () => string;
  /** Where the answer, the person and the inbox row are read and written; MongoDB's collections when omitted. */
  records?: ScheduleNoticeRecords;
}

/** A channel that could not deliver; the others still did. */
export class NoticeChannelsFailed extends Error {
  constructor(readonly channels: NotifyChannel[]) {
    super(`Notice not delivered on: ${channels.join(', ')}`);
    this.name = 'NoticeChannelsFailed';
  }
}

/** The files the answer links, then the ones the run saved that it did not name (G-07), each once. */
function noticeArtifacts(
  named: NoticeArtifact[],
  digest: RunEndedNotice['digest'],
): NoticeArtifact[] {
  const all = named.map((a) => ({ ...a }));
  const links = new Set(all.map((a) => a.link));
  for (const artifact of digest?.artifacts ?? []) {
    if (artifact.recordId === undefined) continue;
    const link = recordLink(artifact.recordId);
    if (links.has(link)) continue;
    links.add(link);
    all.push({ name: artifact.name, link });
  }
  return all.slice(0, MAX_NOTICE_ARTIFACTS);
}

const ANSWER_TYPES = new Set(['bot_response', 'error']);

/** `ScheduleNoticeRecords` over the Mongo messages, users and notifications collections. */
export const mongoScheduleNoticeRecords: ScheduleNoticeRecords = {
  answer: (orgId, sessionId) => savedAnswer(orgId, sessionId),
  person: (orgId, userId) => personOf(orgId, userId),
  async saveNotice(orgId, actorId, row) {
    const [saved] = await Notifications.create([
      {
        orgId: new mongoose.Types.ObjectId(orgId),
        assignedTo: new mongoose.Types.ObjectId(actorId),
        originService: 'AI Service',
        status: 'unread',
        isDeleted: false,
        ...row,
      },
    ]);
    return saved === undefined ? null : saved.toObject();
  },
};

async function savedAnswer(
  orgId: string,
  sessionId: string,
): Promise<string | null> {
  const found = await ChatSessionMessage.find({ sessionId, orgId })
    .sort({ seq: -1 })
    .limit(20)
    .select('messageType content')
    .lean<Array<{ messageType?: string; content?: string }>>();
  const answer = found.find((m) => ANSWER_TYPES.has(String(m.messageType)));
  return typeof answer?.content === 'string' ? answer.content : null;
}

async function personOf(
  orgId: string,
  userId: string,
): Promise<NoticePerson | null> {
  const user = await Users.findOne({
    _id: new mongoose.Types.ObjectId(userId),
    orgId: new mongoose.Types.ObjectId(orgId),
    isDeleted: false,
  })
    .select('email fullName')
    .lean<{ email?: string; fullName?: string }>();
  if (!user?.email) return null;
  return { email: user.email, name: user.fullName || user.email };
}

/**
 * Tells a schedule's actor what needs them or what happened: in the app's
 * notification inbox (stored like the broker consumer stores notifications,
 * then pushed if the user is connected), and when a run ends (TR-07) also by
 * email and Slack where the schedule and the org allow.
 */
export class HarnessScheduleNotifier
  implements ScheduleNotifier, PersonNoticeChannels, RunEndNotifier
{
  private readonly answer: (
    orgId: string,
    sessionId: string,
  ) => Promise<string | null>;
  private readonly personOf: (
    orgId: string,
    userId: string,
  ) => Promise<NoticePerson | null>;
  private readonly mailer?: ScheduleMailSender;
  private readonly slackBots?: SlackDirectMessages;
  private readonly frontendUrl: () => string;
  private readonly records: ScheduleNoticeRecords;

  constructor(options: HarnessScheduleNotifierOptions = {}) {
    this.records = options.records ?? mongoScheduleNoticeRecords;
    this.answer =
      options.answer ??
      ((orgId, sessionId) => this.records.answer(orgId, sessionId));
    this.personOf =
      options.person ?? ((orgId, userId) => this.records.person(orgId, userId));
    this.mailer = options.mail;
    this.slackBots = options.slack;
    this.frontendUrl = options.frontendUrl ?? (() => '');
  }

  async approvalWaiting(notice: {
    orgId: string;
    actorId: string;
    scheduleId: string;
    scheduleName: string;
    agentKey?: string;
    sessionId: string;
    runId: string;
    deadlineAt: number;
  }): Promise<void> {
    await this.inbox(notice.orgId, notice.actorId, {
      type: SCHEDULE_APPROVAL_NOTIFICATION,
      severity: 'warning',
      title: 'A scheduled run is waiting for your approval',
      message: `"${notice.scheduleName}" paused before a write. Approve or deny it before ${new Date(notice.deadlineAt).toISOString()}, or it stops then.`,
      redirectLink: scheduledRunLink(notice.sessionId, notice.agentKey),
      payload: {
        scheduleId: notice.scheduleId,
        // The Inbox words the row from these (UIP3-FU-1); title and message are the English fallback.
        scheduleName: notice.scheduleName,
        runId: notice.runId,
        conversationId: notice.sessionId,
        deadlineAt: notice.deadlineAt,
      },
    });
  }

  async person(orgId: string, userId: string): Promise<NoticePerson | null> {
    return this.personOf(orgId, userId);
  }

  appUrl(path: string): string {
    return `${this.frontendUrl().replace(/\/$/, '')}${path}`;
  }

  async mail(mail: Parameters<PersonNoticeChannels['mail']>[0]): Promise<void> {
    if (this.mailer === undefined) throw new Error('No mail path');
    await this.mailer.sendTemplate(mail);
  }

  async slack(
    message: Parameters<PersonNoticeChannels['slack']>[0],
  ): Promise<'sent' | 'skipped'> {
    if (this.slackBots === undefined) throw new Error('No Slack path');
    return this.slackBots.send(message);
  }

  async firingRefused(notice: {
    orgId: string;
    actorId: string;
    scheduleId: string;
    scheduleName: string;
    reason: string;
    quiet?: boolean;
  }): Promise<void> {
    await this.inbox(
      notice.orgId,
      notice.actorId,
      {
        type: SCHEDULE_FIRING_REFUSED_NOTIFICATION,
        severity: 'warning',
        title: 'A scheduled run could not start',
        message: `"${notice.scheduleName}" did not run. Open Schedules to see why in its history.`,
        redirectLink: '/workspace/schedules/',
        payload: {
          scheduleId: notice.scheduleId,
          scheduleName: notice.scheduleName,
          reason: notice.reason,
        },
      },
      notice.quiet === true,
    );
  }

  /** Every channel is tried; the ones that failed are named together afterwards. */
  async runEnded(notice: RunEndedNotice): Promise<void> {
    const link = scheduledRunLink(notice.sessionId, notice.agentKey);
    const failed: NotifyChannel[] = [];
    const attempt = async (
      channel: NotifyChannel,
      send: () => Promise<void>,
    ): Promise<void> => {
      if (!notice.channels.includes(channel)) return;
      try {
        await send();
      } catch {
        failed.push(channel);
      }
    };
    await attempt('in_app', async () => {
      const answer = await this.answer(notice.orgId, notice.sessionId);
      const digest = runEndDigest(answer ?? '');
      const facts =
        notice.digest !== undefined ? digestLine(notice.digest) : '';
      const said = [facts, digest.summary].filter((text) => text.length > 0);
      await this.inbox(
        notice.orgId,
        notice.actorId,
        {
          type: SCHEDULE_RUN_ENDED_NOTIFICATION,
          severity: notice.status === 'completed' ? 'success' : 'warning',
          title: runEndTitle(notice.status),
          message:
            said.length > 0
              ? `"${notice.scheduleName}": ${said.join(' ')}`
              : `"${notice.scheduleName}"`,
          redirectLink: link,
          payload: {
            scheduleId: notice.scheduleId,
            scheduleName: notice.scheduleName,
            runId: notice.runId,
            conversationId: notice.sessionId,
            status: notice.status,
            artifacts: noticeArtifacts(digest.artifacts, notice.digest),
            ...(notice.digest !== undefined ? { digest: notice.digest } : {}),
          },
        },
        notice.quiet === true,
      );
    });
    const outside = notice.channels.some((c) => c !== 'in_app')
      ? await this.personOf(notice.orgId, notice.actorId).catch(() => undefined)
      : null;
    const absolute = `${this.frontendUrl().replace(/\/$/, '')}${link}`;
    await attempt('email', async () => {
      if (outside === undefined || this.mailer === undefined) {
        throw new Error('No mail path');
      }
      if (outside === null) return;
      await this.mailer.sendRunEnded({
        orgId: notice.orgId,
        to: outside,
        subject: `${runEndTitle(notice.status)}: ${notice.scheduleName}`,
        scheduleName: notice.scheduleName,
        outcome: runEndTitle(notice.status),
        link: absolute,
      });
    });
    await attempt('slack', async () => {
      if (outside === undefined || this.slackBots === undefined) {
        throw new Error('No Slack path');
      }
      if (outside === null) return;
      await this.slackBots.send({
        ...(notice.agentKey !== undefined ? { agentKey: notice.agentKey } : {}),
        email: outside.email,
        text: runEndLine(notice, absolute),
      });
    });
    if (failed.length > 0) throw new NoticeChannelsFailed(failed);
  }

  /**
   * G2 (ADR-INBOX-01): a person's interactive run ended while they were not watching it. The Inbox always; email
   * and the agent's Slack bot where the notice names them. Every channel is tried; the failed ones are named after.
   */
  async runFinished(notice: InteractiveRunNotice): Promise<void> {
    const words = runNoticeWords(notice);
    const link = conversationLink(notice.sessionId, notice.agentKey);
    const failed: NotifyChannel[] = [];
    const attempt = async (
      channel: NotifyChannel,
      send: () => Promise<void>,
    ): Promise<void> => {
      if (!notice.channels.includes(channel)) return;
      try {
        await send();
      } catch {
        failed.push(channel);
      }
    };
    await attempt('in_app', () =>
      this.inbox(
        notice.orgId,
        notice.userId,
        {
          type: words.type,
          severity: words.severity,
          title: words.title,
          message: words.message,
          redirectLink: link,
          payload: {
            conversationId: notice.sessionId,
            ...(notice.chatTitle.trim().length > 0
              ? { chatTitle: notice.chatTitle.trim() }
              : {}),
            runId: notice.runId,
            ...(notice.agentKey !== undefined
              ? { agentKey: notice.agentKey }
              : {}),
            finished: notice.finished,
            ...(notice.cutShort ? { cutShort: true } : {}),
          },
        },
        notice.quiet,
      ),
    );
    const outside = notice.channels.some((c) => c !== 'in_app')
      ? await this.personOf(notice.orgId, notice.userId).catch(() => undefined)
      : null;
    const absolute = `${this.frontendUrl().replace(/\/$/, '')}${link}`;
    await attempt('email', async () => {
      if (outside === undefined || this.mailer === undefined) {
        throw new Error('No mail path');
      }
      if (outside === null) return;
      await this.mailer.sendRunEnded({
        orgId: notice.orgId,
        to: outside,
        subject: `${words.outcome}: ${words.title}`,
        scheduleName: words.title,
        outcome: words.outcome,
        link: absolute,
      });
    });
    await attempt('slack', async () => {
      if (outside === undefined || this.slackBots === undefined) {
        throw new Error('No Slack path');
      }
      if (outside === null) return;
      await this.slackBots.send({
        ...(notice.agentKey !== undefined ? { agentKey: notice.agentKey } : {}),
        email: outside.email,
        text: `"${words.title}": ${words.message} Open it: ${absolute}`,
      });
    });
    if (failed.length > 0) throw new NoticeChannelsFailed(failed);
  }

  private async inbox(
    orgId: string,
    actorId: string,
    row: NoticeRow,
    quiet = false,
  ): Promise<void> {
    const saved = await this.records.saveNotice(orgId, actorId, row);
    if (saved === null || quiet) return;
    NotificationContainer.getNotificationService()?.sendToUser(
      actorId,
      'newNotification',
      saved,
    );
  }
}
