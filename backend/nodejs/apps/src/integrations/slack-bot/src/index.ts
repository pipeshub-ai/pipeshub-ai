import { config } from "dotenv";
config(); // Load environment variables first

import { json, urlencoded, Request, Response } from "express";
import { connect } from "./utils/db";
import { getFromDatabase, saveToDatabase } from "./utils/conversation";

import axios from "axios";
import app from "./slackApp";
import receiver from "./receiver";
import { ConfigService } from "../../../modules/tokens_manager/services/cm.service";
import { slackJwtGenerator } from "../../../libs/utils/createJwt";
import { markdownToSlackMrkdwn } from "./utils/md_to_mrkdwn";
import {
  type SlackBotConfig,
  // findSlackBotByIdentity,
  // getCachedSlackBots,
  getCurrentMatchedSlackBot,
  refreshSlackBotRegistry,
} from "./botRegistry";


interface CitationData {
  citationId: string;
  citationData: {
    content: string;
    metadata: {
      recordId: string;
      recordName: string;
      recordType: string;
      createdAt: string;
      departments: string[];
      categories: string[];
      webUrl?: string;
    };
    chunkIndex?: string;
  }
}

interface BotResponse {
  content: string;
  citations?: CitationData[];
  messageType: string;
}

interface ConversationData {
  conversation: {
    _id: string;
    messages: BotResponse[];
  };
  [key: string]: unknown;
}

interface StreamEvent {
  event: string;
  data: unknown;
}

const STREAM_UPDATE_THROTTLE_MS = 900;
const SLACK_MAX_TEXT_LENGTH = 39000;
const SLACK_STREAM_MARKDOWN_LIMIT = 12000;
const DEFAULT_SLACK_ERROR_MESSAGE = "Something went wrong! Please try again later.";
const MAX_USER_VISIBLE_ERROR_LENGTH = 320;
const SLACK_MENTION_REGEX = /<@[A-Z0-9]+>/g;
const STREAM_FAILURE_MESSAGE =
  "I ran into an issue while streaming the response. Please try again.";

const SLACK_FRIENDLY_ERROR_MAPPINGS: Array<{
  pattern: RegExp;
  message: string;
}> = [
  {
    pattern: /no results found/i,
    message: "I couldn't find relevant information for that query.",
  },
  {
    pattern: /failed to (initialize|start).*(llm|model)/i,
    message: "I couldn't initialize the AI service right now. Please try again shortly.",
  },
  {
    pattern: /(stream error|error in llm streaming|failed to start slack stream)/i,
    message: "I hit an issue while generating the response. Please try again.",
  },
  {
    pattern: /(econnrefused|etimedout|enotfound|service unavailable|network error)/i,
    message: "I couldn't reach the backend service right now. Please try again in a moment.",
  },
  {
    pattern: /back(end)?_url environment variable is not set/i,
    message: "This service is temporarily unavailable. Please try again later.",
  },
];

const SLACK_TECHNICAL_ERROR_PATTERNS: RegExp[] = [
  /traceback/i,
  /\bat\s+[^\n]+:\d+:\d+/i,
  /file\s+"[^"]+",\s+line\s+\d+/i,
  /(internalservererror|httpexception|typeerror|referenceerror|valueerror|syntaxerror)/i,
  /(mongodb|redis|neo4j|postgres|sql|kafka)/i,
  /(stack|stack trace)/i,
  /(status code\s*\d{3}|\b5\d{2}\b)/i,
  /(api\/v1\/|https?:\/\/[^\s]+)/i,
  /(bearer\s+[a-z0-9\.\-_]+)/i,
  /(back(end)?_url|slack_bot|jwt|token|secret)/i,
];

interface StreamStartResult {
  ts?: string;
}

interface SlackMessagePayload {
  subtype?: string;
  bot_id?: string;
  user?: string;
  files?: unknown[];
  text?: string;
  thread_ts?: string;
  ts: string;
  channel?: string;
}

interface SlackConversationsRepliesResponse {
  messages?: SlackMessagePayload[];
  response_metadata?: {
    next_cursor?: string;
  };
}

