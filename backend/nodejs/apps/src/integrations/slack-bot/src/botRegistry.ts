import { AsyncLocalStorage } from "node:async_hooks";
// import axios from "axios";

import { slackJwtGenerator } from '../../../libs/utils/createJwt';
import { ConfigService } from '../../../modules/tokens_manager/services/cm.service';
import axios from 'axios';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import type { SlackCaller, SlackCallerQuery } from './caller-identity';
import { withHome } from './identity/home';

export interface SlackBotConfig {
  botToken: string;
  signingSecret: string;
  /** The workspace the bot token is installed in, from Slack's `auth.test` (ADR-SLACKID-01), never from an event. */
  teamId?: string;
  enterpriseId?: string;
  isEnterpriseInstall?: boolean;
  botId?: string;
  botUserId?: string;
  agentId?: string | null;
  /** The organisation the gateway files the configuration under, when it sends one; an edition may serve that org. */
  orgId?: string;
}

interface SlackBotIdentity {
  teamId?: string;
  botId?: string;
  botUserId?: string;
}

interface SlackRequestContext {
  matchedBot: SlackBotConfig | null;
}

const slackRequestContext = new AsyncLocalStorage<SlackRequestContext>();
let slackBotsCache: SlackBotConfig[] = [];
let slackBotsCacheLoadedAt = 0;
let inFlightRefresh: Promise<SlackBotConfig[]> | null = null;

function registryTtlMs(): number {
  const parsed = Number(process.env.SLACK_BOT_REGISTRY_TTL_MS);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 60_000;
}

const SLACK_BOTS_API_URL = "/api/v1/configurationManager/internal/slack-bot"


function getStringField(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function normalizeBotEntry(entry: unknown): SlackBotConfig | null {
  if (!entry || typeof entry !== "object") {
    return null;
  }

  const record = entry as Record<string, unknown>;
  const botToken =
    getStringField(record.botToken) ||
    getStringField(record.bot_token) ||
    getStringField(record.token);
  const signingSecret =
    getStringField(record.signingSecret) ||
    getStringField(record.signing_secret);

  if (!botToken || !signingSecret) {
    return null;
  }

  const botId = getStringField(record.id) ;
  const agentId =
    getStringField(record.agentId) ||
    null;

  const orgId = getStringField(record.orgId);

  return {
    botToken,
    signingSecret,
    botId,
    agentId,
    ...(orgId ? { orgId } : {}),
  };
}

function extractBotList(payload: unknown): unknown[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }

  const record = payload as Record<string, unknown>;
  if (!record.configs) {
    throw new Error("Failed to get configured slack bots");
  }
  if (Array.isArray(record.configs)) {
    return record.configs;
  }

  return [];
}


const backendUrl = process.env.BACKEND_URL || "http://localhost:3000";


async function fetchAvailableSlackBots(): Promise<SlackBotConfig[]> {
  
  const configService = ConfigService.getInstance();
  const staticToken = slackJwtGenerator("", await configService.getScopedJwtSecret(),[TokenScopes.SLACK_BOT_VERIFY]);

  const headers: Record<string, string> = {};
  headers.Authorization = `Bearer ${staticToken}`;

  const response = await axios.get(`${backendUrl}${SLACK_BOTS_API_URL}`, {
    headers,
    timeout: 5000,
  });
  
  const bots = extractBotList(response.data)
    .map(normalizeBotEntry)
    .filter((bot): bot is SlackBotConfig => Boolean(bot));
  
  return Promise.all(bots.map(withHome));
}

export function resetSlackBotRegistryCache(): void {
  slackBotsCache = [];
  slackBotsCacheLoadedAt = 0;
  inFlightRefresh = null;
}

export async function refreshSlackBotRegistry(
  options?: { force?: boolean },
): Promise<SlackBotConfig[]> {
  // An empty list is a valid result: with no bot configured, caching it is
  // what stops every unsigned request from fetching the registry.
  const isFresh =
    slackBotsCacheLoadedAt > 0 &&
    Date.now() - slackBotsCacheLoadedAt < registryTtlMs();
  if (!options?.force && isFresh) {
    return slackBotsCache;
  }

  if (inFlightRefresh) {
    return inFlightRefresh;
  }

  inFlightRefresh = (async () => {
    try {
      const bots = await fetchAvailableSlackBots();
      slackBotsCache = bots;
      slackBotsCacheLoadedAt = Date.now();
      return slackBotsCache;
    } catch (error) {
      throw error;
    } finally {
      inFlightRefresh = null;
    }
  })();

  return inFlightRefresh;
}

export function getCachedSlackBots(): SlackBotConfig[] {
  return [...slackBotsCache];
}

export function findSlackBotByIdentity(
  bots: SlackBotConfig[],
  identity: SlackBotIdentity,
): SlackBotConfig | null {
  const botId = getStringField(identity.botId);


  for (const bot of bots) {
    if (bot.botId && bot.botId === botId) {
      return bot;
    }
  }
  return null;
}

export function runWithSlackRequestContext<T>(
  matchedBot: SlackBotConfig | null,
  callback: () => T,
): T {
  return slackRequestContext.run({ matchedBot }, callback);
}

export function getCurrentMatchedSlackBot(): SlackBotConfig | null {
  return slackRequestContext.getStore()?.matchedBot || null;
}

const SLACK_CALLER_API_URL =
  '/api/v1/configurationManager/internal/slack-bot/identity';

/** Node's answer to "who is this Slack person": their linked PipesHub account, or a sign-in link to send them. */
export async function askWhoIsSlackCaller(
  query: SlackCallerQuery,
): Promise<SlackCaller> {
  const configService = ConfigService.getInstance();
  const token = slackJwtGenerator(
    '',
    await configService.getScopedJwtSecret(),
    [TokenScopes.SLACK_BOT_VERIFY],
  );
  const response = await axios.post(
    `${backendUrl}${SLACK_CALLER_API_URL}`,
    query,
    {
      headers: { Authorization: `Bearer ${token}` },
      timeout: 5000,
    },
  );
  const data = (response.data ?? {}) as Record<string, unknown>;
  if (
    data.linked === true &&
    typeof data.email === 'string' &&
    data.email !== ''
  ) {
    return {
      linked: true,
      email: data.email,
      fullName: typeof data.fullName === 'string' ? data.fullName : null,
    };
  }
  if (data.linked === false) {
    return {
      linked: false,
      linkUrl: typeof data.linkUrl === 'string' ? data.linkUrl : null,
    };
  }
  throw new Error('Unexpected answer from PipesHub about the Slack caller');
}
