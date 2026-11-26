/**
 * Who may talk to the bot, and as whom (ADR-SLACKID-01). Only a member of the bot's own Slack workspace, in a
 * channel not shared with another organisation, acts as anyone; and then only as the PipesHub account they linked
 * by signing in, matched on Slack team id and user id. A Slack profile email never picks the account.
 */

export type SlackRefusal =
  | 'external'
  | 'shared_channel'
  | 'guest'
  | 'deactivated'
  | 'bot'
  | 'unverified';

/** What the person is told, in Slack, privately. A bot is never answered, so it cannot start a loop. */
export const SLACK_REFUSAL_TEXT: Record<
  Exclude<SlackRefusal, 'bot'>,
  string
> = {
  external:
    'I only answer people in this Slack workspace. Messages from other organisations, ' +
    'including people in shared Slack Connect channels, are not run.',
  shared_channel:
    "I don't answer in channels shared with other organisations, so nothing private is posted where people " +
    'outside your organisation can read it. Message me directly, or mention me in an internal channel.',
  guest:
    "Guest accounts can't use PipesHub from Slack. If you need access, ask an admin of this Slack workspace " +
    'to make you a full member.',
  deactivated: 'This Slack account is deactivated, so I cannot answer it.',
  unverified:
    "I couldn't verify your Slack account right now. Please try again in a moment.",
};

export function slackLinkPromptText(linkUrl: string): string {
  return [
    `To use PipesHub from Slack, link your Slack account to your PipesHub account once: <${linkUrl}|Link my account>.`,
    'Sign in to PipesHub and approve the link. The link is only for you and works for 10 minutes.',
    'From then on I answer as your PipesHub account, with your access. ' +
      'PipesHub no longer goes by the email in your Slack profile, so nobody can act as you by changing theirs.',
  ].join('\n');
}

type Json = Record<string, unknown>;

function record(value: unknown): Json | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Json)
    : undefined;
}

/**
 * A signed delivery refused before any handler runs or anything is looked up. Slack sets `team_id` (the workspace
 * the app is installed in) on the envelope; an event whose person (`user_team`), source (`source_team`) or `team`
 * names any other workspace came through Slack Connect. The comparison is exact: a case or Unicode variant of the
 * home id is another workspace.
 */
export function slackEventRefusal(
  payload: Json | undefined,
): SlackRefusal | null {
  if (!payload || payload.type !== 'event_callback') return null;
  const event = record(payload.event);
  if (!event) return null;
  if (
    event.bot_id !== undefined ||
    event.bot_profile !== undefined ||
    event.subtype === 'bot_message'
  )
    return 'bot';
  const home = payload.team_id;
  if (typeof home !== 'string' || home === '') return 'external';
  for (const field of ['user_team', 'source_team', 'team'] as const) {
    if (event[field] !== undefined && event[field] !== home) return 'external';
  }
  if (payload.is_ext_shared_channel === true) return 'shared_channel';
  return null;
}

export interface SlackPerson {
  id?: string;
  team_id?: string;
  deleted?: boolean;
  is_bot?: boolean;
  is_app_user?: boolean;
  is_restricted?: boolean;
  is_ultra_restricted?: boolean;
  is_stranger?: boolean;
}

/** What Slack itself says about the person (`users.info`), checked against the event's workspace and the bot's own
 * (`auth.test`). Either team id unknown refuses: there is nothing to compare. */
export function slackPersonRefusal(
  person: SlackPerson | undefined,
  eventTeamId: string | undefined,
  botTeamId: string | undefined,
): SlackRefusal | null {
  if (!person || !eventTeamId || !botTeamId) return 'unverified';
  if (person.is_bot === true || person.is_app_user === true) return 'bot';
  if (person.deleted === true) return 'deactivated';
  if (person.is_stranger === true) return 'external';
  if (eventTeamId !== botTeamId || person.team_id !== botTeamId)
    return 'external';
  if (person.is_restricted === true || person.is_ultra_restricted === true)
    return 'guest';
  return null;
}

export type SlackCaller =
  | { linked: true; email: string; fullName: string | null }
  | { linked: false; linkUrl: string | null };

export interface SlackCallerQuery {
  /** The organisation the bot serves; PipesHub answers only from that organisation's links. */
  orgId: string;
  configId: string;
  teamId: string;
  slackUserId: string;
  slackName?: string;
  issueLink?: boolean;
}
