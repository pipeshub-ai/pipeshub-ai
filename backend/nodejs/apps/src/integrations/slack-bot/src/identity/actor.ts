/**
 * Who a Slack message acts as (ADR-SLACKID-01), for one organisation: Slack's record of the sender checked against
 * the bot's own workspace, then PipesHub's answer from that organisation's links. A refusal is told to the sender
 * alone and ends the turn. The organisation is the caller's to establish; nothing of another one reaches the turn.
 */
import { askWhoIsSlackCaller, type SlackBotConfig } from '../botRegistry';
import {
  SLACK_REFUSAL_TEXT,
  slackLinkPromptText,
  slackPersonRefusal,
} from '../caller-identity';
import {
  resolveOwnBotIdentity,
  slackCallerDisplayName,
  type SlackUserRecord,
  type TypedSlackClient,
} from '../helpers';

export interface SlackActorMessage {
  user?: string;
  channel?: string;
  ts?: string;
  thread_ts?: string;
}

export interface SlackActor {
  orgId: string;
  teamId: string;
  slackUserId: string;
  /** The linked person's PipesHub email (thread key and token label). */
  email: string;
  linked: boolean;
  /** Slack's record of the sender, for the display name and time zone. */
  person: SlackUserRecord;
}

export interface ResolveSlackActorInput {
  bot: SlackBotConfig;
  client: TypedSlackClient;
  message: SlackActorMessage;
  /** The organisation the bot serves: its links alone decide who the sender is. */
  orgId: string;
  /** The workspace the event names (Bolt's context team id). */
  eventTeamId?: string;
  botUserId?: string;
}

/** Tells one person, and only them (an ephemeral message); a failure to tell is logged, never retried. */
export async function tellPrivately(
  client: TypedSlackClient,
  message: SlackActorMessage,
  text: string,
): Promise<void> {
  if (!message.channel || !message.user) return;
  try {
    await client.apiCall('chat.postEphemeral', {
      channel: message.channel,
      user: message.user,
      text,
      ...(message.thread_ts ? { thread_ts: message.thread_ts } : {}),
    });
  } catch (error) {
    console.error(
      'Failed to send a private Slack notice:',
      error instanceof Error ? error.message : String(error),
    );
  }
}

/** The actor of the message, or null when the bot does not act (the sender has been told; a bot is ignored). */
export async function resolveSlackActor(
  input: ResolveSlackActorInput,
): Promise<SlackActor | null> {
  const { bot, client, message, orgId, eventTeamId } = input;
  const slackUserId = message.user;
  if (!slackUserId || !message.channel || !bot.botId) return null;
  let person: SlackUserRecord | undefined;
  let botTeamId = bot.teamId;
  try {
    const [lookup, own] = await Promise.all([
      client.users.info({ user: slackUserId }),
      botTeamId
        ? Promise.resolve({ teamId: botTeamId })
        : resolveOwnBotIdentity(client, bot.botId, input.botUserId),
    ]);
    person = lookup.user;
    botTeamId = own.teamId;
  } catch (error) {
    console.error(
      'Slack users.info failed:',
      error instanceof Error ? error.message : String(error),
    );
  }
  const refusal = slackPersonRefusal(person, eventTeamId, botTeamId);
  if (refusal || !person || !eventTeamId) {
    if (refusal !== 'bot')
      await tellPrivately(
        client,
        message,
        SLACK_REFUSAL_TEXT[refusal ?? 'unverified'],
      );
    return null;
  }

  const caller = await askWhoIsSlackCaller({
    orgId,
    configId: bot.botId,
    teamId: eventTeamId,
    slackUserId,
    slackName: slackCallerDisplayName(person) || undefined,
  });
  if (caller.linked) {
    return {
      orgId,
      teamId: eventTeamId,
      slackUserId,
      email: caller.email,
      linked: true,
      person,
    };
  }
  if (caller.linkUrl) {
    await tellPrivately(client, message, slackLinkPromptText(caller.linkUrl));
  }
  // Every bot, an agent's included, answers as the person asking: without a link there is no one to answer as.
  return null;
}
