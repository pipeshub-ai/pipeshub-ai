import { config } from "dotenv";
config(); // Load environment variables first

import { json, urlencoded, Request, Response } from "express";
import { connect } from "./utils/db";
import { getFromDatabase, saveToDatabase } from "./utils/conversation";

import axios from "axios";
import { markdownToBlocks } from "@tryfabric/mack";
import app from "./slackApp";
import receiver from "./receiver";
import { ConfigService } from "../../../modules/tokens_manager/services/cm.service";
import { slackJwtGenerator } from "../../../libs/utils/createJwt";
import { markdownToSlackMrkdwn, markdownToText } from "./utils/md_to_mrkdwn";

import {
  type SlackBotConfig,
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
const MAX_SLACK_ERROR_BODY_LENGTH = 64000;
const SLACK_BLOCKS_PER_MESSAGE_LIMIT = 50;
const SLACK_SECTION_TEXT_LIMIT = 3000;
const SLACK_SECTION_FIELD_TEXT_LIMIT = 2000;
const SLACK_SECTION_FIELDS_PER_BLOCK_LIMIT = 10;
const DEFAULT_SLACK_ERROR_MESSAGE = "Something went wrong! Please try again later.";
const MAX_USER_VISIBLE_ERROR_LENGTH = 320;
const SLACK_MENTION_REGEX = /<@[A-Z0-9]+>/g;
const STREAM_FAILURE_MESSAGE =
  "I ran into an issue while streaming the response. Please try again.";
const TABLE_STREAMING_PAUSED_HINT =
  "\n\n:hourglass_flowing_sand:";





interface StreamStartResult {
  ts?: string;
}

type SlackBlock = Record<string, unknown>;

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
      blocks?: SlackBlock[];
    }) => Promise<{ ts?: string }>;
    update: (params: {
      channel: string;
      ts: string;
      text: string;
      blocks?: SlackBlock[];
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

function isReadableStreamLike(value: unknown): value is NodeJS.ReadableStream {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return typeof candidate.on === "function";
}

function readMessageFromTextPayload(payload: string): string | null {
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

async function readStreamToText(stream: NodeJS.ReadableStream): Promise<string> {
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

async function readMessageFromAxiosResponseData(data: unknown): Promise<string | null> {
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

async function extractSlackErrorMessageAsync(error: unknown): Promise<string> {
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

function formatSlackErrorMessage(rawMessage: string): string {
  const normalizedMessage = normalizeSlackErrorMessage(rawMessage);

  if (!normalizedMessage) {
    return DEFAULT_SLACK_ERROR_MESSAGE;
  }




  return truncateFromEnd(normalizedMessage, MAX_USER_VISIBLE_ERROR_LENGTH);
}

function normalizeSlackErrorMessage(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}





function resolveSlackErrorMessage(error: unknown): string {
  console.log("Resolving Slack error message", error);
  const rawMessage = extractSlackErrorMessage(error);
  return formatSlackErrorMessage(rawMessage);
}

async function resolveSlackErrorMessageAsync(error: unknown): Promise<string> {
  console.log("Resolving Slack error message", error);
  const rawMessage = await extractSlackErrorMessageAsync(error);
  return formatSlackErrorMessage(rawMessage);
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

  const errorMessage = await resolveSlackErrorMessageAsync(errorOrMessage);
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

function splitByLengthPreferringNewlines(text: string, limit: number): string[] {
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

function splitSectionTextObjectByLimit(
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

function splitSectionFieldsByLimit(fields: unknown): {
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

function splitSectionBlockForSlackLimits(block: any): any[] {
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

function normalizeSlackBlocksForLimits(blocks: any[]): any[] {
  const normalizedBlocks: any[] = [];
  for (const block of blocks) {
    normalizedBlocks.push(...splitSectionBlockForSlackLimits(block));
  }
  return normalizedBlocks;
}

type MarkdownTableSegment =
  | { type: "markdown"; content: string }
  | { type: "table"; header: string[]; rows: string[][] };

type FenceMarker = "`" | "~";

interface ParsedMarkdownTable {
  header: string[];
  rows: string[][];
  nextLineIndex: number;
}

function isMarkdownTableRow(line: string): boolean {
  return /^\s*\|.*\|\s*$/.test(line);
}

function isMarkdownTableSeparatorRow(line: string): boolean {
  return /^\s*\|[\s:]*-{3,}[\s:]*(\|[\s:]*-{3,}[\s:]*)*\|\s*$/.test(line);
}

function parseMarkdownTableCells(row: string): string[] {
  return row
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function getFenceMarker(line: string): FenceMarker | null {
  if (/^\s*`{3,}/.test(line)) {
    return "`";
  }
  if (/^\s*~{3,}/.test(line)) {
    return "~";
  }
  return null;
}

function isFenceClosingLine(line: string, marker: FenceMarker): boolean {
  if (marker === "`") {
    return /^\s*`{3,}/.test(line);
  }
  return /^\s*~{3,}/.test(line);
}

function tryParseMarkdownTableAtLine(
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

function hasMarkdownTableStartOutsideCodeFences(content: string): boolean {
  if (!content) {
    return false;
  }

  const normalizedContent = content.replace(/\r\n/g, "\n");
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

function splitMarkdownMessageIntoTableAwareSegments(content: string): MarkdownTableSegment[] {
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

function buildSlackRichTextTextElement(text: string, makeBold: boolean): Record<string, unknown> {
  text = markdownToText(text);
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

function buildSlackRichTextLinkElement(
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

function splitTrailingPunctuationFromUrl(value: string): { url: string; trailingText: string } {
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

function appendTextWithClickableUrls(
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

function buildSlackTableCellElements(
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
    elements.push(buildSlackRichTextTextElement("", isHeaderCell));
  }

  return elements;
}

function buildSlackTableCell(cellText: string, isHeaderCell: boolean): Record<string, unknown> {
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

function buildSlackTableBlockFromMarkdownSegment(
  segment: Extract<MarkdownTableSegment, { type: "table" }>,
): Record<string, unknown> {
  const allRows = [segment.header, ...segment.rows];
  const columnCount = allRows.reduce((max, row) => Math.max(max, row.length), 0);
  const normalizedRows = allRows.map((row, rowIndex) =>
    Array.from({ length: columnCount }, (_, columnIndex) =>
      buildSlackTableCell(row[columnIndex] ?? "", rowIndex === 0),
    ),
  );

  return {
    type: "table",
    rows: normalizedRows,
  };
}


function splitSlackBlocksByLimit(
  blocks: any[],
  maxBlocksPerMessage: number = SLACK_BLOCKS_PER_MESSAGE_LIMIT
): any[][] {
  if (blocks.length === 0) {
    return [];
  }
  const result = [];
  for (let i = 0; i < blocks.length; i += maxBlocksPerMessage) {
    result.push(blocks.slice(i, i + maxBlocksPerMessage));
  }
  return result;
}

async function buildFinalSlackChunks(
  answerBody: string,
): Promise<any[][]> {  

  try {
    const tableAwareSegments = splitMarkdownMessageIntoTableAwareSegments(answerBody || "");
    const combinedBlocks: any[] = [];

    for (const segment of tableAwareSegments) {
      if (segment.type === "markdown") {
        if (segment.content.trim().length === 0) {
          continue;
        }
        const markdownBlocks = await markdownToBlocks(segment.content);
        const normalizedMarkdownBlocks = normalizeSlackBlocksForLimits(markdownBlocks);
        combinedBlocks.push(...normalizedMarkdownBlocks);
        continue;
      }

      combinedBlocks.push(buildSlackTableBlockFromMarkdownSegment(segment));
    }

    const blockChunks = splitSlackBlocksByLimit(combinedBlocks);

    return blockChunks;
  } catch (error) {
    throw error;
  }
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

function buildCitationSources(citations?: CitationData[]): any[]  {

  let blocks: any[] = [];
  let elements: any[] = [];
  for (const citation of citations || []) {
    const webUrl = getCitationWebUrl(citation.citationData.metadata.webUrl);
    if (!webUrl) {
      continue;
    }

    const chunkIndex = citation.citationData.chunkIndex;
    if (chunkIndex) {

      elements.push({
        "type": "link",
        "url": webUrl,
        "text": ` [${chunkIndex}]`,
      });
    }

    if (elements.length == 10) {
      blocks.push({
        "type": "rich_text",
        "elements": [
          {
            "type": "rich_text_section",
            "elements": [
              ...elements,
            ]
          }
        ]
      });
      elements = [];
    }
  }

  if (elements.length > 0) {
    blocks.push({
      "type": "rich_text",
      "elements": [
        {
          "type": "rich_text_section",
          "elements": [
            ...elements,
          ]
        }
      ]
    });
  }

  if (blocks.length > 0) {
    blocks = [ {
      type: "section",
      text: {
        type: "mrkdwn",
        text: "*Sources:*",
      },
    }, ...blocks];
  }
  return blocks;
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
): Promise<SlackBotConfig | null> {
  const matchedFromRequestContext = getCurrentMatchedSlackBot();
  if (matchedFromRequestContext) {
    return matchedFromRequestContext;
  }

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

  const sendOrUpdateNonStreamMessage = async (
    text: string,
    blocks?: any[],
  ): Promise<void> => {
    
    const truncatedText = truncateForSlack(text);
    if (!text && (!blocks || blocks.length === 0)) {
      return;
    }
    if (waitingMessageTs) {
      try {
        await typedClient.chat.update({
          channel: typedMessage.channel!,
          ts: waitingMessageTs,
          text: truncatedText,
          ...(blocks && blocks.length > 0 ? { blocks } : {}),
        });
        return;
      } catch (error) {
        console.error("Error updating Slack waiting message:", error);
        if (blocks && blocks.length > 0) {
          throw error;
        } 
        else {
          try {
            await typedClient.chat.update({
              channel: typedMessage.channel!,
              ts: waitingMessageTs,
              text: truncatedText,
            });
            return;
          } catch (fallbackError) {
            console.error(
              "Error updating Slack waiting message with text fallback:",
              fallbackError,
            );
            throw fallbackError;
          }
        }
      }
    }

    try {
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: truncatedText,
        ...(blocks && blocks.length > 0 ? { blocks } : {}),
      });
    } catch (error) {
      if (blocks && blocks.length > 0) {
        throw error;
      }
      console.error("Error posting Slack non-stream blocks message:", error);
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: truncatedText,
      });
    }
  };

  const postThreadChunkMessage = async (chunk: any[]): Promise<void> => {
    try {
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: "",
        blocks: chunk,
      });
    } catch (error) {
      console.error("Error posting Slack chunk as blocks, retrying with text:", error);
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: "Something went wrong while generating the response. Please try again later.",
      });
    }
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
    let tableStreamingDisabled = false;
    let streamTableProbeText = "";
    let tablePauseHintSent = false;

    const pushTextToSlackStream = async (text: string): Promise<void> => {
      if (text.length === 0) {
        return;
      }

      const renderedDeltaText = markdownToSlackMrkdwn(text, {
        preserveTrailingWhitespace: true,
      });
      if (renderedDeltaText.length === 0) {
        return;
      }

      const markdownChunks = splitByLengthPreferringNewlines(
        renderedDeltaText,
        SLACK_STREAM_MARKDOWN_LIMIT,
      );
      if (markdownChunks.length === 0) {
        return;
      }

      let chunksToAppend = markdownChunks;
      if (!streamTs) {
        const [firstChunk, ...restChunks] = markdownChunks;
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

    const sendTableStreamingPausedHint = async (): Promise<void> => {
      if (tablePauseHintSent) {
        return;
      }
      tablePauseHintSent = true;

      if (streamTs) {
        const renderedHint = markdownToSlackMrkdwn(TABLE_STREAMING_PAUSED_HINT, {
          preserveTrailingWhitespace: true,
        });
        if (!renderedHint) {
          return;
        }

        try {
          await typedClient.apiCall("chat.appendStream", {
            channel: typedMessage.channel!,
            ts: streamTs,
            markdown_text: truncateForSlackStreamMarkdown(renderedHint),
          });
        } catch (error) {
          console.error("Error appending Slack table formatting hint:", error);
        }
        return;
      }

      if (!waitingMessageTs) {
        return;
      }

      try {
        await typedClient.chat.update({
          channel: typedMessage.channel!,
          ts: waitingMessageTs,
          text: truncateForSlack(TABLE_STREAMING_PAUSED_HINT),
        });
      } catch (error) {
        console.error("Error updating Slack waiting message with table hint:", error);
      }
    };

    await new Promise<void>((resolve, reject) => {
      responseStream.setEncoding("utf8");
      let settled = false;

      const cleanupListeners = (): void => {
        responseStream.removeListener("data", onData);
        responseStream.removeListener("end", onEnd);
        responseStream.removeListener("error", onError);
      };

      const resolveOnce = (): void => {
        if (settled) {
          return;
        }
        settled = true;
        cleanupListeners();
        resolve();
      };

      const rejectOnce = (error: unknown): void => {
        if (settled) {
          return;
        }
        settled = true;
        cleanupListeners();
        reject(error);
      };

      const onData = (chunk: string): void => {
        sseBuffer += chunk;
        const { events, remainder } = parseSSEEvents(sseBuffer);
        sseBuffer = remainder;

        for (const evt of events) {
          if (evt.event === "answer_chunk" || evt.event === "chunk") {
            const nextChunk = extractStreamChunk(evt.data);
            if (nextChunk.length === 0) {
              continue;
            }

            if (tableStreamingDisabled) {
              continue;
            }

            streamTableProbeText += nextChunk;
            if (hasMarkdownTableStartOutsideCodeFences(streamTableProbeText)) {
              tableStreamingDisabled = true;
              pendingAppendText = "";
              queuedStreamAppend = queuedStreamAppend
                .then(async () => sendTableStreamingPausedHint())
                .catch((error) => {
                  console.error("Error sending Slack table formatting hint:", error);
                });
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
            resolveOnce();
            return;
          }
        }
      };

      const onEnd = (): void => {
        resolveOnce();
      };

      const onError = (error: unknown): void => {
        rejectOnce(error);
      };

      responseStream.on("data", onData);
      responseStream.on("end", onEnd);
      responseStream.on("error", onError);
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

    if (!streamTs && !tableStreamingDisabled && botResponse.content) {
      await pushTextToSlackStream(botResponse.content);
    }
    

    const citationBlocks = buildCitationSources(botResponse.citations);
    const citationBlockChunks = splitSlackBlocksByLimit(citationBlocks);
    const answerBody = botResponse.content || "" ;
    const finalChunks = await buildFinalSlackChunks(answerBody);
    
    
    const [firstFinalChunk, ...remainingFinalChunks] = finalChunks;
    
    if (streamTs) {
      await stopSlackStream();
      if (firstFinalChunk) {
        try {
          await typedClient.chat.update({
            channel: typedMessage.channel!,
            ts: streamTs,
            text: "",
            blocks: firstFinalChunk,
          });
          for (const remainingChunk of remainingFinalChunks) {
            await postThreadChunkMessage(remainingChunk);
          }
          for (const citationChunk of citationBlockChunks) {
            await postThreadChunkMessage(citationChunk);
          }
        } catch (error) {
          const fallbackErrorMessage =
            "Something went wrong while generating the response. Please try again later.";
          console.error(
            "Error updating final streamed Slack message with blocks, trying delete and repost:",
            error,
          );
          let replacedMessageSuccessfully = false;
          try {
            await typedClient.apiCall("chat.delete", {
              channel: typedMessage.channel!,
              ts: streamTs,
            });
            await typedClient.chat.postMessage({
              channel: typedMessage.channel!,
              thread_ts: threadId,
              text: "",
              blocks: firstFinalChunk,
            });

            for (const remainingChunk of remainingFinalChunks) {
              await postThreadChunkMessage(remainingChunk);
            }
            for (const citationChunk of citationBlockChunks) {
              await postThreadChunkMessage(citationChunk);
            }
            replacedMessageSuccessfully = true;

          } catch (replacementError) {
            console.error(
              "Error replacing failed streamed Slack message, sending fallback error message:",
              replacementError,
            );
          }

          if (!replacedMessageSuccessfully) {
            await sendOrUpdateNonStreamMessage(fallbackErrorMessage);
          }
        }
      }
      
    } else {
      if (firstFinalChunk) {
        await sendOrUpdateNonStreamMessage(
          "",
          firstFinalChunk,
        );

        for (const remainingChunk of remainingFinalChunks) {
          await postThreadChunkMessage(remainingChunk);
        }
        for (const citationChunk of citationBlockChunks) {
          await postThreadChunkMessage(citationChunk);
        }
      }
      
    }
  } catch (error) {
    const errorMessage = await resolveSlackErrorMessageAsync(error);
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