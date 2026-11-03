/**
 * Runs one agent turn by calling this server's own agent stream route, so the
 * OpenAI endpoints get the same auth, permission, history and persistence as
 * the dashboard without a second copy of that pipeline.
 */
import {
  AgentAnswer,
  asString,
  citationsToAnnotations,
  isJson,
  Json,
} from './openai-format';

export interface AgentRunRequest {
  /** `appConfig.esBackend`: this Node API's own origin. */
  baseUrl: string;
  frontendUrl: string;
  authorization: string;
  agentKey: string;
  query: string;
  /** Continue this conversation instead of starting one. */
  conversationId?: string | null;
  signal?: AbortSignal;
  /** Called once the conversation id is known, before the answer. */
  onConversation?: (conversationId: string) => void;
  /** Called on every upstream frame; lets a streaming caller keep its client connection busy. */
  onFrame?: () => void;
}

export class AgentRunError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
  ) {
    super(message);
  }
}

interface SSEFrame {
  event: string;
  data: string;
}

/** Splits an SSE byte stream into frames; tolerant of CRLF and multi-line data. */
export async function* readSSE(
  body: AsyncIterable<Uint8Array>,
): AsyncGenerator<SSEFrame> {
  const decoder = new TextDecoder();
  let buffer = '';
  const parse = (block: string): SSEFrame | null => {
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:'))
        data.push(line.slice(5).replace(/^ /, ''));
    }
    return data.length > 0 ? { event, data: data.join('\n') } : null;
  };
  for await (const chunk of body) {
    // Normalize after appending: a chunk can end on the `\r` of a `\r\n` pair.
    buffer = (buffer + decoder.decode(chunk, { stream: true })).replace(
      /\r\n/g,
      '\n',
    );
    for (
      let cut = buffer.indexOf('\n\n');
      cut >= 0;
      cut = buffer.indexOf('\n\n')
    ) {
      const frame = parse(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
      if (frame) yield frame;
    }
  }
  const tail = parse((buffer + decoder.decode()).replace(/\r\n/g, '\n'));
  if (tail) yield tail;
}

function upstreamErrorMessage(body: unknown, fallback: string): string {
  if (!isJson(body)) return fallback;
  const error = body.error;
  if (isJson(error) && asString(error.message) !== '') {
    return asString(error.message);
  }
  return asString(error) || asString(body.message) || fallback;
}

function codeForStatus(status: number): string {
  if (status === 401) return 'invalid_api_key';
  if (status === 403) return 'permission_denied';
  if (status === 404) return 'model_not_found';
  if (status === 429) return 'rate_limit_exceeded';
  return status >= 500 ? 'server_error' : 'invalid_request';
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function answerFromConversation(
  conversation: Json,
  conversationId: string | null,
  frontendUrl: string,
): AgentAnswer {
  const messages = Array.isArray(conversation.messages)
    ? conversation.messages.filter(isJson)
    : [];
  const bot = [...messages]
    .reverse()
    .find((m) => m.messageType === 'bot_response');
  const text = asString(bot?.content);
  return {
    conversationId: conversationId ?? asString(conversation._id),
    text,
    annotations: citationsToAnnotations(bot?.citations, text, frontendUrl),
  };
}

/** The root run's answer from the frames of one agent stream. */
export async function answerFromFrames(
  frames: AsyncIterable<SSEFrame>,
  frontendUrl: string,
  knownConversationId: string | null,
  onConversation?: (conversationId: string) => void,
  onFrame?: () => void,
): Promise<AgentAnswer> {
  let conversationId = knownConversationId;
  if (conversationId !== null) onConversation?.(conversationId);
  for await (const frame of frames) {
    onFrame?.();
    const data = parseJson(frame.data);
    // Sub-agent runs carry `parentRunId`; only the root run's frames end the turn.
    if (!isJson(data) || asString(data.parentRunId) !== '') continue;
    const type = asString(data.type) || frame.event;
    if (
      type === 'CUSTOM' &&
      data.name === 'conversation_created' &&
      conversationId === null
    ) {
      const id = isJson(data.value) ? asString(data.value.conversationId) : '';
      if (id !== '') {
        conversationId = id;
        onConversation?.(id);
      }
    } else if (type === 'RUN_ERROR') {
      throw new AgentRunError(
        asString(data.message) || 'The agent run failed',
        502,
        asString(data.code) || 'server_error',
      );
    } else if (
      type === 'RUN_FINISHED' &&
      isJson(data.result) &&
      isJson(data.result.conversation)
    ) {
      return answerFromConversation(
        data.result.conversation,
        conversationId,
        frontendUrl,
      );
    }
  }
  throw new AgentRunError(
    'The agent stream ended without an answer',
    502,
    'server_error',
  );
}

export async function runAgent(req: AgentRunRequest): Promise<AgentAnswer> {
  const key = encodeURIComponent(req.agentKey);
  const base = req.baseUrl.replace(/\/$/, '');
  // The stream route answers an unknown agent with 200 + RUN_ERROR after saving an
  // empty conversation; look it up first so clients get a 404 and nothing is saved.
  // Other statuses (an OAuth token without agent:read) fall through to the run.
  const lookup = await fetch(`${base}/api/v1/agents/${key}`, {
    headers: { Authorization: req.authorization, Accept: 'application/json' },
    signal: req.signal,
  });
  if (lookup.status === 404) {
    throw new AgentRunError(
      `The model '${req.agentKey}' does not exist or you do not have access to it`,
      404,
      'model_not_found',
    );
  }
  await lookup.body?.cancel();
  const path =
    req.conversationId != null && req.conversationId !== ''
      ? `/api/v1/agents/${key}/conversations/${encodeURIComponent(req.conversationId)}/messages/stream`
      : `/api/v1/agents/${key}/conversations/stream`;
  const response = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: {
      Authorization: req.authorization,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ query: req.query, chatMode: 'quick' }),
    signal: req.signal,
  });
  if (!response.ok || response.body === null) {
    const raw = await response.text().catch(() => '');
    // Errors before the stream opens are JSON; the stream route's own catch answers 500 with one SSE frame.
    const jsonText = raw.trimStart().startsWith('{')
      ? raw
      : (/^data: ?(.*)$/m.exec(raw)?.[1] ?? '');
    const status = response.status === 0 ? 502 : response.status;
    throw new AgentRunError(
      upstreamErrorMessage(
        parseJson(jsonText),
        `Agent request failed (${String(status)})`,
      ),
      status,
      codeForStatus(status),
    );
  }
  return answerFromFrames(
    readSSE(response.body as unknown as AsyncIterable<Uint8Array>),
    req.frontendUrl,
    req.conversationId ?? null,
    req.onConversation,
    req.onFrame,
  );
}
