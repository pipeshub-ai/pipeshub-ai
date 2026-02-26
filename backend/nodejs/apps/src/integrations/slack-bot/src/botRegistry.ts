import { AsyncLocalStorage } from "node:async_hooks";
// import axios from "axios";

import { slackJwtGenerator } from "../../../libs/utils/createJwt";
import { ConfigService } from "../../../modules/tokens_manager/services/cm.service";
import axios from "axios";
import { TokenScopes } from "../../../libs/enums/token-scopes.enum";


export interface SlackBotConfig {
  botToken: string;
  signingSecret: string;
  teamId?: string;
  botId?: string;
  botUserId?: string;
  agentId?: string | null;
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
let inFlightRefresh: Promise<SlackBotConfig[]> | null = null;

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

  return {
    botToken,
    signingSecret,
    botId,
    agentId,
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
  const staticToken = slackJwtGenerator("", await configService.getScopedJwtSecret(),[TokenScopes.FETCH_CONFIG]);

  const headers: Record<string, string> = {};
  headers.Authorization = `Bearer ${staticToken}`;

  const response = await axios.get(`${backendUrl}${SLACK_BOTS_API_URL}`, {
    headers,
    timeout: 5000,
  });
  
  const bots = extractBotList(response.data)
    .map(normalizeBotEntry)
    .filter((bot): bot is SlackBotConfig => Boolean(bot));

  return bots;
}

export async function refreshSlackBotRegistry(
  options?: { force?: boolean },
): Promise<SlackBotConfig[]> {
  if (!options?.force && slackBotsCache.length > 0) {
    return slackBotsCache;
  }

  if (inFlightRefresh) {
    return inFlightRefresh;
  }

  inFlightRefresh = (async () => {
    try {
      const bots = await fetchAvailableSlackBots();
      if (bots.length > 0) {
        slackBotsCache = bots;
        return slackBotsCache;
      }


      throw new Error("No Slack bots are configured.");
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
