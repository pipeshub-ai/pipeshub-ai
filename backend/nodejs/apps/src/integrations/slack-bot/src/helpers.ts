import axios from "axios";
import FormData from "form-data";
import { markdownToSlackMrkdwn, markdownToText } from "./utils/md_to_mrkdwn";
import { type SlackBotConfig } from "./botRegistry";

// ---------------------------------------------------------------------------
// Interfaces & Types
// ---------------------------------------------------------------------------

export interface CitationData {
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
      connector?: string;
    };
    chunkIndex?: string | number;
  }
}

export interface BotResponse {
  content: string;
  citations?: CitationData[];
  messageType: string;
}

export interface ConversationData {
  conversation: {
    _id: string;
    messages: BotResponse[];
  };
  [key: string]: unknown;
}

export interface StreamEvent {
  event: string;
  data: unknown;
}

export interface StreamStartResult {
  ts?: string;
}

export type SlackBlock = Record<string, unknown>;

export interface SlackMessagePayload {
  subtype?: string;
  bot_id?: string;
  user?: string;
  files?: unknown[];
  text?: string;
  thread_ts?: string;
  ts: string;
  channel?: string;
}

export interface SlackConversationsRepliesResponse {
  messages?: SlackMessagePayload[];
  response_metadata?: {
    next_cursor?: string;
  };
}

export interface SlackUserProfile {
  email?: string;
  display_name?: string;
  real_name?: string;
}

export interface SlackUserRecord {
  id?: string;
  name?: string;
  real_name?: string;
  profile?: SlackUserProfile;
  tz?: string;
}

export interface TypedSlackClient {
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
      blocks?: SlackBlock[];
      unfurl_links?: boolean;
      unfurl_media?: boolean;
    }) => Promise<{ ts?: string }>;
    update: (params: {
      channel: string;
      ts: string;
      text: string;
      blocks?: SlackBlock[];
      unfurl_links?: boolean;
      unfurl_media?: boolean;
    }) => Promise<{ ts?: string }>;
  };
  apiCall: (
    apiMethod: string,
    options?: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
}

export interface SlackFile {
  id: string;
  name?: string;
  mimetype?: string;
  filetype?: string;
  size?: number;
  url_private_download?: string;
  url_private?: string;
}

export interface AttachmentRef {
  recordId: string;
  recordName: string;
  mimeType: string;
  extension: string;
  virtualRecordId: string;
}

export interface SlackFileClassification {
  supported: SlackFile[];
  unsupported: SlackFile[];
  oversized: SlackFile[];
}

export interface SlackAttachmentUploadResult {
  attachments: AttachmentRef[];
  /** Text attachments whose content is not valid UTF-8 text; not uploaded. */
  unreadable: SlackFile[];
  /** Attachments whose downloaded size is over the limit; not uploaded. */
  oversized: SlackFile[];
}

export interface CachedUserInfo {
  userRecord: SlackUserRecord | undefined;
  timestamp: number;
}

export type MarkdownTableSegment =
  | { type: "markdown"; content: string }
  | { type: "table"; header: string[]; rows: string[][] };

export type FenceMarker = "`" | "~";

