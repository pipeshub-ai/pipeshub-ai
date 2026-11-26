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
  /** The linked person's PipesHub email, or the account's reserved stand-in address (thread key and token label). */
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

/** A link is sent again at most this often to a person an agent bot keeps answering through its service account. */
const LINK_PROMPT_REPEAT_MS = 10 * 60 * 1000;
const lastLinkPromptAt = new Map<string, number>();

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

  const promptKey = `${orgId}:${bot.botId}:${eventTeamId}:${slackUserId}`;
  const promptedAt = lastLinkPromptAt.get(promptKey);
  const promptedRecently =
    promptedAt !== undefined && Date.now() - promptedAt < LINK_PROMPT_REPEAT_MS;
  const caller = await askWhoIsSlackCaller({
    orgId,
    configId: bot.botId,
    teamId: eventTeamId,
    slackUserId,
    slackName: slackCallerDisplayName(person) || undefined,
    // An agent bot keeps answering through its service account, so its last link stays the one that works.
    issueLink: !(bot.agentId && promptedRecently),
  });
  if (caller.linked) {
    lastLinkPromptAt.delete(promptKey);
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
    lastLinkPromptAt.set(promptKey, Date.now());
    await tellPrivately(client, message, slackLinkPromptText(caller.linkUrl));
  }
  if (!bot.agentId) return null;
  return {
    orgId,
    teamId: eventTeamId,
    slackUserId,
    email: slackStandInEmail(eventTeamId, slackUserId),
    linked: false,
    person,
  };
}

/** The address an unlinked Slack account is known by: reserved (`.invalid`), so it can never be a person's. */
export function slackStandInEmail(teamId: string, slackUserId: string): string {
  return `slack-${teamId}-${slackUserId}@slack.invalid`.toLowerCase();
}
