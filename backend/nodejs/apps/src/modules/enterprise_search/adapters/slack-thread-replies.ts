import type { SlackThreadPoster } from '../../agent-harness/application/thread-replies';
import {
  finishedAnswer,
  newestBotResponse,
} from '../../../integrations/slack-bot/src/utils/finished-answer';

/** A Slack bot as the app's Slack integration configures it, by its configuration id. */
export interface ThreadSlackBot {
  botId?: string;
  botToken: string;
}

/** The one Web API call a thread reply needs. */
export interface SlackThreadClient {
  chat: {
    postMessage(args: {
      channel: string;
      thread_ts: string;
      text: string;
      blocks?: unknown[];
      unfurl_links?: boolean;
      unfurl_media?: boolean;
    }): Promise<unknown>;
  };
}

const NO_UNFURL = { unfurl_links: false, unfurl_media: false } as const;

/**
 * ADR-OWD-09 D-SLACK-ASK: a run's end posted into the Slack thread it came from by the bot that answers that thread,
 * composed as the bot composes an answer at the end of its own stream (`finishedAnswer`), so the thread reads the
 * same either way. A failed or stopped run says so and links the conversation. A bot removed since: nothing.
 */
export class SlackBotThreadReplies implements SlackThreadPoster {
  constructor(
    private readonly bots: () => Promise<readonly ThreadSlackBot[]>,
    private readonly client: (botToken: string) => SlackThreadClient,
    private readonly frontendUrl: () => string,
  ) {}

  async post(
    reply: Parameters<SlackThreadPoster['post']>[0],
  ): Promise<'posted' | 'skipped'> {
    const bot = (await this.bots()).find((b) => b.botId === reply.botId);
    if (bot === undefined) return 'skipped';
    const slack = this.client(bot.botToken);
    const thread = {
      channel: reply.channel,
      thread_ts: reply.threadTs,
      ...NO_UNFURL,
    };
    const answer =
      reply.end === 'answered' && reply.conversation !== null
        ? newestBotResponse(reply.conversation['messages'])
        : null;
    if (answer === null || answer.content.trim() === '') {
      await slack.chat.postMessage({ ...thread, text: this.endWords(reply) });
      return 'posted';
    }
    const finished = await finishedAnswer(answer);
    for (const blocks of [...finished.answer, ...finished.followUps]) {
      await slack.chat.postMessage({ ...thread, text: '', blocks });
    }
    for (const artifact of finished.artifacts) {
      await slack.chat.postMessage({ ...thread, text: artifact.text });
    }
    return 'posted';
  }

  private endWords(reply: Parameters<SlackThreadPoster['post']>[0]): string {
    const open = `<${this.frontendUrl().replace(/\/$/, '')}${reply.link}|Open the conversation in PipesHub>`;
    return reply.end === 'cancelled'
      ? `This was stopped in PipesHub. ${open} to see what was done.`
      : `I couldn't finish this. ${open} to see what happened.`;
  }
}
