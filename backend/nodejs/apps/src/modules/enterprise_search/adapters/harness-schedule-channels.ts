import { mailJwtGenerator } from '../../../libs/utils/createJwt';
import { EmailTemplateType } from '../../mail/middlewares/types';
import type { AgentSlackBots } from '../../agent-harness/ports/schedules';
import type {
  ScheduleMailSender,
  SlackDirectMessages,
} from '../../../libs/repositories/agent-harness/schedule-notifier/mongo';

/** What the app's mail services offer; a run-ended notice needs only the send. */
export interface MailSend {
  sendMail(params: {
    emailTemplateType: string;
    initiator: { orgId?: string; jwtAuthToken: string };
    usersMails: string[];
    subject: string;
    templateData?: Record<string, unknown>;
  }): Promise<{ statusCode: number }>;
}

/** Notices through the app's mail path: a run's end (TR-07), and a template a bound feature names; one mail each. */
export class MailScheduleNotices implements ScheduleMailSender {
  constructor(
    private readonly mail: MailSend,
    private readonly scopedJwtSecret: () => string,
  ) {}

  async sendRunEnded(
    mail: Parameters<ScheduleMailSender['sendRunEnded']>[0],
  ): Promise<void> {
    const answer = await this.mail.sendMail({
      emailTemplateType: EmailTemplateType.ScheduledRunEnded,
      initiator: {
        orgId: mail.orgId,
        jwtAuthToken: mailJwtGenerator(mail.to.email, this.scopedJwtSecret()),
      },
      usersMails: [mail.to.email],
      subject: mail.subject,
      templateData: {
        name: mail.to.name,
        scheduleName: mail.scheduleName,
        outcome: mail.outcome,
        link: mail.link,
      },
    });
    if (answer.statusCode !== 200) {
      throw new Error(`The mail service answered ${String(answer.statusCode)}`);
    }
  }

  async sendTemplate(
    mail: Parameters<ScheduleMailSender['sendTemplate']>[0],
  ): Promise<void> {
    const answer = await this.mail.sendMail({
      emailTemplateType: mail.template,
      initiator: {
        orgId: mail.orgId,
        jwtAuthToken: mailJwtGenerator(mail.to.email, this.scopedJwtSecret()),
      },
      usersMails: [mail.to.email],
      subject: mail.subject,
      templateData: { name: mail.to.name, ...mail.data },
    });
    if (answer.statusCode !== 200) {
      throw new Error(`The mail service answered ${String(answer.statusCode)}`);
    }
  }
}

/** A Slack bot as the app's Slack integration configures it: one per agent. */
export interface AgentSlackBot {
  botToken: string;
  agentId?: string | null;
}

/** The two Web API calls a direct message needs. */
export interface SlackWebClient {
  users: {
    lookupByEmail(args: {
      email: string;
    }): Promise<{ ok?: boolean; user?: { id?: string } }>;
  };
  chat: {
    postMessage(args: { channel: string; text: string }): Promise<unknown>;
  };
}

const notFound = (error: unknown): boolean =>
  (error as { data?: { error?: unknown } } | null)?.data?.error ===
  'users_not_found';

/**
 * A direct message from the schedule's agent's own Slack bot (TR-07): the bot
 * the agent answers Slack mentions with, found in the app's Slack bot
 * registry. Only that agent's bot: the assistant, or an agent without a bot,
 * sends nothing. The actor is found in that workspace by their email.
 */
export class AgentSlackBotMessages
  implements SlackDirectMessages, AgentSlackBots
{
  constructor(
    private readonly bots: () => Promise<readonly AgentSlackBot[]>,
    private readonly client: (botToken: string) => SlackWebClient,
  ) {}

  async hasBot(agentKey: string): Promise<boolean> {
    return (await this.bots()).some((bot) => bot.agentId === agentKey);
  }

  async send(message: {
    agentKey?: string;
    email: string;
    text: string;
  }): Promise<'sent' | 'skipped'> {
    if (message.agentKey === undefined) return 'skipped';
    const bot = (await this.bots()).find(
      (candidate) => candidate.agentId === message.agentKey,
    );
    if (bot === undefined) return 'skipped';
    const slack = this.client(bot.botToken);
    let userId: string | undefined;
    try {
      userId = (await slack.users.lookupByEmail({ email: message.email })).user
        ?.id;
    } catch (error) {
      if (notFound(error)) return 'skipped';
      throw error;
    }
    if (userId === undefined) return 'skipped';
    await slack.chat.postMessage({ channel: userId, text: message.text });
    return 'sent';
  }
}
