/**
 * The workspace a bot is installed in, from Slack's own answer to `auth.test` with the bot's token (ADR-SLACKID-01).
 * Nothing in an event can change it: the event says who sent it, this says whose bot it is.
 */
import crypto from 'node:crypto';
import { WebClient } from '@slack/web-api';
import { slackClientOptions } from '../utils/web-api';
import type { SlackBotConfig } from '../botRegistry';

export interface SlackHome {
  teamId: string;
  enterpriseId?: string;
  /** An org-wide (Enterprise Grid) install: every workspace of the enterprise is home. */
  isEnterpriseInstall: boolean;
  botUserId?: string;
}

export type AuthTest = (botToken: string) => Promise<Record<string, unknown>>;

const HOME_TTL_MS = 60 * 60 * 1000;
const homes = new Map<string, { at: number; home: Promise<SlackHome> }>();

// One quick retry: the receiver answers 503 meanwhile, and Slack's own redelivery is the longer retry.
const defaultAuthTest: AuthTest = async (botToken) =>
  (await new WebClient(botToken, {
    ...slackClientOptions(),
    retryConfig: { retries: 1 },
    timeout: 5000,
  }).auth.test()) as unknown as Record<string, unknown>;

let authTest: AuthTest = defaultAuthTest;

const str = (v: unknown): string | undefined =>
  typeof v === 'string' && v.trim() !== '' ? v.trim() : undefined;

export function homeFromAuthTest(answer: Record<string, unknown>): SlackHome {
  const teamId = str(answer.team_id);
  const enterpriseId = str(answer.enterprise_id);
  const isEnterpriseInstall = answer.is_enterprise_install === true;
  if (!teamId && !(isEnterpriseInstall && enterpriseId)) {
    throw new Error('auth.test named no workspace for this bot token');
  }
  return {
    teamId: teamId ?? '',
    ...(enterpriseId ? { enterpriseId } : {}),
    isEnterpriseInstall,
    ...(str(answer.user_id) ? { botUserId: str(answer.user_id) } : {}),
  };
}

/** The Slack identity space a link is keyed by: user ids are unique within an enterprise, else within a workspace. */
export function identitySpace(home: SlackHome): string {
  return home.enterpriseId ?? home.teamId;
}

export function resolveSlackHome(botToken: string): Promise<SlackHome> {
  const key = crypto.createHash('sha256').update(botToken).digest('hex');
  const cached = homes.get(key);
  if (cached && Date.now() - cached.at < HOME_TTL_MS) return cached.home;
  const home = authTest(botToken).then(homeFromAuthTest);
  homes.set(key, { at: Date.now(), home });
  home.catch(() => {
    if (homes.get(key)?.home === home) homes.delete(key);
  });
  return home;
}

export function setSlackAuthTestForTests(fn?: AuthTest): void {
  authTest = fn ?? defaultAuthTest;
  homes.clear();
}

/** A bot with its home recorded; one whose workspace Slack would not name yet stays without, and is refused until known. */
export async function withHome(bot: SlackBotConfig): Promise<SlackBotConfig> {
  try {
    const home = await resolveSlackHome(bot.botToken);
    return {
      ...bot,
      teamId: home.teamId || undefined,
      enterpriseId: home.enterpriseId,
      isEnterpriseInstall: home.isEnterpriseInstall,
      botUserId: home.botUserId,
    };
  } catch (error) {
    console.error(
      'Slack auth.test failed for a configured bot:',
      error instanceof Error ? error.message : String(error),
    );
    return bot;
  }
}