interface SlackUserProfile {
  email?: string;
  display_name?: string;
  real_name?: string;
}

interface SlackUserRecord {
  id?: string;
  name?: string;
  real_name?: string;
  profile?: SlackUserProfile;
}

interface TypedSlackClient {
  botUserId?: string;
  users: {
    info: (params: { user: string }) => Promise<{
      user?: SlackUserRecord;
    }>;
  };
  chat: {
    postMessage: (params: {
      channel: string;
      thread_ts?: string;
      text: string;
    }) => Promise<{ ts?: string }>;
    update: (params: {
      channel: string;
      ts: string;
      text: string;
    }) => Promise<{ ts?: string }>;
  };
  apiCall: (
    apiMethod: string,
    options?: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
}

interface TypedSlackContext {
  botUserId?: string;
  teamId?: string;
  matchedBotId?: string;
  matchedBotUserId?: string;
  matchedBotTeamId?: string;
  matchedBotAgentId?: string | null;
}



function parseSSEEvents(buffer: string): { events: StreamEvent[]; remainder: string } {
  const rawEvents = buffer.split("\n\n");
  const remainder = rawEvents.pop() || "";
  const events: StreamEvent[] = [];

  for (const rawEvent of rawEvents) {
    if (!rawEvent.trim()) {
      continue;
    }

    let eventType = "message";
    const dataLines: string[] = [];

    for (const line of rawEvent.split("\n")) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    const dataPayload = dataLines.join("\n");
    let parsedData: unknown = dataPayload;

    if (dataPayload) {
      try {
        parsedData = JSON.parse(dataPayload);
      } catch {
        parsedData = dataPayload;
      }
    }

    events.push({ event: eventType, data: parsedData });
  }

  return { events, remainder };
}

function extractStreamChunk(data: unknown): string {
  if (typeof data === "string") {
    return data;
  }
  if (data && typeof data === "object") {
    if ("chunk" in data && typeof data.chunk === "string") {
      return data.chunk;
    }
    if ("content" in data && typeof data.content === "string") {
      return data.content;
    }
  }
  return "";
}

function readMessageFromObject(value: unknown): string | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const record = value as Record<string, unknown>;
  const directKeys = ["message", "error", "detail", "reason"] as const;

  for (const key of directKeys) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  if (record.error && typeof record.error === "object") {
    const nestedErrorMessage = readMessageFromObject(record.error);
    if (nestedErrorMessage) {
      return nestedErrorMessage;
    }
  }

  return null;
}

function extractSlackErrorMessage(error: unknown): string {
  if (!error) {
    return DEFAULT_SLACK_ERROR_MESSAGE;
  }

  if (typeof error === "string" && error.trim()) {
    return error.trim();
  }

  if (axios.isAxiosError(error)) {
    const responseMessage = readMessageFromObject(error.response?.data);
    if (responseMessage) {
      return responseMessage;
    }
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }

  const objectMessage = readMessageFromObject(error);
  if (objectMessage) {
    return objectMessage;
  }

  return DEFAULT_SLACK_ERROR_MESSAGE;
}

