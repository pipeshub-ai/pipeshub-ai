/**
 * Pure mappers between OpenAI's Chat Completions / Responses wire shapes and
 * a PipesHub assistant run. No I/O here, so every shape is unit-tested.
 */
import { randomBytes } from 'crypto';

export type Json = Record<string, unknown>;

export const isJson = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export const asString = (value: unknown): string =>
  typeof value === 'string' ? value : '';

export interface ChatAnswer {
  conversationId: string;
  text: string;
  annotations: UrlCitation[];
}

export interface UrlCitation {
  type: 'url_citation';
  url: string;
  title: string;
  start_index: number;
  end_index: number;
}

export interface OpenAIError {
  error: { message: string; type: string; param: string | null; code: string };
}

export function openAIError(
  message: string,
  code: string,
  type = 'invalid_request_error',
  param: string | null = null,
): OpenAIError {
  return { error: { message, type, param, code } };
}

/** Text of an OpenAI message `content`: a string, or parts carrying `text`. */
export function contentText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((part) => (isJson(part) ? asString(part.text) : ''))
    .filter((text) => text !== '')
    .join('\n');
}

export interface TurnMessage {
  role: string;
  text: string;
}

/**
 * One assistant query from the client's turns. A single user turn goes through
 * as-is; anything more is folded into one plain-text transcript, because the
 * chat routes refuse client-supplied history as structured turns (CE-19) and
 * keep their own history only for conversations they created.
 */
export function turnsToQuery(
  turns: TurnMessage[],
  instructions?: string,
): string {
  const kept = turns.filter((t) => t.text.trim() !== '');
  const lastUserIndex = kept.map((t) => t.role).lastIndexOf('user');
  const current = kept[lastUserIndex];
  if (current === undefined) return '';
  const before = kept.slice(0, lastUserIndex);
  const system = [
    instructions ?? '',
    ...before
      .filter((t) => t.role === 'system' || t.role === 'developer')
      .map((t) => t.text),
  ].filter((s) => s.trim() !== '');
  const history = before.filter(
    (t) => t.role === 'user' || t.role === 'assistant',
  );
  if (system.length === 0 && history.length === 0) return current.text;

  const sections: string[] = [];
  if (system.length > 0) {
    sections.push(
      `Instructions from the calling application:\n${system.join('\n\n')}`,
    );
  }
  if (history.length > 0) {
    sections.push(
      `Conversation so far:\n${history
        .map((t) => `${t.role === 'user' ? 'User' : 'Assistant'}: ${t.text}`)
        .join('\n\n')}`,
    );
  }
  sections.push(`Current message:\n${current.text}`);
  return sections.join('\n\n');
}

/**
 * File ids attached to messages: Chat Completions `{type: "file", file: {file_id}}`
 * and Responses `{type: "input_file", file_id}` parts. Inline file data is not read.
 */
export function attachedFileIds(messages: unknown): unknown[] {
  if (!Array.isArray(messages)) return [];
  const ids: unknown[] = [];
  for (const message of messages.filter(isJson)) {
    const parts = Array.isArray(message.content)
      ? message.content.filter(isJson)
      : [];
    for (const part of parts) {
      if (
        part.type === 'file' &&
        isJson(part.file) &&
        part.file.file_id !== undefined
      ) {
        ids.push(part.file.file_id);
      } else if (part.type === 'input_file' && part.file_id !== undefined) {
        ids.push(part.file_id);
      }
    }
  }
  return ids;
}

/** Vector store ids named by Responses `file_search` tools. */
export function fileSearchStoreIds(tools: unknown): unknown[] {
  if (!Array.isArray(tools)) return [];
  return tools
    .filter(isJson)
    .filter(
      (tool) =>
        tool.type === 'file_search' && Array.isArray(tool.vector_store_ids),
    )
    .flatMap((tool) => tool.vector_store_ids as unknown[]);
}

/** Chat Completions `messages[]` → turns. */
export function chatMessagesToTurns(messages: unknown): TurnMessage[] {
  if (!Array.isArray(messages)) return [];
  return messages
    .filter(isJson)
    .filter((m) => typeof m.role === 'string')
    .map((m) => ({ role: asString(m.role), text: contentText(m.content) }));
}

/** Responses `input` (a string, or message items) → turns. */
export function responsesInputToTurns(input: unknown): TurnMessage[] {
  if (typeof input === 'string') return [{ role: 'user', text: input }];
  if (!Array.isArray(input)) return [];
  return input
    .filter(isJson)
    .filter(
      (item) =>
        typeof item.role === 'string' &&
        (item.type === undefined || item.type === 'message'),
    )
    .map((item) => ({
      role: asString(item.role),
      text: contentText(item.content),
    }));
}

/** Our response ids carry the conversation id, so `previous_response_id` needs no new storage. */
export function newResponseId(conversationId: string): string {
  return `resp_${conversationId}_${randomBytes(6).toString('hex')}`;
}

export function conversationIdFromResponseId(
  responseId: unknown,
): string | null {
  if (typeof responseId !== 'string') return null;
  return /^resp_([a-f0-9]{24})_[a-f0-9]+$/i.exec(responseId)?.[1] ?? null;
}

const SAFE_ID = /^[A-Za-z0-9_-]{1,128}$/;

/** `file-<recordId>`: a PipesHub record exposed as an OpenAI file. */
export const toFileId = (recordId: string): string => `file-${recordId}`;

export function recordIdFromFileId(fileId: unknown): string | null {
  if (typeof fileId !== 'string' || !fileId.startsWith('file-')) return null;
  const id = fileId.slice('file-'.length);
  return SAFE_ID.test(id) ? id : null;
}