interface ParsedMarkdownTable {
  header: string[];
  rows: string[][];
  nextLineIndex: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const FAILED_RESPONSE_GENERATION_MESSAGE = 'Something went wrong while generating the response. Please try again later.';
/** Batch answer tokens briefly, then flush — long gaps feel like a stuck stream. */
export const STREAM_UPDATE_THROTTLE_MS = 400;
/** Flush immediately once this much answer text is buffered (avoid huge appends). */
export const STREAM_UPDATE_MAX_CHARS = 400;
/** Activity message updates can be slower; they must not block answer appends. */
export const ACTIVITY_UPDATE_THROTTLE_MS = 900;
export const SLACK_MAX_TEXT_LENGTH = 39000;
export const SLACK_STREAM_MARKDOWN_LIMIT = 11500;
export const SLACK_STREAM_MESSAGE_CHAR_LIMIT = 11500;
export const MAX_SLACK_ERROR_BODY_LENGTH = 64000;
export const SLACK_BLOCKS_PER_MESSAGE_LIMIT = 50;
/** Slack cumulative block text limit per message (~13,200 chars in practice); use 10k to stay safe. */
export const SLACK_BLOCKS_TOTAL_TEXT_LIMIT = 10000;
/** Maximum rows (including header) per table block to prevent msg_blocks_too_long. */
export const MAX_TABLE_ROWS = 100;
/** Maximum columns per table block; extra columns are silently dropped. */
export const MAX_TABLE_COLS = 20;
/** Maximum character count per table block; Slack enforces a hard limit of 10,000. */
export const MAX_TABLE_CHARS = 9500;
export const SLACK_SECTION_TEXT_LIMIT = 3000;
export const SLACK_SECTION_FIELD_TEXT_LIMIT = 2000;
export const SLACK_SECTION_FIELDS_PER_BLOCK_LIMIT = 10;
export const NO_UNFURL_OPTIONS = {
  unfurl_links: false,
  unfurl_media: false,
} as const;
export const DEFAULT_SLACK_ERROR_MESSAGE = "Something went wrong! Please try again later.";
export const MAX_USER_VISIBLE_ERROR_LENGTH = 320;
export const STREAM_FAILURE_MESSAGE =
  "I ran into an issue while streaming the response. Please try again.";
export const BACKEND_STREAM_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes
export const TABLE_STREAMING_PAUSED_HINT =
  "\n\n:hourglass_flowing_sand:";

export const SUPPORTED_ATTACHMENT_MIMETYPES = new Set([
  "image/jpeg",
  "image/jpg",
  "image/png",
  "application/pdf",
]);

/**
 * Text attachments by extension, with the MIME type sent to the chat upload
 * endpoint. That endpoint picks its parser from the MIME type, and Slack often
 * labels these files text/plain or application/octet-stream, so the extension
 * decides. Only types the upload endpoint parses belong here.
 */
export const TEXT_ATTACHMENT_EXTENSION_MIMETYPES: Readonly<Record<string, string>> = {
  txt: "text/plain",
  md: "text/markdown",
  markdown: "text/markdown",
  mdx: "text/mdx",
  csv: "text/csv",
  tsv: "text/tab-separated-values",
};

const TEXT_ATTACHMENT_MIMETYPES: Readonly<Record<string, string>> = {
  "text/plain": "text/plain",
  "text/markdown": "text/markdown",
  "text/x-markdown": "text/markdown",
  "text/mdx": "text/mdx",
  "text/csv": "text/csv",
  "text/tab-separated-values": "text/tab-separated-values",
};

const GENERIC_MIMETYPES = new Set(["", "application/octet-stream", "binary/octet-stream"]);

/** Slack `filetype` values for text files, used only when the file name has no extension. */
const SLACK_TEXT_FILETYPE_EXTENSIONS: Readonly<Record<string, string>> = {
  text: "txt",
  markdown: "md",
  csv: "csv",
  tsv: "tsv",
};

export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
export const MAX_ATTACHMENT_MB = Math.floor(MAX_ATTACHMENT_BYTES / (1024 * 1024));

export const SUPPORTED_ATTACHMENTS_HINT =
  "I can read PDF, JPEG and PNG files, and text files: plain text (.txt), " +
  "Markdown (.md, .markdown, .mdx), CSV (.csv) and TSV (.tsv). " +
  `Each file can be up to ${MAX_ATTACHMENT_MB} MB.`;

export const USER_INFO_CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 1 day

// ---------------------------------------------------------------------------
// User info cache
// ---------------------------------------------------------------------------

export const userInfoCache = new Map<string, CachedUserInfo>();

export function getCachedUserInfo(userId: string): SlackUserRecord | undefined | null {
  const cached = userInfoCache.get(userId);
  if (!cached) {
    return null; // Not in cache
  }

  const now = Date.now();
  if (now - cached.timestamp > USER_INFO_CACHE_TTL_MS) {
    userInfoCache.delete(userId); // Expired
    return null;
  }

  return cached.userRecord;
}

export function setCachedUserInfo(userId: string, userRecord: SlackUserRecord | undefined): void {
  userInfoCache.set(userId, {
    userRecord,
    timestamp: Date.now(),
  });
}

// ---------------------------------------------------------------------------
// Attachment helpers
// ---------------------------------------------------------------------------

function normalizeMimetype(mimetype: string | undefined): string {
  return (mimetype ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
}

function slackFileExtension(file: SlackFile): string | null {
  const name = file.name?.trim() ?? "";
  const dot = name.lastIndexOf(".");
  if (dot > 0 && dot < name.length - 1) {
    return name.slice(dot + 1).toLowerCase();
  }
  const filetype = file.filetype?.trim().toLowerCase() ?? "";
  if (filetype === "") return null;
  return SLACK_TEXT_FILETYPE_EXTENSIONS[filetype] ?? filetype;
}

/**
 * The MIME type to upload a supported text attachment with, or null when the
 * file is not one. The extension and MIME type must agree: a known text
 * extension needs a text/* or generic MIME type, and a file with no extension
 * needs a known text MIME type.
 */
export function resolveTextAttachmentMimetype(file: SlackFile): string | null {
  const mime = normalizeMimetype(file.mimetype);
  const extension = slackFileExtension(file);
  if (extension) {
    const mimeForExtension = TEXT_ATTACHMENT_EXTENSION_MIMETYPES[extension];
    if (mimeForExtension === undefined) return null;
    return mime.startsWith("text/") || GENERIC_MIMETYPES.has(mime) ? mimeForExtension : null;
  }
  return TEXT_ATTACHMENT_MIMETYPES[mime] ?? null;
}

export function isSlackSupportedAttachment(file: SlackFile): boolean {
  return (
    SUPPORTED_ATTACHMENT_MIMETYPES.has(normalizeMimetype(file.mimetype)) ||
    resolveTextAttachmentMimetype(file) !== null
  );
}

const DELIMITED_TEXT_MIMETYPES = new Set(["text/csv", "text/tab-separated-values"]);

function isDelimitedTextAttachment(file: SlackFile): boolean {
  const mime = resolveTextAttachmentMimetype(file);
  return mime !== null && DELIMITED_TEXT_MIMETYPES.has(mime);
}

/**
 * The chat CSV/TSV parser reads a byte-order mark, then UTF-8, then falls back to
 * Windows-1252 (cp1252) with undefined bytes replaced, so it decodes any byte and a
 * Windows Excel CSV works there. Only NUL bytes, which mean a binary file renamed
 * to .csv, are refused.
 */
export function isReadableDelimitedText(binary: Buffer): boolean {
  return !binary.includes(0);
}

/** Strict UTF-8 check; NUL bytes mean a binary file (or UTF-16) renamed to a text extension. */
export function isReadableUtf8Text(binary: Buffer): boolean {
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(binary);
    return !text.includes("\u0000");
  } catch {
    return false;
  }
}

export function isSlackOversizedAttachment(file: SlackFile): boolean {
  return typeof file.size === "number" && file.size > MAX_ATTACHMENT_BYTES;
}

export function classifySlackFiles(files: unknown[] | undefined): SlackFileClassification {
  if (!files || !Array.isArray(files)) return { supported: [], unsupported: [], oversized: [] };
  const supported: SlackFile[] = [];
  const unsupported: SlackFile[] = [];
  const oversized: SlackFile[] = [];
  for (const f of files) {
    if (typeof f !== "object" || f === null) continue;
    const file = f as SlackFile;
    const downloadable = Boolean(file.url_private_download || file.url_private);
    if (isSlackOversizedAttachment(file)) {
      oversized.push(file);
    } else if (isSlackSupportedAttachment(file) && downloadable) {
      supported.push(file);
    } else {
      unsupported.push(file);
    }
  }
  return { supported, unsupported, oversized };
}

export function extractSupportedAttachments(files: unknown[] | undefined): SlackFile[] {
  return classifySlackFiles(files).supported;
}

export class SlackFileTooLargeError extends Error {
  constructor(fileId: string) {
    super(`Slack file ${fileId} is larger than ${MAX_ATTACHMENT_BYTES} bytes`);
    this.name = "SlackFileTooLargeError";
  }
}

function isAxiosMaxContentLengthError(error: unknown): boolean {
  return axios.isAxiosError(error) && /maxContentLength size of \d+ exceeded/.test(error.message);
}

export async function downloadSlackFile(
  file: SlackFile,
  botToken: string,
): Promise<Buffer> {
  const url = file.url_private_download || file.url_private;
  if (!url) throw new Error(`No download URL for file ${file.id}`);
  try {
    const response = await axios.get(url, {
      headers: { Authorization: `Bearer ${botToken}` },
      responseType: "arraybuffer",
      timeout: 60_000,
      // Slack's size field is optional, so cap the transfer itself.
      maxContentLength: MAX_ATTACHMENT_BYTES,
    });
    return Buffer.from(response.data);
  } catch (error) {
    if (isAxiosMaxContentLengthError(error)) throw new SlackFileTooLargeError(file.id);
    throw error;
  }
}

export async function uploadSlackAttachments(
  files: SlackFile[],
  botToken: string,
  accessToken: string,
  agentId?: string | null,
): Promise<SlackAttachmentUploadResult> {
  const backendUrl = process.env.BACKEND_URL || "http://localhost:3000";
  const form = new FormData();
  const result: SlackAttachmentUploadResult = { attachments: [], unreadable: [], oversized: [] };

  const binaries = await Promise.all(
    files.map(async (file) => {
      try {
        return await downloadSlackFile(file, botToken);
      } catch (error) {
        if (error instanceof SlackFileTooLargeError) return null;
        throw error;
      }
    }),
  );
  let appended = 0;
  files.forEach((file, i) => {
    const binary = binaries[i];
    if (!binary || binary.length > MAX_ATTACHMENT_BYTES) {
      result.oversized.push(file);
      return;
    }
    const textMimetype = resolveTextAttachmentMimetype(file);
    const readable =
      textMimetype === null ||
      (DELIMITED_TEXT_MIMETYPES.has(textMimetype)
        ? isReadableDelimitedText(binary)
        : isReadableUtf8Text(binary));
    if (!readable) {
      result.unreadable.push(file);
      return;
    }
    const fileName = file.name || `attachment_${file.id}.${file.filetype || "bin"}`;
    form.append("files", binary, {
      filename: fileName,
      contentType: textMimetype ?? (file.mimetype || "application/octet-stream"),
    });
    appended += 1;
  });

  if (appended === 0) return result;

  const uploadUrl = agentId
    ? `${backendUrl}/api/v1/agents/${encodeURIComponent(agentId)}/conversations/internal/attachments/upload`
    : `${backendUrl}/api/v1/conversations/internal/attachments/upload`;

  const uploadResponse = await axios.post(
    uploadUrl,
    form,
    {
      headers: {
        ...form.getHeaders(),
        Authorization: `Bearer ${accessToken}`,
      },
      maxBodyLength: Infinity,
      maxContentLength: Infinity,
      timeout: 120_000,
    },
  );

  result.attachments = (uploadResponse.data?.attachments || []) as AttachmentRef[];
  return result;
}

// ---------------------------------------------------------------------------
// SSE parsing
// ---------------------------------------------------------------------------

export function parseSSEEvents(buffer: string): { events: StreamEvent[]; remainder: string } {
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

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

export function readMessageFromObject(value: unknown): string | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const record = value as Record<string, unknown>;
  const directKeys = ["message", "error", "detail", "reason", "msg"] as const;

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

export function isReadableStreamLike(value: unknown): value is NodeJS.ReadableStream {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return typeof candidate.on === "function";
}

export function readMessageFromTextPayload(payload: string): string | null {
  const normalizedPayload = payload.trim();
  if (!normalizedPayload) {
    return null;
  }

  const isSSEPayload =
    normalizedPayload.includes("event:") && normalizedPayload.includes("data:");
  if (isSSEPayload) {
    const payloadForParser = normalizedPayload.endsWith("\n\n")
      ? normalizedPayload
      : `${normalizedPayload}\n\n`;
    const { events } = parseSSEEvents(payloadForParser);
    for (const event of events) {
      if (event.event !== "error") {
        continue;
      }
      const streamMessage =
        readMessageFromObject(event.data) ||
        (typeof event.data === "string" ? event.data.trim() : null);
      if (streamMessage) {
        return streamMessage;
      }
    }
  }

  try {
    const parsed = JSON.parse(normalizedPayload);
    const parsedMessage = readMessageFromObject(parsed);
    if (parsedMessage) {
      return parsedMessage;
    }
  } catch {
    // Ignore JSON parsing errors and fall back to plain text.
  }

  return normalizedPayload;
}

export async function readStreamToText(stream: NodeJS.ReadableStream): Promise<string> {
  return await new Promise<string>((resolve, reject) => {
    const chunks: string[] = [];
    let collectedLength = 0;

    stream.setEncoding?.("utf8");

    stream.on("data", (chunk: string | Buffer) => {
      if (collectedLength >= MAX_SLACK_ERROR_BODY_LENGTH) {
        return;
      }
      const chunkText = typeof chunk === "string" ? chunk : chunk.toString("utf8");
      const remainingLength = MAX_SLACK_ERROR_BODY_LENGTH - collectedLength;
      const clippedChunk = chunkText.slice(0, remainingLength);
      chunks.push(clippedChunk);
      collectedLength += clippedChunk.length;
    });

    stream.on("end", () => resolve(chunks.join("")));
    stream.on("error", (error) => reject(error));
  });
}

export async function readMessageFromAxiosResponseData(data: unknown): Promise<string | null> {
  if (!data) {
    return null;
  }

  if (typeof data === "string") {
    return readMessageFromTextPayload(data);
  }

  if (Buffer.isBuffer(data)) {
    return readMessageFromTextPayload(data.toString("utf8"));
  }

  const messageFromObject = readMessageFromObject(data);
  if (messageFromObject) {
    return messageFromObject;
  }

  if (isReadableStreamLike(data)) {
    try {
      const streamText = await readStreamToText(data);
      return readMessageFromTextPayload(streamText);
    } catch {
      return null;
    }
  }

  return null;
}

export function extractSlackErrorMessage(error: unknown): string {
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

export async function extractSlackErrorMessageAsync(error: unknown): Promise<string> {
  if (!error) {
    return DEFAULT_SLACK_ERROR_MESSAGE;
  }

  if (typeof error === "string" && error.trim()) {
    return error.trim();
  }

  if (axios.isAxiosError(error)) {
    const responseMessage = await readMessageFromAxiosResponseData(
      error.response?.data,
    );
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

export function normalizeSlackErrorMessage(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export function formatSlackErrorMessage(rawMessage: string): string {
  const normalizedMessage = normalizeSlackErrorMessage(rawMessage);

  if (!normalizedMessage) {
    return DEFAULT_SLACK_ERROR_MESSAGE;
  }

  return truncateFromEnd(normalizedMessage, MAX_USER_VISIBLE_ERROR_LENGTH);
}

export function resolveSlackErrorMessage(error: unknown): string {
  const rawMessage = extractSlackErrorMessage(error);
  return formatSlackErrorMessage(rawMessage);
}

export async function resolveSlackErrorMessageAsync(error: unknown): Promise<string> {
  const rawMessage = await extractSlackErrorMessageAsync(error);
  return formatSlackErrorMessage(rawMessage);
}

// ---------------------------------------------------------------------------
// Slack text helpers
// ---------------------------------------------------------------------------

export function truncateForSlack(text: string): string {
  if (text.length <= SLACK_MAX_TEXT_LENGTH) {
    return text;
  }
  return `...${text.slice(-(SLACK_MAX_TEXT_LENGTH - 3))}`;
}

export function resolveThreadId(typedMessage: SlackMessagePayload): string {
  return typedMessage.thread_ts || typedMessage.ts;
}

export async function sendUserFacingSlackErrorMessage(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
  errorOrMessage: unknown,
): Promise<void> {
  if (!typedMessage.channel) {
    return;
  }

  const errorMessage = await resolveSlackErrorMessageAsync(errorOrMessage);
  const threadId = resolveThreadId(typedMessage);

  try {
    await typedClient.chat.postMessage({
      channel: typedMessage.channel,
      thread_ts: threadId,
      text: truncateForSlack(errorMessage),
      ...NO_UNFURL_OPTIONS,
    });
  } catch (sendError) {
    console.error("Failed to send Slack user-facing error message:", sendError);
  }
}

export function describeSlackFile(file: SlackFile): string {
  const name = file.name?.trim() || file.id;
  const ext = file.filetype?.trim();
  return ext ? `${name} (${ext})` : name;
}

export interface SkippedAttachments {
  unsupported?: SlackFile[];
  unreadable?: SlackFile[];
  oversized?: SlackFile[];
}

/** What the bot still answers after skipping files: the other attachments, the message text, or nothing. */
export type SkippedAttachmentsFollowUp = "otherAttachments" | "messageOnly" | "none";

/** Plain-language reply for attachments the bot is skipping, or "" when nothing was skipped. */
export function buildSkippedAttachmentsNotice(
  skipped: SkippedAttachments,
  followUp: SkippedAttachmentsFollowUp,
): string {
  const unsupported = skipped.unsupported ?? [];
  const unreadable = skipped.unreadable ?? [];
  const oversized = skipped.oversized ?? [];
  if (unsupported.length + unreadable.length + oversized.length === 0) return "";

  const describe = (files: SlackFile[]): string => files.map(describeSlackFile).join(", ");
  const parts: string[] = [];
  if (unsupported.length > 0) {
    const lead = unsupported.length === 1 ? "I can't read this type of file" : "I can't read these types of file";
    parts.push(`${lead}: ${describe(unsupported)}.`);
  }
  // Only the plain-text and Markdown parsers need UTF-8; CSV and TSV fail here only when binary.
  const unreadableDelimited = unreadable.filter(isDelimitedTextAttachment);
  const unreadableUtf8 = unreadable.filter((file) => !isDelimitedTextAttachment(file));
  if (unreadableUtf8.length > 0) {
    const one = unreadableUtf8.length === 1;
    parts.push(
      `${one ? "This file doesn't" : "These files don't"} contain readable text: ${describe(unreadableUtf8)}. ` +
        `If ${one ? "it is a text file" : "they are text files"}, save ${one ? "it" : "them"} ` +
        `with UTF-8 encoding and send ${one ? "it" : "them"} again.`,
    );
  }
  if (unreadableDelimited.length > 0) {
    const one = unreadableDelimited.length === 1;
    parts.push(
      `${one ? "This file looks" : "These files look"} like binary data, not CSV or TSV text: ` +
        `${describe(unreadableDelimited)}.`,
    );
  }
  if (oversized.length > 0) {
    const lead = oversized.length === 1 ? "This file is" : "These files are";
    parts.push(`${lead} larger than the ${MAX_ATTACHMENT_MB} MB limit: ${describe(oversized)}.`);
  }
  if (followUp === "otherAttachments") {
    parts.push("I'll answer using your other attachments.");
  } else if (followUp === "messageOnly") {
    parts.push("I'll answer your message without them.");
  }
  parts.push(SUPPORTED_ATTACHMENTS_HINT);
  return parts.join(" ");
}

export async function postSkippedAttachmentsNotice(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
  skipped: SkippedAttachments,
  followUp: SkippedAttachmentsFollowUp,
): Promise<void> {
  const notice = buildSkippedAttachmentsNotice(skipped, followUp);
  if (!typedMessage.channel || notice === "") return;
  try {
    await typedClient.chat.postMessage({
      channel: typedMessage.channel,
      thread_ts: resolveThreadId(typedMessage),
      text: truncateForSlack(notice),
      ...NO_UNFURL_OPTIONS,
    });
  } catch (error) {
    console.error("Failed to post unsupported-attachment notice:", error);
  }
}

/** Whether the message has words besides the bot's own @mention. */
export function messageHasQuestionText(text: string | undefined, botUserId?: string): boolean {
  let remaining = text ?? "";
  if (botUserId !== undefined && botUserId !== "") {
    remaining = remaining.split(`<@${botUserId}>`).join(" ");
  }
  return remaining.trim() !== "";
}

export interface IncomingAttachmentsOutcome {
  /** False when the message was attachment-only and nothing was accepted: the notice is the whole reply. */
  shouldAnswer: boolean;
  hasSupported: boolean;
}

/**
 * Decides whether the message gets an answer, for every bot setup (with or
 * without an agent). When no attachment was accepted, it posts the
 * skipped-attachments notice here, since no upload follows. Otherwise the
 * notice waits for the upload, so the user gets one notice that also covers
 * files refused after download (see resolveSkippedAttachmentsAfterUpload).
 */
export async function handleIncomingAttachments(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
  botUserId?: string,
): Promise<IncomingAttachmentsOutcome> {
  const { supported, unsupported, oversized } = classifySlackFiles(typedMessage.files);
  const hasSupported = supported.length > 0;
  const hasText = messageHasQuestionText(typedMessage.text, botUserId);
  if (!hasSupported) {
    await postSkippedAttachmentsNotice(
      typedClient,
      typedMessage,
      { unsupported, oversized },
      hasText ? "messageOnly" : "none",
    );
  }
  const filesPresent = (typedMessage.files?.length ?? 0) > 0;
  return { shouldAnswer: hasSupported || hasText || !filesPresent, hasSupported };
}

/**
 * The one notice for a message whose accepted attachments went through
 * upload: files refused before download plus those refused after it, worded
 * by whether anything was actually uploaded.
 */
export function resolveSkippedAttachmentsAfterUpload(
  classification: SlackFileClassification,
  upload: SlackAttachmentUploadResult,
  hasText: boolean,
): { skipped: SkippedAttachments; followUp: SkippedAttachmentsFollowUp; hasSkipped: boolean } {
  const skipped: SkippedAttachments = {
    unsupported: classification.unsupported,
    unreadable: upload.unreadable,
    oversized: [...classification.oversized, ...upload.oversized],
  };
  const followUp: SkippedAttachmentsFollowUp =
    upload.attachments.length > 0 ? "otherAttachments" : hasText ? "messageOnly" : "none";
  const hasSkipped =
    classification.unsupported.length + classification.oversized.length +
      upload.unreadable.length + upload.oversized.length > 0;
  return { skipped, followUp, hasSkipped };
}

export function truncateForSlackStreamMarkdown(text: string): string {
  if (text.length <= SLACK_STREAM_MARKDOWN_LIMIT) {
    return text;
  }
  return `...${text.slice(-(SLACK_STREAM_MARKDOWN_LIMIT - 3))}`;
}

export function truncateFromEnd(text: string, limit: number): string {
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

export function splitByLengthPreferringNewlines(text: string, limit: number): string[] {
  if (!text) {
    return [];
  }
  if (limit <= 0) {
    return [text];
  }

  const chunks: string[] = [];
  let remaining = text;
  while (remaining.length > limit) {
    const candidate = remaining.slice(0, limit);
    const lastNewlineIndex = candidate.lastIndexOf("\n");
    const splitIndex = lastNewlineIndex > -1 ? lastNewlineIndex + 1 : limit;
    chunks.push(remaining.slice(0, splitIndex));
    remaining = remaining.slice(splitIndex);
  }

  if (remaining.length > 0) {
    chunks.push(remaining);
  }

  return chunks;
}

// ---------------------------------------------------------------------------
// Block normalization
// ---------------------------------------------------------------------------

export function splitSectionTextObjectByLimit(
  textObject: unknown,
  limit: number,
): { chunks: Record<string, unknown>[]; didSplit: boolean; hasTextObject: boolean } {
  if (!textObject || typeof textObject !== "object" || Array.isArray(textObject)) {
    return { chunks: [], didSplit: false, hasTextObject: false };
  }

  const textRecord = textObject as Record<string, unknown>;
  const textValue = textRecord.text;
  if (typeof textValue !== "string") {
    return { chunks: [textRecord], didSplit: false, hasTextObject: true };
  }

  const splitChunks = splitByLengthPreferringNewlines(textValue, limit);
  if (splitChunks.length <= 1) {
    return { chunks: [textRecord], didSplit: false, hasTextObject: true };
  }

  return {
    chunks: splitChunks.map((chunk) => ({
      ...textRecord,
      text: chunk,
    })),
    didSplit: true,
    hasTextObject: true,
  };
}

export function splitSectionFieldsByLimit(fields: unknown): {
  groups: unknown[][];
  didSplit: boolean;
  hasFieldsArray: boolean;
} {
  if (!Array.isArray(fields)) {
    return {
      groups: [],
      didSplit: false,
      hasFieldsArray: false,
    };
  }

  const expandedFields: unknown[] = [];
  let didSplit = false;
  for (const field of fields) {
    if (!field || typeof field !== "object" || Array.isArray(field)) {
      expandedFields.push(field);
      continue;
    }

    const fieldRecord = field as Record<string, unknown>;
    const fieldText = fieldRecord.text;
    if (typeof fieldText !== "string") {
      expandedFields.push(fieldRecord);
      continue;
    }

    const fieldChunks = splitByLengthPreferringNewlines(
      fieldText,
      SLACK_SECTION_FIELD_TEXT_LIMIT,
    );
    if (fieldChunks.length <= 1) {
      expandedFields.push(fieldRecord);
      continue;
    }

    didSplit = true;
    for (const fieldChunk of fieldChunks) {
      expandedFields.push({
        ...fieldRecord,
        text: fieldChunk,
      });
    }
  }

  const groups: unknown[][] = [];
  for (let i = 0; i < expandedFields.length; i += SLACK_SECTION_FIELDS_PER_BLOCK_LIMIT) {
    groups.push(expandedFields.slice(i, i + SLACK_SECTION_FIELDS_PER_BLOCK_LIMIT));
  }

  if (groups.length > 1) {
    didSplit = true;
  }

  return {
    groups,
    didSplit,
    hasFieldsArray: true,
  };
}

export function splitSectionBlockForSlackLimits(block: any): any[] {
  if (!block || typeof block !== "object" || Array.isArray(block) || block.type !== "section") {
    return [block];
  }

  const sectionBlock = block as Record<string, unknown>;
  const textSplit = splitSectionTextObjectByLimit(
    sectionBlock.text,
    SLACK_SECTION_TEXT_LIMIT,
  );
  const fieldsSplit = splitSectionFieldsByLimit(sectionBlock.fields);

  if (!textSplit.didSplit && !fieldsSplit.didSplit) {
    return [block];
  }

  const normalizedBlocks: any[] = [];
  const textChunks = textSplit.hasTextObject ? textSplit.chunks : [];

  if (textChunks.length > 0) {
    for (let chunkIndex = 0; chunkIndex < textChunks.length; chunkIndex += 1) {
      const normalizedChunk: Record<string, unknown> = {
        ...sectionBlock,
        text: textChunks[chunkIndex],
      };

      if (fieldsSplit.hasFieldsArray) {
        if (chunkIndex === 0 && fieldsSplit.groups.length > 0) {
          normalizedChunk.fields = fieldsSplit.groups[0];
        } else {
          delete normalizedChunk.fields;
        }
      }

      if (chunkIndex > 0) {
        delete normalizedChunk.accessory;
        delete normalizedChunk.block_id;
      }

      normalizedBlocks.push(normalizedChunk);
    }

    for (let fieldGroupIndex = 1; fieldGroupIndex < fieldsSplit.groups.length; fieldGroupIndex += 1) {
      const fieldsOnlyChunk: Record<string, unknown> = {
        ...sectionBlock,
        fields: fieldsSplit.groups[fieldGroupIndex],
      };
      delete fieldsOnlyChunk.text;
      delete fieldsOnlyChunk.accessory;
      delete fieldsOnlyChunk.block_id;
      normalizedBlocks.push(fieldsOnlyChunk);
    }

    return normalizedBlocks;
  }

  if (!fieldsSplit.hasFieldsArray || fieldsSplit.groups.length === 0) {
    return [block];
  }

  for (let groupIndex = 0; groupIndex < fieldsSplit.groups.length; groupIndex += 1) {
    const normalizedFieldsChunk: Record<string, unknown> = {
      ...sectionBlock,
      fields: fieldsSplit.groups[groupIndex],
    };
    if (groupIndex > 0) {
      delete normalizedFieldsChunk.accessory;
      delete normalizedFieldsChunk.block_id;
    }
    normalizedBlocks.push(normalizedFieldsChunk);
  }

  return normalizedBlocks;
}

export function normalizeSlackBlocksForLimits(blocks: any[]): any[] {
  const normalizedBlocks: any[] = [];
  for (const block of blocks) {
    normalizedBlocks.push(...splitSectionBlockForSlackLimits(block));
  }
  return normalizedBlocks;
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

export function isMarkdownTableRow(line: string): boolean {
  return /^\s*\|.*\|\s*$/.test(line);
}

export function isMarkdownTableSeparatorRow(line: string): boolean {
  return /^\s*\|[\s:]*-{3,}[\s:]*(\|[\s:]*-{3,}[\s:]*)*\|\s*$/.test(line);
}

export function parseMarkdownTableCells(row: string): string[] {
  return row
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

export function getFenceMarker(line: string): FenceMarker | null {
  if (/^\s*`{3,}/.test(line)) {
    return "`";
  }
  if (/^\s*~{3,}/.test(line)) {
    return "~";
  }
  return null;
}

export function isFenceClosingLine(line: string, marker: FenceMarker): boolean {
  if (marker === "`") {
    return /^\s*`{3,}/.test(line);
  }
  return /^\s*~{3,}/.test(line);
}

export function tryParseMarkdownTableAtLine(
  lines: string[],
  startLineIndex: number,
): ParsedMarkdownTable | null {
  const headerLine = lines[startLineIndex];
  const separatorLine = lines[startLineIndex + 1];
  const firstDataLine = lines[startLineIndex + 2];

  if (!headerLine || !separatorLine || !firstDataLine) {
    return null;
  }

  if (
    !isMarkdownTableRow(headerLine) ||
    !isMarkdownTableSeparatorRow(separatorLine) ||
    !isMarkdownTableRow(firstDataLine)
  ) {
    return null;
  }

  const header = parseMarkdownTableCells(headerLine);
  const rows: string[][] = [];
  let rowIndex = startLineIndex + 2;

  while (rowIndex < lines.length) {
    const rowLine = lines[rowIndex];
    if (!rowLine || !isMarkdownTableRow(rowLine)) {
      break;
    }
    rows.push(parseMarkdownTableCells(rowLine));
    rowIndex += 1;
  }

  if (rows.length === 0) {
    return null;
  }

  return {
    header,
    rows,
    nextLineIndex: rowIndex,
  };
}

export function hasMarkdownTableStartOutsideCodeFences(content: string): boolean {
  if (!content) {
    return false;
  }

  const normalizedContent = content.replace(/\r\n/g, "\n").replace(/\\n/g, "\n");

  const lines = normalizedContent.split("\n");
  let activeFenceMarker: FenceMarker | null = null;

  for (let lineIndex = 0; lineIndex < lines.length - 1; lineIndex += 1) {
    const line = lines[lineIndex] ?? "";

    if (activeFenceMarker) {
      if (isFenceClosingLine(line, activeFenceMarker)) {
        activeFenceMarker = null;
      }
      continue;
    }

    const openingFenceMarker = getFenceMarker(line);
    if (openingFenceMarker) {
      activeFenceMarker = openingFenceMarker;
      continue;
    }

    const nextLine = lines[lineIndex + 1] ?? "";
    if (isMarkdownTableRow(line) && isMarkdownTableSeparatorRow(nextLine)) {
      return true;
    }
  }

  return false;
}

export function splitMarkdownMessageIntoTableAwareSegments(content: string): MarkdownTableSegment[] {
  if (!content) {
    return [];
  }

  const normalizedContent = content.replace(/\r\n/g, "\n");
  const lines = normalizedContent.split("\n");
  const segments: MarkdownTableSegment[] = [];
  const markdownBuffer: string[] = [];
  let activeFenceMarker: FenceMarker | null = null;

  const flushMarkdownBuffer = () => {
    if (markdownBuffer.length === 0) {
      return;
    }
    const markdownContent = markdownBuffer.join("\n");
    markdownBuffer.length = 0;
    if (markdownContent.length === 0) {
      return;
    }
    segments.push({
      type: "markdown",
      content: markdownContent,
    });
  };

  for (let lineIndex = 0; lineIndex < lines.length;) {
    const line = lines[lineIndex] ?? "";

    if (activeFenceMarker) {
      markdownBuffer.push(line);
      if (isFenceClosingLine(line, activeFenceMarker)) {
        activeFenceMarker = null;
      }
      lineIndex += 1;
      continue;
    }

    const openingFenceMarker = getFenceMarker(line);
    if (openingFenceMarker) {
      activeFenceMarker = openingFenceMarker;
      markdownBuffer.push(line);
      lineIndex += 1;
      continue;
    }

    const parsedTable = tryParseMarkdownTableAtLine(lines, lineIndex);
    if (parsedTable) {
      flushMarkdownBuffer();
      segments.push({
        type: "table",
        header: parsedTable.header,
        rows: parsedTable.rows,
      });
      lineIndex = parsedTable.nextLineIndex;
      continue;
    }

    markdownBuffer.push(line);
    lineIndex += 1;
  }

  flushMarkdownBuffer();
  return segments;
}

export function buildSlackRichTextTextElement(text: string, makeBold: boolean): Record<string, unknown> {
  const stripped = markdownToText(text);
  text = stripped.length > 0 ? stripped : text;
  const element: Record<string, unknown> = {
    type: "text",
    text,
  };
  if (makeBold) {
    element.style = {
      bold: true,
    };
  }
  return element;
}

export function buildSlackRichTextLinkElement(
  url: string,
  label: string | undefined,
  makeBold: boolean,
): Record<string, unknown> {
  const element: Record<string, unknown> = {
    type: "link",
    url,
  };
  if (label && label !== url) {
    element.text = label;
  }
  if (makeBold) {
    element.style = {
      bold: true,
    };
  }
  return element;
}

export function splitTrailingPunctuationFromUrl(value: string): { url: string; trailingText: string } {
  let url = value;
  let trailingText = "";

  while (url.length > 0 && /[),.;!?]$/.test(url)) {
    trailingText = `${url.slice(-1)}${trailingText}`;
    url = url.slice(0, -1);
  }

  return {
    url,
    trailingText,
  };
}

export function appendTextWithClickableUrls(
  text: string,
  elements: Record<string, unknown>[],
  makeBold: boolean,
): void {
  if (!text) {
    return;
  }

  const bareUrlRegex = /https?:\/\/[^\s<>()]+/g;
  let cursor = 0;
  bareUrlRegex.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = bareUrlRegex.exec(text)) !== null) {
    const matchedUrl = match[0] ?? "";
    if (!matchedUrl) {
      continue;
    }

    if (match.index > cursor) {
      const plainTextChunk = text.slice(cursor, match.index);
      if (plainTextChunk.length > 0) {
        elements.push(buildSlackRichTextTextElement(plainTextChunk, makeBold));
      }
    }

    const { url, trailingText } = splitTrailingPunctuationFromUrl(matchedUrl);
    if (url.length > 0) {
      elements.push(buildSlackRichTextLinkElement(url, undefined, makeBold));
    } else {
      elements.push(buildSlackRichTextTextElement(matchedUrl, makeBold));
    }

    if (trailingText.length > 0) {
      elements.push(buildSlackRichTextTextElement(trailingText, makeBold));
    }

    cursor = match.index + matchedUrl.length;
  }

  if (cursor < text.length) {
    const remainingText = text.slice(cursor);
    if (remainingText.length > 0) {
      elements.push(buildSlackRichTextTextElement(remainingText, makeBold));
    }
  }
}

export function buildSlackTableCellElements(
  cellText: string,
  isHeaderCell: boolean,
): Record<string, unknown>[] {
  const elements: Record<string, unknown>[] = [];
  const markdownLinkRegex = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let cursor = 0;
  markdownLinkRegex.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = markdownLinkRegex.exec(cellText)) !== null) {
    const fullMatch = match[0] ?? "";
    if (!fullMatch) {
      continue;
    }

    const label = match[1] ?? "";
    const rawUrl = match[2] ?? "";
    const leadingText = cellText.slice(cursor, match.index);
    appendTextWithClickableUrls(leadingText, elements, isHeaderCell);

    const { url, trailingText } = splitTrailingPunctuationFromUrl(rawUrl);
    if (url.length > 0) {
      elements.push(buildSlackRichTextLinkElement(url, label, isHeaderCell));
    } else {
      appendTextWithClickableUrls(fullMatch, elements, isHeaderCell);
    }

    if (trailingText.length > 0) {
      elements.push(buildSlackRichTextTextElement(trailingText, isHeaderCell));
    }

    cursor = match.index + fullMatch.length;
  }

  const trailingText = cellText.slice(cursor);
  appendTextWithClickableUrls(trailingText, elements, isHeaderCell);

  if (elements.length === 0) {
    elements.push(buildSlackRichTextTextElement(" ", isHeaderCell));
  }

  return elements;
}

export function buildSlackTableCell(cellText: string, isHeaderCell: boolean): Record<string, unknown> {
  return {
    type: "rich_text",
    elements: [
      {
        type: "rich_text_section",
        elements: buildSlackTableCellElements(cellText, isHeaderCell),
      },
    ],
  };
}

export function buildSlackTableBlock(rows: string[][]): Record<string, unknown> {
  const columnCount = Math.min(
    rows.reduce((max, row) => Math.max(max, row.length), 0),
    MAX_TABLE_COLS,
  );
  const normalizedRows = rows.map((row, rowIndex) =>
    Array.from({ length: columnCount }, (_, colIndex) =>
      buildSlackTableCell(row[colIndex] ?? "", rowIndex === 0),
    ),
  );
  return { type: "table", rows: normalizedRows };
}

export function buildSlackTableBlocksFromMarkdownSegment(
  segment: Extract<MarkdownTableSegment, { type: "table" }>,
): Record<string, unknown>[] {
  const { header, rows: dataRows } = segment;
  const maxDataRows = MAX_TABLE_ROWS - 1; // reserve 1 slot for header
  const headerCharCount = header.reduce((sum, cell) => sum + cell.length, 0);

  if (dataRows.length === 0) {
    return [buildSlackTableBlock([header])];
  }

  const blocks: Record<string, unknown>[] = [];
  let currentRows: string[][] = [];
  let currentCharCount = headerCharCount;

  for (const row of dataRows) {
    const rowChars = row.reduce((sum, cell) => sum + cell.length, 0);
    const wouldExceedChars = currentCharCount + rowChars > MAX_TABLE_CHARS;
    const wouldExceedRows = currentRows.length >= maxDataRows;

    if (currentRows.length > 0 && (wouldExceedChars || wouldExceedRows)) {
      blocks.push(buildSlackTableBlock([header, ...currentRows]));
      currentRows = [];
      currentCharCount = headerCharCount;
    }

    currentRows.push(row);
    currentCharCount += rowChars;
  }

  if (currentRows.length > 0) {
    blocks.push(buildSlackTableBlock([header, ...currentRows]));
  }

  return blocks;
}

/**
 * Returns the approximate character count of a block that counts toward Slack's
 * cumulative blocks payload limit (used for chunking).
 */
export function getBlockPayloadTextSize(block: any): number {
  if (!block || typeof block !== "object" || Array.isArray(block)) {
    return 0;
  }
  const type = block.type;
  if (type === "section") {
    let size = 0;
    if (block.text && typeof block.text.text === "string") {
      size += block.text.text.length;
    }
    const fields = block.fields;
    if (Array.isArray(fields)) {
      for (const f of fields) {
        if (f && typeof f.text === "string") size += f.text.length;
      }
    }
    return size;
  }
  if (type === "rich_text" && Array.isArray(block.elements)) {
    let size = 0;
    for (const el of block.elements) {
      if (el && Array.isArray(el.elements)) {
        for (const sub of el.elements) {
          if (sub && typeof sub.text === "string") size += sub.text.length;
          if (sub && typeof sub.url === "string") size += sub.url.length;
        }
      }
    }
    return size;
  }
  if (type === "table" && Array.isArray(block.rows)) {
    let size = 0;
    for (const row of block.rows) {
      if (!Array.isArray(row)) continue;
      for (const cell of row) {
        if (cell && Array.isArray(cell.elements)) {
          for (const el of cell.elements) {
            if (el && Array.isArray(el.elements)) {
              for (const sub of el.elements) {
                if (sub && typeof sub.text === "string") size += sub.text.length;
                if (sub && typeof sub.url === "string") size += sub.url.length;
              }
            }
          }
        }
      }
    }
    return size;
  }
  return JSON.stringify(block).length;
}

export function splitSlackBlocksByLimit(
  blocks: any[],
  maxBlocksPerMessage: number = SLACK_BLOCKS_PER_MESSAGE_LIMIT,
  maxTotalTextPerMessage: number = SLACK_BLOCKS_TOTAL_TEXT_LIMIT,
): any[][] {
  if (blocks.length === 0) {
    return [];
  }
  const result: any[][] = [];
  let currentChunk: any[] = [];
  let currentSize = 0;
  let currentChunkHasTable = false;
  for (const block of blocks) {
    const blockSize = getBlockPayloadTextSize(block);
    const isTable = block.type === "table";
    const wouldExceedCount = currentChunk.length >= maxBlocksPerMessage;
    const wouldExceedSize = currentSize + blockSize > maxTotalTextPerMessage;
    const wouldExceedTableLimit = isTable && currentChunkHasTable;
    if (currentChunk.length > 0 && (wouldExceedCount || wouldExceedSize || wouldExceedTableLimit)) {
      result.push(currentChunk);
      currentChunk = [];
      currentSize = 0;
      currentChunkHasTable = false;
    }
    currentChunk.push(block);
    currentSize += blockSize;
    if (isTable) currentChunkHasTable = true;
  }
  if (currentChunk.length > 0) {
    result.push(currentChunk);
  }
  return result;
}

export async function buildFinalSlackChunks(
  answerBody: string,
): Promise<any[][]> {
  const tableAwareSegments = splitMarkdownMessageIntoTableAwareSegments(answerBody || "");
  const combinedBlocks: any[] = [];

  for (const segment of tableAwareSegments) {
    if (segment.type === "markdown") {
      if (segment.content.trim().length === 0) {
        continue;
      }
      const slackMrkdwn = markdownToSlackMrkdwn(segment.content);
      const markdownBlocks = [{
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": slackMrkdwn,
        },
      }];
      const normalizedMarkdownBlocks = normalizeSlackBlocksForLimits(markdownBlocks);
      combinedBlocks.push(...normalizedMarkdownBlocks);
      continue;
    }

    combinedBlocks.push(...buildSlackTableBlocksFromMarkdownSegment(segment));
  }

  return splitSlackBlocksByLimit(combinedBlocks);
}

// ---------------------------------------------------------------------------
// User / thread helpers
// ---------------------------------------------------------------------------

export function isThreadFollowUpMessage(message: SlackMessagePayload): boolean {
  return Boolean(message.thread_ts && message.thread_ts !== message.ts);
}

export function sanitizeSlackLabelValue(value?: string): string {
  if (!value) {
    return "";
  }
  return value.replace(/\s+/g, " ").trim();
}

/** First non-empty sanitized display label from Slack user profile (shared candidate order). */
export function pickSlackDisplayName(userRecord: SlackUserRecord | undefined): string {
  const displayNameCandidates = [
    userRecord?.profile?.display_name,
    userRecord?.real_name,
    userRecord?.profile?.real_name,
    userRecord?.name,
  ];
  return (
    displayNameCandidates
      .map((nameCandidate) => sanitizeSlackLabelValue(nameCandidate))
      .find((nameCandidate) => Boolean(nameCandidate)) || ""
  );
}

export function formatMentionedUser(
  userRecord: SlackUserRecord | undefined,
  userId: string,
): string {
  const email = sanitizeSlackLabelValue(userRecord?.profile?.email);

  if (!email) {
    return "";
  }

  const displayName = pickSlackDisplayName(userRecord) || "User";

  return `${displayName} (Email: ${email}, Slack user id: ${userId})`;
}

export function formatSlackUserLabel(userRecord: SlackUserRecord | undefined, userId: string): string {
  const email = sanitizeSlackLabelValue(userRecord?.profile?.email);
  const displayName = pickSlackDisplayName(userRecord);

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

/** Display name of the Slack user sending the message (for AI "current user" context). */
export function slackCallerDisplayName(userRecord: SlackUserRecord | undefined): string {
  return pickSlackDisplayName(userRecord);
}

export async function resolveMentionsInText(
  text: string | undefined,
  typedClient: TypedSlackClient,
): Promise<string> {
  if (!text) {
    return "";
  }

  const mentionRegex = /<@([A-Z0-9]+)>/g;
  const userIds = new Set<string>();
  let match: RegExpExecArray | null;

  while ((match = mentionRegex.exec(text)) !== null) {
    const userId = match[1];
    if (userId) {
      userIds.add(userId);
    }
  }

  if (userIds.size === 0) {
    return text.replace(/\s+/g, " ").trim();
  }

  const replacements = new Map<string, string>();

  for (const userId of userIds) {
    const mention = `<@${userId}>`;

    const cachedUserRecord = getCachedUserInfo(userId);

    if (cachedUserRecord !== null) {
      const formattedUser = formatMentionedUser(cachedUserRecord, userId);
      replacements.set(mention, formattedUser);
      continue;
    }

    try {
      const userInfoResult = await typedClient.users.info({ user: userId });
      const userRecord = userInfoResult.user;
      setCachedUserInfo(userId, userRecord);
      const formattedUser = formatMentionedUser(userRecord, userId);
      replacements.set(mention, formattedUser);
    } catch (error) {
      console.error(`Failed to resolve Slack user mention for ${userId}:`, error);
      replacements.set(mention, mention);
    }
  }

  let result = text;
  for (const [mention, replacement] of replacements) {
    result = result.replace(new RegExp(mention.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"), replacement);
  }

  return result.replace(/\s+/g, " ").trim();
}

export function inferThreadMessageSpeaker(
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

export async function resolveThreadUserLabels(
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

export async function fetchPriorThreadMessages(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
): Promise<SlackMessagePayload[]> {
  if (!typedMessage.channel || !typedMessage.thread_ts) {
    return [];
  }

  const SLACK_MAX_REPLIES_LIMIT = 101;

  const call1Raw = await typedClient.apiCall("conversations.replies", {
    channel: typedMessage.channel,
    ts: typedMessage.thread_ts,
    limit: SLACK_MAX_REPLIES_LIMIT,
  });
  const call1 = call1Raw as SlackConversationsRepliesResponse;
  let firstBatch: SlackMessagePayload[] = Array.isArray(call1.messages) ? call1.messages : [];

  firstBatch = firstBatch.slice(0, -1);
  return firstBatch;

}

// ---------------------------------------------------------------------------
// URL / link helpers
// ---------------------------------------------------------------------------

export function getFrontendBaseUrl(): string {
  return (process.env.FRONTEND_PUBLIC_URL || "http://localhost:3000").replace(
    /\/$/,
    "",
  );
}

export function buildFrontendRecordUrl(recordId: string): string {
  return `${getFrontendBaseUrl()}/record/${encodeURIComponent(recordId)}`;
}

/**
 * Prefer the frontend record page (same as Sources / frontend Artifacts panel).
 * Fall back to an absolute download URL when no recordId is available.
 */
export function resolveSlackArtifactLink(artifact: {
  recordId?: string;
  downloadUrl?: string;
}): string | null {
  const recordId = artifact.recordId?.trim();
  if (recordId) {
    return buildFrontendRecordUrl(recordId);
  }
  const downloadUrl = artifact.downloadUrl?.trim() || "";
  if (!downloadUrl || downloadUrl.startsWith("record:")) {
    return null;
  }
  if (/^https?:\/\//i.test(downloadUrl)) {
    return downloadUrl;
  }
  if (downloadUrl.startsWith("/")) {
    return `${getFrontendBaseUrl()}${downloadUrl}`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Stream URL
// ---------------------------------------------------------------------------

export function buildChatStreamUrl(
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

// ---------------------------------------------------------------------------
// Bot resolution
// ---------------------------------------------------------------------------

/**
 * The resolver is passed in rather than imported: the EE receiver stores the
 * matched bot in the EE registry's own AsyncLocalStorage, so reading the OSS
 * store from an EE handler would always come back empty.
 */
export async function resolveSlackBotForEvent<T extends SlackBotConfig>(
  getMatchedBot: () => T | null,
): Promise<T | null> {
  return getMatchedBot();
}

// ---------------------------------------------------------------------------
// Text fixups
// ---------------------------------------------------------------------------

export function removeContinuousDuplicateMarkdownLinks(text: string): string {
  const linkPattern = /(\[[^\]]+\]\([^)]+\))(?:\s*\1)+/g;

  return text.replace(linkPattern, "$1" + " ");
}

export function addSpaceBetweenMarkdownLinks(input: string): string {
  const pattern = /(\[[^\]]+\]\([^)]+\))(?=\[[^\]]+\]\([^)]+\))/g;

  return input.replace(pattern, "$1 ");
}

// ---------------------------------------------------------------------------
// Message filtering
// ---------------------------------------------------------------------------

export function isIgnoredSlackMessage(
  typedMessage: SlackMessagePayload,
  typedContext: { botUserId?: string },
): boolean {
  return (
    typedMessage.subtype === "bot_message" ||
    Boolean(typedMessage.bot_id) ||
    typedMessage.user === typedContext.botUserId
  );
}