function normalizeSlackErrorMessage(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function mapFriendlySlackErrorMessage(message: string): string | null {
  for (const mapping of SLACK_FRIENDLY_ERROR_MAPPINGS) {
    if (mapping.pattern.test(message)) {
      return mapping.message;
    }
  }
  return null;
}

function isTechnicalSlackErrorMessage(message: string): boolean {
  return SLACK_TECHNICAL_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

function resolveSlackErrorMessage(error: unknown): string {
  const rawMessage = extractSlackErrorMessage(error);
  const normalizedMessage = normalizeSlackErrorMessage(rawMessage);

  if (!normalizedMessage) {
    return DEFAULT_SLACK_ERROR_MESSAGE;
  }

  const mappedMessage = mapFriendlySlackErrorMessage(normalizedMessage);
  if (mappedMessage) {
    return mappedMessage;
  }

  if (isTechnicalSlackErrorMessage(normalizedMessage)) {
    return DEFAULT_SLACK_ERROR_MESSAGE;
  }

  return truncateFromEnd(normalizedMessage, MAX_USER_VISIBLE_ERROR_LENGTH);
}

function truncateForSlack(text: string): string {
  if (text.length <= SLACK_MAX_TEXT_LENGTH) {
    return text;
  }
  return `...${text.slice(-(SLACK_MAX_TEXT_LENGTH - 3))}`;
}

function resolveThreadId(typedMessage: SlackMessagePayload): string {
  return typedMessage.thread_ts || typedMessage.ts;
}

async function sendUserFacingSlackErrorMessage(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
  errorOrMessage: unknown,
): Promise<void> {
  if (!typedMessage.channel) {
    return;
  }

  const errorMessage = resolveSlackErrorMessage(errorOrMessage);
  const threadId = resolveThreadId(typedMessage);

  try {
    await typedClient.chat.postMessage({
      channel: typedMessage.channel,
      thread_ts: threadId,
      text: truncateForSlack(errorMessage),
    });
  } catch (sendError) {
    console.error("Failed to send Slack user-facing error message:", sendError);
  }
}

function truncateForSlackStreamMarkdown(text: string): string {
  if (text.length <= SLACK_STREAM_MARKDOWN_LIMIT) {
    return text;
  }
  return `...${text.slice(-(SLACK_STREAM_MARKDOWN_LIMIT - 3))}`;
}

function truncateFromEnd(text: string, limit: number): string {
  if (limit <= 0) {
    return "";
  }
  if (text.length <= limit) {
    return text;
  }
  if (limit <= 3) {
    return text.slice(0, limit);
  }
  return `${text.slice(0, limit - 3)}...`;
}

function buildFinalStreamOverwriteMessage(
  answerBody: string,
  sourcesLine: string,
): string {
  if (!sourcesLine) {
    return truncateFromEnd(answerBody, SLACK_STREAM_MARKDOWN_LIMIT);
  }

  if (sourcesLine.length >= SLACK_STREAM_MARKDOWN_LIMIT) {
    return truncateFromEnd(sourcesLine, SLACK_STREAM_MARKDOWN_LIMIT);
  }

  const bodyLimit = SLACK_STREAM_MARKDOWN_LIMIT - sourcesLine.length;
  return `${truncateFromEnd(answerBody, bodyLimit)}${sourcesLine}`;
}

function splitByLength(text: string, limit: number): string[] {
  if (!text) {
    return [];
  }
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += limit) {
    chunks.push(text.slice(i, i + limit));
  }
  return chunks;
}

function parseSlackTimestamp(ts?: string): number | null {
  if (!ts) {
    return null;
  }

  const parsed = Number(ts);
  return Number.isFinite(parsed) ? parsed : null;
}

function sanitizeSlackThreadMessageText(text?: string): string {
  if (!text) {
    return "";
  }

  return text.replace(SLACK_MENTION_REGEX, "").replace(/\s+/g, " ").trim();
}

function isThreadFollowUpMessage(message: SlackMessagePayload): boolean {
  return Boolean(message.thread_ts && message.thread_ts !== message.ts);
}

function sanitizeSlackLabelValue(value?: string): string {
  if (!value) {
    return "";
  }
  return value.replace(/\s+/g, " ").trim();
}

function formatSlackUserLabel(userRecord: SlackUserRecord | undefined, userId: string): string {
  const email = sanitizeSlackLabelValue(userRecord?.profile?.email);
  const displayNameCandidates = [
    userRecord?.profile?.display_name,
    userRecord?.real_name,
    userRecord?.profile?.real_name,
    userRecord?.name,
  ];
  const displayName =
    displayNameCandidates
      .map((nameCandidate) => sanitizeSlackLabelValue(nameCandidate))
      .find((nameCandidate) => Boolean(nameCandidate)) || "";

  if (displayName && email) {
    return `${displayName} (${email})`;
  }
  if (displayName) {
    return displayName;
  }
  if (email) {
    return email;
  }
  return `User (${userId})`;
}

function inferThreadMessageSpeaker(
  message: SlackMessagePayload,
  userLabelsById: Map<string, string>,
): string {
  if (message.bot_id || message.subtype === "bot_message") {
    return "Assistant";
  }
  if (message.user) {
    return userLabelsById.get(message.user) || `User (${message.user})`;
  }
  return "User";
}

async function resolveThreadUserLabels(
  typedClient: TypedSlackClient,
  priorMessages: SlackMessagePayload[],
): Promise<Map<string, string>> {
  const userLabelsById = new Map<string, string>();
  const userIds = Array.from(
    new Set(
      priorMessages
        .filter((message) => !message.bot_id && Boolean(message.user))
        .map((message) => message.user as string),
    ),
  );

  for (const userId of userIds) {
    try {
      const userInfoResult = await typedClient.users.info({ user: userId });
      const userLabel = formatSlackUserLabel(userInfoResult.user, userId);
      userLabelsById.set(userId, userLabel);
    } catch (error) {
      console.error(`Failed to resolve Slack user info for ${userId}:`, error);
      userLabelsById.set(userId, `User (${userId})`);
    }
  }

  return userLabelsById;
}

async function fetchPriorThreadMessages(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
): Promise<SlackMessagePayload[]> {
  if (!typedMessage.channel || !typedMessage.thread_ts) {
    return [];
  }

  const allThreadMessages: SlackMessagePayload[] = [];
  let cursor: string | undefined;

  do {
    const apiOptions: Record<string, unknown> = {
      channel: typedMessage.channel,
      ts: typedMessage.thread_ts,
      limit: 200,
    };
    if (cursor) {
      apiOptions.cursor = cursor;
    }

    const rawResponse = await typedClient.apiCall("conversations.replies", apiOptions);
    const response = rawResponse as SlackConversationsRepliesResponse;
    if (Array.isArray(response.messages)) {
      allThreadMessages.push(...response.messages);
    }

    const nextCursor = response.response_metadata?.next_cursor?.trim();
    cursor = nextCursor || undefined;
  } while (cursor);

  const currentMessageTs = parseSlackTimestamp(typedMessage.ts);

  return allThreadMessages.filter((threadMessage) => {
    const normalizedText = sanitizeSlackThreadMessageText(threadMessage.text);
    if (!normalizedText) {
      return false;
    }

    if (threadMessage.ts === typedMessage.ts) {
      return false;
    }

    if (currentMessageTs === null) {
      return true;
    }

    const threadMessageTs = parseSlackTimestamp(threadMessage.ts);
    return threadMessageTs !== null && threadMessageTs < currentMessageTs;
  });
}

function buildThreadContextualQuery(
  query: string,
  priorMessages: SlackMessagePayload[],
  userLabelsById: Map<string, string>,
): string {
  const contextLines = priorMessages
    .map((message) => {
      const normalizedText = sanitizeSlackThreadMessageText(message.text);
      if (!normalizedText) {
        return null;
      }
      const speaker = inferThreadMessageSpeaker(message, userLabelsById);
      return `${speaker}: ${normalizedText}`;
    })
    .filter((line): line is string => Boolean(line));

  if (contextLines.length === 0) {
    return query;
  }

  return `Slack thread context:\n${contextLines.join("\n")}\n\nCurrent slack message: ${query}`;
}

async function buildQueryWithThreadContext(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
  query: string,
): Promise<string> {
  if (!isThreadFollowUpMessage(typedMessage)) {
    return query;
  }

  try {
    const priorMessages = await fetchPriorThreadMessages(typedClient, typedMessage);
    const userLabelsById = await resolveThreadUserLabels(typedClient, priorMessages);
    return buildThreadContextualQuery(query, priorMessages, userLabelsById);
  } catch (error) {
    console.error("Failed to fetch Slack thread context:", error);
    return query;
  }
}

function getCitationWebUrl(webUrl?: string): string {
  if (!webUrl) {
    return "";
  }
  if (/^https?:\/\//i.test(webUrl)) {
    return webUrl;
  }
  return `${process.env.FRONTEND_PUBLIC_URL || ""}${webUrl}`;
}

function buildCitationSources(citations?: CitationData[]): string[] {
  const citationLinks: string[] = [];

  for (const citation of citations || []) {
    const webUrl = getCitationWebUrl(citation.citationData.metadata.webUrl);
    if (!webUrl) {
      continue;
    }

    const chunkIndex = citation.citationData.chunkIndex;
    if (chunkIndex) {
      citationLinks.push(`<${webUrl}|[${chunkIndex}]>`);
      continue;
    }
    citationLinks.push(`<${webUrl}|${webUrl}>`);
  }

  return citationLinks;
}



function toMrkdwn(input: string): string {
  if (!input) return "";

  return input
    // convert escaped newlines (\\n) into actual newlines
    .replace(/\\n/g, "\n")
    // normalize CRLF -> LF
    .replace(/\r\n/g, "\n")
    // trim spaces around lines
    .split("\n")
    .map(line => line.trim())
    .join("\n")
    .trim();
}

function buildChatStreamUrl(
  conversationId: string | null,
  agentId: string | null,
): string {
  const backendUrl = process.env.BACKEND_URL || "http://localhost:3000";
  if (!backendUrl) {
    throw new Error("BACKEND_URL environment variable is not set.");
  }

  if (agentId) {
    const encodedAgentId = encodeURIComponent(agentId);
    return conversationId
      ? `${backendUrl}/api/v1/agents/${encodedAgentId}/conversations/internal/${conversationId}/messages/stream`
      : `${backendUrl}/api/v1/agents/${encodedAgentId}/conversations/internal/stream`;
  }
  
  return conversationId
    ? `${backendUrl}/api/v1/conversations/internal/${conversationId}/messages/stream`
    : `${backendUrl}/api/v1/conversations/internal/stream`;
}

async function resolveSlackBotForEvent(
  // typedMessage: SlackMessagePayload,
  // typedContext: TypedSlackContext,
): Promise<SlackBotConfig | null> {
  // let latestBots: SlackBotConfig[] = [];
  // try {
  //   latestBots = await refreshSlackBotRegistry({ force: true });
  // } catch (error) {
  //   console.error("Failed to refresh Slack bot registry for event routing:", error);
  //   latestBots = getCachedSlackBots();
  // }

  // if (latestBots.length === 0) {
  //   return null;
  // }

  const matchedFromRequestContext = getCurrentMatchedSlackBot();
  if (matchedFromRequestContext) {
    return matchedFromRequestContext;
    // const refreshedMatchedBot = findSlackBotByIdentity(latestBots, {
    //   teamId: matchedFromRequestContext.teamId,
    //   botId: matchedFromRequestContext.botId,
    //   botUserId: matchedFromRequestContext.botUserId,
    // });
    // if (refreshedMatchedBot) {
    //   return refreshedMatchedBot;
    // }
  }

  // const matchedFromContext = findSlackBotByIdentity(latestBots, {
  //   teamId: typedContext.matchedBotTeamId || typedContext.teamId,
  //   botId: typedContext.matchedBotId || typedMessage.bot_id,
  //   botUserId: typedContext.matchedBotUserId || typedContext.botUserId,
  // });
  // if (matchedFromContext) {
  //   return matchedFromContext;
  // }

  // return findSlackBotByIdentity(latestBots, {
  //   teamId: typedContext.teamId,
  //   botId: typedMessage.bot_id,
  //   botUserId: typedContext.botUserId,
  // });
  return null;
}

// Middleware setup
receiver.router.use(json());
receiver.router.use(urlencoded());

// Routes
receiver.router.get("/", (req: Request, res: Response) => {
  console.log(req);
  res.send("Running");
});


receiver.router.post("slack/command", (req: Request, res: Response) => {
  if (req.body.type === "url_verification") {
    res.send({ challenge: req.body.challenge });
  } else {
    res.status(200).send();
  }
});


async function processSlackMessage(
  typedMessage: SlackMessagePayload,
  typedClient: TypedSlackClient,
  typedContext: TypedSlackContext,
  query: string,
  resolvedSlackBot: SlackBotConfig | null,
): Promise<void> {

  if (!typedMessage.user || !typedMessage.channel) {
    return;
  }

  const threadId = resolveThreadId(typedMessage);
  
  const lookupResult = await typedClient.users.info({
    user: typedMessage.user,
  });
  


  if (!lookupResult.user?.profile?.email) {
    console.error("Failed to get user email");
    await sendUserFacingSlackErrorMessage(
      typedClient,
      typedMessage,
      "I couldn't verify your Slack profile details right now. Please try again in a moment.",
    );
    return;
  }

  const email = lookupResult.user.profile.email;
  const configService = ConfigService.getInstance();
  const accessToken = slackJwtGenerator(email, await configService.getScopedJwtSecret());

  const currentAgentId = resolvedSlackBot?.agentId || null;
  console.log("currentAgentId", currentAgentId);
  const currentBotId = resolvedSlackBot?.botId;
  if (!currentBotId) {
    throw new Error("Unable to resolve Slack bot id for conversation persistence.");
  }

  const conversation = await getFromDatabase(
    threadId,
    currentBotId,
  );
  let streamTs: string | null = null;
  let streamStopped = false;
  let waitingMessageTs: string | null = null;

  const sendOrUpdateNonStreamMessage = async (text: string): Promise<void> => {
    const truncatedText = truncateForSlack(text);
    if (waitingMessageTs) {
      try {
        await typedClient.chat.update({
          channel: typedMessage.channel!,
          ts: waitingMessageTs,
          text: truncatedText,
        });
        return;
      } catch (error) {
        console.error("Error updating Slack waiting message:", error);
      }
    }

    await typedClient.chat.postMessage({
      channel: typedMessage.channel!,
      thread_ts: threadId,
      text: truncatedText,
    });
  };

  const stopSlackStream = async (markdownText?: string): Promise<boolean> => {
    if (!streamTs || streamStopped) {
      return true;
    }

    const payload: Record<string, unknown> = {
      channel: typedMessage.channel!,
      ts: streamTs,
    };
    if (typeof markdownText === "string" && markdownText.length > 0) {
      payload.markdown_text = truncateForSlackStreamMarkdown(markdownText);
    }

    try {
      await typedClient.apiCall("chat.stopStream", payload);
      streamStopped = true;
      return true;
    } catch (error) {
      if (
        error &&
        typeof error === "object" &&
        "data" in error &&
        (error as { data?: { error?: string } }).data?.error ===
          "message_not_in_streaming_state"
      ) {
        streamStopped = true;
        return true;
      }
      console.error("Error stopping Slack stream:", error);
      return false;
    }
  };

  try {
    const streamRecipientPayload: Record<string, unknown> = {};
    streamRecipientPayload.recipient_user_id = typedMessage.user;
    if (typedContext.teamId) {
      streamRecipientPayload.recipient_team_id = typedContext.teamId;
    }

    try {
      const waitingMessage = await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: "_Thinking..._",
      });
      waitingMessageTs = waitingMessage.ts || null;
    } catch (error) {
      console.error("Error posting Slack waiting message:", error);
    }

    const url = buildChatStreamUrl(conversation, currentAgentId);
    const response = await axios.post(
      url,
      {
        query,
        chatMode: "quick",
      },
      {
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        responseType: "stream",
      },
    );

    const responseStream = response.data as NodeJS.ReadableStream;
    let sseBuffer = "";
    let pendingAppendText = "";
    let lastAppendAt = 0;
    let streamErrorMessage: string | null = null;
    let completionConversation: ConversationData["conversation"] | null = null;
    let queuedStreamAppend: Promise<void> = Promise.resolve();

    const pushTextToSlackStream = async (text: string): Promise<void> => {
      if (!text || !text.trim()) {
        return;
      }

      const markdownChunks = splitByLength(text, SLACK_STREAM_MARKDOWN_LIMIT);
      if (markdownChunks.length === 0) {
        return;
      }

      const processedChunks = markdownChunks
        .map(chunk => toMrkdwn(chunk))
        .filter(chunk => chunk.length > 0);
      if (processedChunks.length === 0) {
        return;
      }

      let chunksToAppend = processedChunks;
      if (!streamTs) {
        const [firstChunk, ...restChunks] = processedChunks;
        if (!firstChunk) {
          return;
        }
        const startStreamResult = (await typedClient.apiCall(
          "chat.startStream",
          {
            channel: typedMessage.channel!,
            thread_ts: threadId,
            markdown_text: firstChunk,
            ...streamRecipientPayload,
          },
        )) as StreamStartResult;

        if (!startStreamResult.ts) {
          throw new Error("Failed to start Slack stream");
        }
        streamTs = startStreamResult.ts;
        if (waitingMessageTs) {
          try {
            await typedClient.apiCall("chat.delete", {
              channel: typedMessage.channel!,
              ts: waitingMessageTs,
            });
          } catch (error) {
            console.error("Error deleting Slack waiting message:", error);
          } finally {
            waitingMessageTs = null;
          }
        }
        chunksToAppend = restChunks;
      }

      for (const appendChunk of chunksToAppend) {
        await typedClient.apiCall("chat.appendStream", {
          channel: typedMessage.channel!,
          ts: streamTs,
          markdown_text: appendChunk,
        });
      }
    };

    const flushPendingAppend = (): void => {
      const textToAppend = pendingAppendText;
      if (!textToAppend) {
        return;
      }

      pendingAppendText = "";
      queuedStreamAppend = queuedStreamAppend
        .then(async () => pushTextToSlackStream(textToAppend))
        .catch((error) => {
          console.error("Error appending Slack stream text:", error);
          if (!streamErrorMessage) {
            streamErrorMessage = STREAM_FAILURE_MESSAGE;
          }
        });
    };

    await new Promise<void>((resolve, reject) => {
      responseStream.setEncoding("utf8");
      responseStream.on("data", (chunk: string) => {
        sseBuffer += chunk;
        const { events, remainder } = parseSSEEvents(sseBuffer);
        sseBuffer = remainder;

        for (const evt of events) {
          if (evt.event === "answer_chunk" || evt.event === "chunk") {
            const nextChunk = extractStreamChunk(evt.data);
            if (!nextChunk || !nextChunk.trim()) {
              continue;
            }

            pendingAppendText += nextChunk;
            const now = Date.now();
            if (now - lastAppendAt >= STREAM_UPDATE_THROTTLE_MS) {
              lastAppendAt = now;
              flushPendingAppend();
            }
          } else if (evt.event === "complete") {
            if (
              evt.data &&
              typeof evt.data === "object" &&
              "conversation" in evt.data
            ) {
              completionConversation = (evt.data as ConversationData).conversation;
            }
          } else if (evt.event === "error") {
            streamErrorMessage = resolveSlackErrorMessage(evt.data);
          }
        }
      });

      responseStream.on("end", () => resolve());
      responseStream.on("error", (error) => reject(error));
    });

    flushPendingAppend();
    await queuedStreamAppend;

    if (streamErrorMessage) {
      if (streamTs) {
        const stopStreamSucceeded = await stopSlackStream(streamErrorMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(streamErrorMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(streamErrorMessage);
      }
      return;
    }

    const conversationData =
      completionConversation as ConversationData["conversation"] | null;
    if (!conversationData) {
      const incompleteResponseMessage =
        "Received an incomplete response from the backend. Please try again later.";
      if (streamTs) {
        const stopStreamSucceeded = await stopSlackStream(incompleteResponseMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(incompleteResponseMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(incompleteResponseMessage);
      }
      return;
    }

    if (!conversation) {
      const conversationId = conversationData._id;
      await saveToDatabase({
        threadId: threadId,
        conversationId,
        botId: currentBotId
      });
    }

    const botResponses = conversationData.messages;
    const botResponse = botResponses.length > 0 ? botResponses[botResponses.length - 1] : null;
    if (!botResponse || botResponse.messageType !== "bot_response") {
      const invalidResponseMessage =
        "Received an unexpected response format from the backend. Please try again later.";
      if (streamTs) {
        const stopStreamSucceeded = await stopSlackStream(invalidResponseMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(invalidResponseMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(invalidResponseMessage);
      }
      return;
    }

    if (!streamTs && botResponse.content) {
      await pushTextToSlackStream(botResponse.content);
    }

    const citationLinks = buildCitationSources(botResponse.citations);

    const convertedFinalText = markdownToSlackMrkdwn(botResponse.content || "");
    const sourcesLine =
      citationLinks.length > 0
        ? `\n\n*Sources:* ${citationLinks.join(" ")}`
        : "";

    const finalMessageText = `${convertedFinalText}${sourcesLine}`;
    const finalStreamMessageText = buildFinalStreamOverwriteMessage(
      convertedFinalText,
      sourcesLine,
    );

    if (streamTs) {
      await stopSlackStream();
      await typedClient.apiCall("chat.update", {
        channel: typedMessage.channel!,
        ts: streamTs,
        markdown_text: finalStreamMessageText,
      });
    } else {
      await sendOrUpdateNonStreamMessage(truncateForSlack(finalMessageText));
    }
  } catch (error) {
    console.error("Error calling the Chat API:", error);
    const errorMessage = resolveSlackErrorMessage(error);
    if (streamTs) {
      const stopStreamSucceeded = await stopSlackStream(errorMessage);
      if (!stopStreamSucceeded) {
        await sendOrUpdateNonStreamMessage(errorMessage);
      }
    } else {
      await sendOrUpdateNonStreamMessage(errorMessage);
    }
  }
}

function isIgnoredSlackMessage(
  typedMessage: SlackMessagePayload,
  typedContext: TypedSlackContext,
): boolean {
  return Boolean(
    typedMessage.subtype === "bot_message" ||
    typedMessage.bot_id ||
    typedMessage.user === typedContext.botUserId ||
    typedMessage.files,
  );
}

// Handle DMs via message.im events.
app.message(async ({ message, client, context }) => {
  if (!message || typeof message !== "object") {
    return;
  }
  
  const typedMessage = message as SlackMessagePayload;
  const typedClient = client as unknown as TypedSlackClient;
  const typedContext = context as TypedSlackContext;

  if (isIgnoredSlackMessage(typedMessage, typedContext)) {
    return;
  }

  const isDirectMessage = typedMessage.channel?.startsWith("D") || false;
  if (!isDirectMessage) {
    return;
  }

  const query = typedMessage.text?.replace(SLACK_MENTION_REGEX, "").trim();
  if (!query) {
    return;
  }

  try {
    const resolvedSlackBot = await resolveSlackBotForEvent();
    await processSlackMessage(
      typedMessage,
      typedClient,
      typedContext,
      query,
      resolvedSlackBot,
    );
  } catch (error) {
    console.error("Error handling DM message:", error);
    await sendUserFacingSlackErrorMessage(typedClient, typedMessage, error);
  }
});

// Handle @mentions in channels via app_mention events.
app.event("app_mention", async ({ event, client, context }) => {
  const typedMessage = event as unknown as SlackMessagePayload;
  const typedClient = client as unknown as TypedSlackClient;
  const typedContext = context as TypedSlackContext;
  if (isIgnoredSlackMessage(typedMessage, typedContext)) {
    return;
  }

  const query = typedMessage.text?.replace(SLACK_MENTION_REGEX, "").trim();
  if (!query) {
    return;
  }

  try {
    const contextualQuery = await buildQueryWithThreadContext(
      typedClient,
      typedMessage,
      query,
    );
    const resolvedSlackBot = await resolveSlackBotForEvent();
    await processSlackMessage(
      typedMessage,
      typedClient,
      typedContext,
      contextualQuery,
      resolvedSlackBot,
    );
  } catch (error) {
    console.error("Error handling app mention:", error);
    await sendUserFacingSlackErrorMessage(typedClient, typedMessage, error);
  }
});



(async () => {
  await connect();
  try {
    await refreshSlackBotRegistry({ force: true });
  } catch (error) {
    console.error("Initial Slack bot registry refresh failed:", error);
  }
  await app.start(process.env.SLACK_BOT_PORT || 3020);
  console.log("Bolt app is running on 3020.");
})();