/** `vs_<collectionId>`: a PipesHub collection exposed as an OpenAI vector store. */
export const toVectorStoreId = (kbId: string): string => `vs_${kbId}`;

export function kbIdFromVectorStoreId(vectorStoreId: unknown): string | null {
  if (typeof vectorStoreId !== 'string' || !vectorStoreId.startsWith('vs_')) {
    return null;
  }
  const id = vectorStoreId.slice('vs_'.length);
  return SAFE_ID.test(id) ? id : null;
}

export function newChatCompletionId(): string {
  return `chatcmpl-${randomBytes(12).toString('hex')}`;
}

export function newMessageItemId(): string {
  return `msg_${randomBytes(12).toString('hex')}`;
}

function absoluteUrl(url: string, frontendUrl: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  const base = frontendUrl.replace(/\/$/, '');
  return url.startsWith('/') ? `${base}${url}` : `${base}/${url}`;
}

/**
 * The saved bot message's citations as OpenAI `url_citation` annotations,
 * one per distinct source URL. Indices span the whole text: the answer's own
 * inline markers stay in the text, and OpenAI has no per-marker anchor for us.
 */
export function citationsToAnnotations(
  citations: unknown,
  text: string,
  frontendUrl: string,
): UrlCitation[] {
  if (!Array.isArray(citations)) return [];
  const seen = new Set<string>();
  const out: UrlCitation[] = [];
  for (const citation of citations.filter(isJson)) {
    const data = isJson(citation.citationData)
      ? citation.citationData
      : citation;
    const meta = isJson(data.metadata) ? data.metadata : {};
    const rawUrl = asString(meta.webUrl).trim();
    if (rawUrl === '') continue;
    const url = absoluteUrl(rawUrl, frontendUrl);
    if (seen.has(url)) continue;
    seen.add(url);
    out.push({
      type: 'url_citation',
      url,
      title: asString(meta.recordName) || url,
      start_index: 0,
      end_index: text.length,
    });
  }
  return out;
}

const nowSeconds = (): number => Math.floor(Date.now() / 1000);

// ---------------------------------------------------------------- chat/completions

/** Chat Completions nests the citation under `url_citation`; Responses does not. */
function chatAnnotation(a: UrlCitation): Json {
  return {
    type: 'url_citation',
    url_citation: {
      url: a.url,
      title: a.title,
      start_index: a.start_index,
      end_index: a.end_index,
    },
  };
}

export function chatCompletion(
  id: string,
  model: string,
  answer: ChatAnswer,
): Json {
  return {
    id,
    object: 'chat.completion',
    created: nowSeconds(),
    model,
    choices: [
      {
        index: 0,
        message: {
          role: 'assistant',
          content: answer.text,
          refusal: null,
          annotations: answer.annotations.map(chatAnnotation),
        },
        logprobs: null,
        finish_reason: 'stop',
      },
    ],
    usage: null,
    system_fingerprint: null,
    conversation_id: answer.conversationId,
  };
}

export function chatChunk(
  id: string,
  model: string,
  delta: Json,
  finishReason: string | null = null,
): Json {
  return {
    id,
    object: 'chat.completion.chunk',
    created: nowSeconds(),
    model,
    choices: [{ index: 0, delta, logprobs: null, finish_reason: finishReason }],
  };
}

export function chatAnswerChunks(
  id: string,
  model: string,
  answer: ChatAnswer,
): Json[] {
  return [
    chatChunk(id, model, {
      content: answer.text,
      ...(answer.annotations.length > 0
        ? { annotations: answer.annotations.map(chatAnnotation) }
        : {}),
    }),
    chatChunk(id, model, {}, 'stop'),
  ];
}

// ---------------------------------------------------------------- responses

export function responseMessageItem(
  itemId: string,
  answer: ChatAnswer | null,
): Json {
  return {
    type: 'message',
    id: itemId,
    status: answer ? 'completed' : 'in_progress',
    role: 'assistant',
    content: answer
      ? [
          {
            type: 'output_text',
            text: answer.text,
            annotations: answer.annotations,
          },
        ]
      : [],
  };
}

export function responseObject(
  id: string,
  model: string,
  createdAt: number,
  status: 'in_progress' | 'completed' | 'failed',
  output: unknown[],
  extra: {
    instructions?: string | null;
    previousResponseId?: string | null;
    error?: unknown;
  } = {},
): Json {
  return {
    id,
    object: 'response',
    created_at: createdAt,
    status,
    error: extra.error ?? null,
    incomplete_details: null,
    instructions: extra.instructions ?? null,
    model,
    output,
    parallel_tool_calls: false,
    previous_response_id: extra.previousResponseId ?? null,
    store: true,
    tools: [],
    usage: null,
    metadata: {},
  };
}

/** The Responses stream events for a finished answer, after `response.created`/`in_progress`. */
export function responseAnswerEvents(
  itemId: string,
  answer: ChatAnswer,
): Json[] {
  const part = {
    type: 'output_text',
    text: answer.text,
    annotations: answer.annotations,
  };
  const base = { item_id: itemId, output_index: 0, content_index: 0 };
  return [
    {
      type: 'response.output_item.added',
      output_index: 0,
      item: responseMessageItem(itemId, null),
    },
    {
      type: 'response.content_part.added',
      ...base,
      part: { ...part, text: '', annotations: [] },
    },
    { type: 'response.output_text.delta', ...base, delta: answer.text },
    ...answer.annotations.map((annotation, i) => ({
      type: 'response.output_text.annotation.added',
      ...base,
      annotation_index: i,
      annotation,
    })),
    { type: 'response.output_text.done', ...base, text: answer.text },
    { type: 'response.content_part.done', ...base, part },
    {
      type: 'response.output_item.done',
      output_index: 0,
      item: responseMessageItem(itemId, answer),
    },
  ];
}
