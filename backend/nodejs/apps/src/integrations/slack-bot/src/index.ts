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

function resolveSlackErrorMessage(error: unknown): string {
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

function truncateForSlack(text: string): string {
  if (text.length <= SLACK_MAX_TEXT_LENGTH) {
    return text;
  }
  return `...${text.slice(-(SLACK_MAX_TEXT_LENGTH - 3))}`;
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

// const processMarkdownContent = (content: string): string => {
//   if (!content) return '';

//   return content
//     // Fix escaped newlines
//     .replace(/\\n/g, '\n').replace(/\n/g, '  ')
//     // Clean up trailing whitespace but preserve structure
//     .trim();
// };

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
): Promise<void> {
  if (!typedMessage.user || !typedMessage.channel) {
    return;
  }

  const threadId = typedMessage.thread_ts || typedMessage.ts;

  const lookupResult = await typedClient.users.info({
    user: typedMessage.user,
  });

  if (!lookupResult.user?.profile?.email) {
    console.error("Failed to get user email");
    return;
  }

  const email = lookupResult.user.profile.email;
  const configService = ConfigService.getInstance();
  const accessToken = slackJwtGenerator(email, await configService.getScopedJwtSecret());

  const conversation = await getFromDatabase(threadId, email);
  let streamTs: string | null = null;
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

    const url = conversation
      ? `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/conversations/internal/${conversation}/messages/stream`
      : `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/conversations/internal/stream`;

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
        });
    };

    await new Promise<void>((resolve, reject) => {
      responseStream.on("data", (chunk: Buffer | string) => {
        sseBuffer += chunk.toString();
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
        await typedClient.apiCall("chat.stopStream", {
          channel: typedMessage.channel!,
          ts: streamTs,
          markdown_text: truncateForSlack(streamErrorMessage),
        });
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
        await typedClient.apiCall("chat.stopStream", {
          channel: typedMessage.channel!,
          ts: streamTs,
          markdown_text: incompleteResponseMessage,
        });
      } else {
        await sendOrUpdateNonStreamMessage(incompleteResponseMessage);
      }
      return;
    }

    if (!conversation) {
      const conversationId = conversationData._id;
      await saveToDatabase({ threadId: threadId, conversationId, email });
    }

    const botResponses = conversationData.messages;
    const botResponse = botResponses.length > 0 ? botResponses[botResponses.length - 1] : null;
    if (!botResponse || botResponse.messageType !== "bot_response") {
      const invalidResponseMessage =
        "Received an unexpected response format from the backend. Please try again later.";
      if (streamTs) {
        await typedClient.apiCall("chat.stopStream", {
          channel: typedMessage.channel!,
          ts: streamTs,
          markdown_text: invalidResponseMessage,
        });
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

    const convertedFinalText = truncateForSlack(
      markdownToSlackMrkdwn(botResponse.content || ""),
    );
    const remappedFinalText = remapCitationMarkers(
      convertedFinalText,
      chunkIndexToCitationNumber,
    );
    const sourcesLine =
      citationLinks.length > 0
        ? `\n\n*Sources:* ${citationLinks.join(" ")}`
        : "";
    const finalMessageText = truncateForSlack(`${remappedFinalText}${sourcesLine}`);

    if (streamTs) {
      await typedClient.apiCall("chat.stopStream", {
        channel: typedMessage.channel!,
        ts: streamTs,
      });

      // Overwrite streamed content with fully converted mrkdwn text.
      await typedClient.chat.update({
        channel: typedMessage.channel!,
        ts: streamTs,
        text: finalMessageText,
      });
    } else {
      await sendOrUpdateNonStreamMessage(finalMessageText);
    }
  } catch (error) {
    console.error("Error calling the Chat API:", error);
    const errorMessage = resolveSlackErrorMessage(error);
    if (streamTs) {
      await typedClient.apiCall("chat.stopStream", {
        channel: typedMessage.channel!,
        ts: streamTs,
        markdown_text: truncateForSlack(errorMessage),
      });
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

  const query = typedMessage.text?.trim();
  if (!query) {
    return;
  }

  try {
    await processSlackMessage(typedMessage, typedClient, typedContext, query);
  } catch (error) {
    console.error("Error handling DM message:", error);
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

  const query = typedMessage.text?.replace(/<@[A-Z0-9]+>/g, "").trim();
  if (!query) {
    return;
  }

  try {
    await processSlackMessage(typedMessage, typedClient, typedContext, query);
  } catch (error) {
    console.error("Error handling app mention:", error);
  }
});



(async () => {
  await connect();
  await app.start(process.env.SLACK_BOT_PORT || 3020);
  console.log("Bolt app is running on 3020.");
})();