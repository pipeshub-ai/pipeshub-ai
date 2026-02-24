import { AsyncLocalStorage } from "node:async_hooks";
// import axios from "axios";

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

// const SLACK_BOTS_API_URL =
//   process.env.SLACK_BOTS_API_URL ||
//   process.env.SLACK_BOTS_REGISTRY_URL ||
//   "";

function getStringField(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

// function normalizeBotEntry(entry: unknown): SlackBotConfig | null {
//   if (!entry || typeof entry !== "object") {
//     return null;
//   }

//   const record = entry as Record<string, unknown>;
//   const botToken =
//     getStringField(record.botToken) ||
//     getStringField(record.bot_token) ||
//     getStringField(record.token);
//   const signingSecret =
//     getStringField(record.signingSecret) ||
//     getStringField(record.signing_secret);

//   if (!botToken || !signingSecret) {
//     return null;
//   }

//   const botId = getStringField(record.botId) || getStringField(record.bot_id);
//   const botUserId =
//     getStringField(record.botUserId) || getStringField(record.bot_user_id);
//   const teamId = getStringField(record.teamId) || getStringField(record.team_id);
//   const agentId =
//     getStringField(record.agentId) ||
//     getStringField(record.agent_id) ||
//     getStringField(record.agentKey) ||
//     getStringField(record.agent_key) ||
//     null;

//   return {
//     botToken,
//     signingSecret,
//     botId,
//     botUserId,
//     teamId,
//     agentId,
//   };
// }

// function extractBotList(payload: unknown): unknown[] {
//   if (Array.isArray(payload)) {
//     return payload;
//   }
//   if (!payload || typeof payload !== "object") {
//     return [];
//   }

//   const record = payload as Record<string, unknown>;
//   if (Array.isArray(record.bots)) {
//     return record.bots;
//   }
//   if (Array.isArray(record.items)) {
//     return record.items;
//   }
//   if (Array.isArray(record.data)) {
//     return record.data;
//   }

//   if (
//     record.data &&
//     typeof record.data === "object" &&
//     Array.isArray((record.data as Record<string, unknown>).bots)
//   ) {
//     return (record.data as Record<string, unknown>).bots as unknown[];
//   }

//   return [];
// }

function getFallbackSlackBotsFromEnv(): SlackBotConfig[] {
  const botToken = getStringField(process.env.BOT_TOKEN);
  const signingSecret = getStringField(process.env.SLACK_SIGNING_SECRET);
  if (!botToken || !signingSecret) {
    return [];
  }

  return [
    {
      botToken,
      signingSecret,
      teamId: getStringField(process.env.SLACK_TEAM_ID),
      botId: getStringField(process.env.SLACK_BOT_ID),
      botUserId: getStringField(process.env.SLACK_BOT_USER_ID),
      agentId: getStringField(process.env.SLACK_DEFAULT_AGENT_ID) || null,
    },
  ];
}

async function fetchAvailableSlackBots(): Promise<SlackBotConfig[]> {

  return [
    {
      botToken: process.env.AGENT1_BOT_TOKEN || "",
      signingSecret: process.env.AGENT1_SLACK_SIGNING_SECRET || "",
      agentId: "2213a0ea-31dd-4db9-93d3-6a46ed3fcd9a", //linear agent
    },
    {
      botToken: process.env.AGENT2_BOT_TOKEN || "",
      signingSecret: process.env.AGENT2_SLACK_SIGNING_SECRET || "",
      agentId: "8537137f-5dd6-4d8c-b87b-0250fe15b504",
    },
    {
      botToken: process.env.BOT_TOKEN || "",
      signingSecret: process.env.SLACK_SIGNING_SECRET || "",
      agentId: process.env.SLACK_DEFAULT_AGENT_ID || "",
    }
  ];
  // if (!SLACK_BOTS_API_URL) {
  //   return getFallbackSlackBotsFromEnv();
  // }

  // const staticToken = getStringField(process.env.SLACK_BOTS_API_TOKEN);
  // const headers: Record<string, string> = {};
  // if (staticToken) {
  //   headers.Authorization = `Bearer ${staticToken}`;
  // }

  // const response = await axios.get(SLACK_BOTS_API_URL, {
  //   headers,
  //   timeout: 5000,
  // });
  // const bots = extractBotList(response.data)
  //   .map(normalizeBotEntry)
  //   .filter((bot): bot is SlackBotConfig => Boolean(bot));

  // return bots;
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

      if (slackBotsCache.length > 0) {
        return slackBotsCache;
      }

      const fallbackBots = getFallbackSlackBotsFromEnv();
      if (fallbackBots.length > 0) {
        slackBotsCache = fallbackBots;
        return slackBotsCache;
      }

      throw new Error("No Slack bots are configured.");
    } catch (error) {
      if (slackBotsCache.length > 0) {
        return slackBotsCache;
      }
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
