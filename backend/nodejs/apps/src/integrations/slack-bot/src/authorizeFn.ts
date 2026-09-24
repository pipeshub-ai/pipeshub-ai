import {
  type SlackBotConfig,
  findSlackBotByIdentity,
  getCachedSlackBots,
  getCurrentMatchedSlackBot,
  refreshSlackBotRegistry,
} from "./botRegistry";

interface AuthorizeParams {
  teamId?: string;
  enterpriseId?: string;
  userId?: string;
  conversationId?: string;
  isEnterpriseInstall?: boolean;
}

interface AuthorizationResult {
  botToken: string;
  botId?: string;
  botUserId?: string;
}

export interface BotRegistryDeps {
  getCurrentMatchedSlackBot: () => SlackBotConfig | null;
  getCachedSlackBots: () => SlackBotConfig[];
  refreshSlackBotRegistry: (options?: { force?: boolean }) => Promise<SlackBotConfig[]>;
  findSlackBotByIdentity: (
    bots: SlackBotConfig[],
    identity: { teamId?: string; botId?: string; botUserId?: string },
  ) => SlackBotConfig | null;
}

function getStringField(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function getBodyIdentifiers(body: unknown): {
  teamId?: string;
  botId?: string;
  botUserId?: string;
} {
  if (!body || typeof body !== "object") {
    return {};
  }

  const payload = body as Record<string, unknown>;
  const eventPayload =
    payload.event && typeof payload.event === "object"
      ? (payload.event as Record<string, unknown>)
      : null;

  let authorizationsPayload: Record<string, unknown> | null = null;
  if (Array.isArray(payload.authorizations) && payload.authorizations[0]) {
    const firstAuth = payload.authorizations[0];
    if (firstAuth && typeof firstAuth === "object") {
      authorizationsPayload = firstAuth as Record<string, unknown>;
    }
  }

  const teamId =
    getStringField(payload.team_id) ||
    getStringField((payload.team as Record<string, unknown> | undefined)?.id) ||
    getStringField(eventPayload?.team) ||
    getStringField(authorizationsPayload?.team_id);

  const botId =
    getStringField(eventPayload?.bot_id) ||
    getStringField(authorizationsPayload?.bot_id);

  const botUserId = getStringField(authorizationsPayload?.user_id);

  return { teamId, botId, botUserId };
}

export function createAuthorizeFn(deps: BotRegistryDeps) {
  return async (
    params: AuthorizeParams,
    body?: unknown,
  ): Promise<AuthorizationResult> => {
    const matchedFromRequestContext = deps.getCurrentMatchedSlackBot();
    if (matchedFromRequestContext?.botToken) {
      return { botToken: matchedFromRequestContext.botToken };
    }

    let bots = deps.getCachedSlackBots();
    if (bots.length === 0) {
      bots = await deps.refreshSlackBotRegistry({ force: true });
    }

    const bodyIdentifiers = getBodyIdentifiers(body);
    const matchedFromPayload = deps.findSlackBotByIdentity(bots, {
      teamId: params.teamId || bodyIdentifiers.teamId,
      botId: bodyIdentifiers.botId,
      botUserId: bodyIdentifiers.botUserId,
    });

    if (matchedFromPayload?.botToken) {
      return {
        botToken: matchedFromPayload.botToken,
        botId: matchedFromPayload.botId,
        botUserId: matchedFromPayload.botUserId,
      };
    }

    const fallbackBotToken = process.env.BOT_TOKEN;
    if (!fallbackBotToken) {
      throw new Error("Unable to resolve Slack bot token for authorization.");
    }

    return {
      botToken: fallbackBotToken,
      botId: process.env.SLACK_BOT_ID,
      botUserId: process.env.SLACK_BOT_USER_ID,
    };
  };
}

const authorizeFn = createAuthorizeFn({
  getCurrentMatchedSlackBot,
  getCachedSlackBots,
  refreshSlackBotRegistry,
  findSlackBotByIdentity,
});

export default authorizeFn;
