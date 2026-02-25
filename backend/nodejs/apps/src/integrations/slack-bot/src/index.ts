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
  };
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

interface NormalizedCitations {
  citationLinks: string[];
  chunkIndexToCitationNumber: Record<string, string>;
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

interface TypedSlackClient {
  botUserId?: string;
  users: {
    info: (params: { user: string }) => Promise<{
      user?: {
        profile?: {
          email?: string;
        };
      };
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

function getCitationWebUrl(webUrl?: string): string {
  if (!webUrl) {
    return "";
  }
  if (/^https?:\/\//i.test(webUrl)) {
    return webUrl;
  }
  return `${process.env.FRONTEND_PUBLIC_URL || ""}${webUrl}`;
}

function normalizeCitations(citations?: CitationData[]): NormalizedCitations {
  const uniqueUrls: string[] = [];
  const urlToCitationNumber = new Map<string, string>();
  const chunkIndexToCitationNumber: Record<string, string> = {};

  for (const citation of citations || []) {
    const chunkIndex = citation.citationData.chunkIndex;
    if (!chunkIndex) {
      continue;
    }

    const webUrl = getCitationWebUrl(citation.citationData.metadata.webUrl);
    if (!webUrl) {
      continue;
    }

    let citationNumber = urlToCitationNumber.get(webUrl);
    if (!citationNumber) {
      citationNumber = String(uniqueUrls.length + 1);
      uniqueUrls.push(webUrl);
      urlToCitationNumber.set(webUrl, citationNumber);
    }

    if (!chunkIndexToCitationNumber[chunkIndex]) {
      chunkIndexToCitationNumber[chunkIndex] = citationNumber;
    }
  }

  return {
    chunkIndexToCitationNumber,
    citationLinks: uniqueUrls.map((link, idx) => `<${link}|[${idx + 1}]>`),
  };
}

function remapCitationMarkers(text: string, citationMap: Record<string, string>): string {
  const remappedText = text.replace(/\[(\d+)\]/g, (match, chunkIndex: string) => {
    const mappedCitation = citationMap[chunkIndex];
    return mappedCitation ? `[${mappedCitation}]` : match;
  });

  return remappedText.replace(/(\[\d+\])(?:\s*)\1+/g, "$1");
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
  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    throw new Error("BACKEND_URL environment variable is not set.");
  }

  if (agentId) {
    console.log("using agent id", agentId);
    const encodedAgentId = encodeURIComponent(agentId);
    return conversationId
      ? `${backendUrl}/api/v1/agents/${encodedAgentId}/conversations/internal/${conversationId}/messages/stream`
      : `${backendUrl}/api/v1/agents/${encodedAgentId}/conversations/internal/stream`;
  }
  
  console.log("using default agent id");
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
  //   console.log("no bots found");
  //   return null;
  // }

  const matchedFromRequestContext = getCurrentMatchedSlackBot();
  if (matchedFromRequestContext) {
    console.log("matchedFromRequestContext", matchedFromRequestContext);
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
  console.log("no matched bot found");
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
  console.log("request");
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

  console.log("processSlackMessage", resolvedSlackBot);
  if (!typedMessage.user || !typedMessage.channel) {
    return;
  }

  const threadId = resolveThreadId(typedMessage);
  
  console.log("typedMessage", typedMessage);
  const lookupResult = await typedClient.users.info({
    user: typedMessage.user,
  });
  


  if (!lookupResult.user?.profile?.email) {
    console.log("lookupResult", lookupResult);
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
  const conversation = await getFromDatabase(
    threadId,
    email,
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
      if (!text) {
        return;
      }

      const markdownChunks = splitByLength(text, SLACK_STREAM_MARKDOWN_LIMIT);
      if (markdownChunks.length === 0) {
        return;
      }

      let chunksToAppend = markdownChunks;
      if (!streamTs) {
        const [firstChunk, ...restChunks] = markdownChunks;
        const processedFirstChunk = toMrkdwn(firstChunk || "");
        const startStreamResult = (await typedClient.apiCall(
          "chat.startStream",
          {
            channel: typedMessage.channel!,
            thread_ts: threadId,
            markdown_text: processedFirstChunk,
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
        const processedChunk = toMrkdwn(appendChunk);
        await typedClient.apiCall("chat.appendStream", {
          channel: typedMessage.channel!,
          ts: streamTs,
          markdown_text: processedChunk,
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
            if (!nextChunk) {
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
        email,
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
      console.log("pushed all content to slack stream");
      await pushTextToSlackStream(botResponse.content);
    }

    const { citationLinks, chunkIndexToCitationNumber } = normalizeCitations(
      botResponse.citations,
    );

    const convertedFinalText = markdownToSlackMrkdwn(botResponse.content || "");
    const remappedFinalText = remapCitationMarkers(
      convertedFinalText,
      chunkIndexToCitationNumber,
    );
    const sourcesLine =
      citationLinks.length > 0
        ? `\n\n*Sources:* ${citationLinks.join(" ")}`
        : "";

    const finalMessageText = `${remappedFinalText}${sourcesLine}`;
    const finalStreamMessageText = buildFinalStreamOverwriteMessage(
      remappedFinalText,
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
    const resolvedSlackBot = await resolveSlackBotForEvent();
    await processSlackMessage(
      typedMessage,
      typedClient,
      typedContext,
      query,
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