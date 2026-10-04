/**
 * agui-sse-builder.ts
 *
 * Shared AG-UI SSE frame builder for e2e chat specs. Produces the same
 * hybrid `event: TYPE\ndata: {"type":TYPE,...}\n\n` frames the Python/Node
 * AG-UI path emits, matched against `agui-event-handler.ts`'s
 * `createAGUIEventHandler` switch (frontend/app/(main)/chat/agui-event-handler.ts):
 *
 *   CUSTOM(conversation_created) -> TEXT_MESSAGE_START ->
 *   TEXT_MESSAGE_CONTENT(s) -> TEXT_MESSAGE_END -> RUN_FINISHED
 *
 * `chat/api.ts::runChatStream` always negotiates `protocol: 'agui'`, so
 * every e2e spec that mocks a chat stream response must speak this wire
 * format instead of the legacy `connected/status/answer_chunk/complete` one.
 */

function frame(type: string, fields: Record<string, unknown> = {}): string {
  return `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;
}

export interface AguiConversationOptions {
  conversationId: string;
  userMessageId: string;
  botMessageId: string;
  question: string;
  answer: string;
  modelInfo: Record<string, unknown>;
  requestId?: string;
}

/**
 * A conversation as the API stores it: the `conversation` inside RUN_FINISHED,
 * and the body of GET /conversations/:id/. Leave out `answer` for a turn whose
 * reply has not been stored yet — one still streaming, or stopped before any
 * answer was persisted. Set `stopped` for a reply the user stopped: it is stored
 * with `status: 'stopped'`, which the page renders as the Stopped marker.
 */
export function buildAguiConversation(
  opts: Omit<AguiConversationOptions, 'answer' | 'botMessageId' | 'requestId'> & {
    answer?: string;
    botMessageId?: string;
    stopped?: boolean;
  },
): { _id: string; messages: Record<string, unknown>[] } & Record<string, unknown> {
  const { conversationId, userMessageId, botMessageId, question, answer, modelInfo, stopped } = opts;
  const now = new Date().toISOString();
  const messages: Record<string, unknown>[] = [
    {
      _id: userMessageId,
      messageType: 'user_query',
      content: question,
      contentFormat: 'MARKDOWN',
      citations: [],
      followUpQuestions: [],
      referenceData: [],
      modelInfo,
      createdAt: now,
      updatedAt: now,
      feedback: [],
    },
  ];
  if (answer !== undefined) {
    messages.push({
      _id: botMessageId ?? `${userMessageId}-reply`,
      messageType: 'bot_response',
      content: answer,
      contentFormat: 'MARKDOWN',
      citations: [],
      ...(stopped ? { status: 'stopped' } : { confidence: 'High' }),
      followUpQuestions: [],
      referenceData: [],
      modelInfo,
      createdAt: now,
      updatedAt: now,
      feedback: [],
    });
  }
  return {
    _id: conversationId,
    userId: 'user-e2e',
    orgId: 'org-e2e',
    title: question.slice(0, 60),
    initiator: 'main',
    messages,
    isShared: false,
    isDeleted: false,
    isArchived: false,
    lastActivityAt: Date.now(),
    status: stopped ? 'Stopped' : 'active',
    modelInfo,
    sharedWith: [],
    conversationErrors: [],
    createdAt: now,
    updatedAt: now,
    __v: 0,
  };
}

/**
 * Full happy-path AG-UI event sequence, equivalent to the legacy
 * connected -> status -> answer_chunk -> complete sequence used before the
 * AG-UI migration. `RUN_FINISHED`'s `result` is exactly the payload Node's
 * `frameAGUI(AGUIEventType.RUN_FINISHED, { result: responsePayload })`
 * re-emits after persisting — see `es_controller.ts`.
 */
export function buildAguiSseBody(opts: AguiConversationOptions): string {
  const { conversationId, question, answer, requestId } = opts;

  const responsePayload = {
    conversation: buildAguiConversation(opts),
    meta: {
      requestId: requestId ?? 'req-e2e-agui',
      timestamp: new Date().toISOString(),
      duration: 480,
    },
  };

  return [
    frame('CUSTOM', {
      name: 'conversation_created',
      value: { conversationId, title: question.slice(0, 60) },
    }),
    frame('TEXT_MESSAGE_START'),
    frame('TEXT_MESSAGE_CONTENT', { delta: answer }),
    frame('TEXT_MESSAGE_END'),
    frame('RUN_FINISHED', { result: responsePayload }),
  ].join('');
}

/** `conversation_created` + fatal `RUN_ERROR` sequence, for error-path tests. */
export function buildAguiErrorSseBody(conversationId: string, message: string): string {
  return [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId } }),
    frame('RUN_ERROR', { message }),
  ].join('');
}

/**
 * `conversation_created` + one in-flight text delta, with no RUN_FINISHED — for
 * stop/cancel tests. Serve it with `serveOpenSseStream`: a response that ends
 * after these frames is a dropped connection, which the chat reports as
 * interrupted instead of leaving the run open for Stop.
 */
export function buildAguiPartialSseBody(conversationId: string, partialText: string): string {
  return [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId } }),
    frame('TEXT_MESSAGE_START'),
    frame('TEXT_MESSAGE_CONTENT', { delta: partialText }),
  ].join('');
}

/**
 * Cooperative-stop happy path: `conversation_created` -> `TEXT_MESSAGE_START`
 * -> one partial delta -> `RUN_FINISHED` whose `result.conversation.messages`
 * bot entry carries `status: 'stopped'` — the exact shape Node's
 * `saveCompleteConversation`/`savePartialConversation` persist once Python's
 * `/chat/cancel` (or a passive disconnect) ends the run early (see
 * `es_controller.ts`, `utils.ts`). Distinct from `buildAguiPartialSseBody`,
 * which omits `RUN_FINISHED` for a run still open when the test clicks Stop.
 */
export function buildAguiStoppedSseBody(opts: AguiConversationOptions): string {
  const { conversationId, question, answer, requestId } = opts;

  const responsePayload = {
    conversation: buildAguiConversation({ ...opts, stopped: true }),
    meta: {
      requestId: requestId ?? 'req-e2e-agui-stopped',
      timestamp: new Date().toISOString(),
      duration: 480,
    },
  };

  return [
    frame('CUSTOM', {
      name: 'conversation_created',
      value: { conversationId, title: question.slice(0, 60) },
    }),
    frame('TEXT_MESSAGE_START'),
    frame('TEXT_MESSAGE_CONTENT', { delta: answer }),
    frame('RUN_FINISHED', { result: responsePayload }),
  ].join('');
}

/**
 * `conversation_created` + `TOOL_CALL_START` — no `TOOL_CALL_RESULT` or
 * `RUN_FINISHED`, matching a tool that is still running when the user clicks
 * Stop (see `handleToolCallStart` in `agui-event-handler.ts`, which leaves the
 * part `status: 'running'` until a `TOOL_CALL_RESULT` arrives). Serve it with
 * `serveOpenSseStream`, for the reason given on `buildAguiPartialSseBody`.
 */
export function buildAguiToolCallStartSseBody(
  conversationId: string,
  toolCallId: string,
  toolCallName: string,
): string {
  return [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId } }),
    frame('TOOL_CALL_START', { toolCallId, toolCallName, displayName: toolCallName }),
  ].join('');
}

/**
 * A turn that ends by asking the user a question: `conversation_created` ->
 * `CUSTOM(ask_user_question)` -> `RUN_FINISHED`. `ask_user_question` is a
 * terminal tool, so the backend always finishes the run after it
 * (`AnswerFinalizer.answer_final` in respond.py, then Node's re-emitted
 * `RUN_FINISHED`), and the card stays interactive while the user's answer
 * waits to be sent as the next turn. The stored conversation carries the
 * question as a `tool_call` message before the reply, as `es_controller.ts`
 * saves it. `toolData` shape matches `AskUserQuestionPayload` (chat/types.ts).
 */
export function buildAguiAskUserQuestionSseBody(opts: {
  conversationId: string;
  userMessageId: string;
  botMessageId: string;
  question: string;
  modelInfo: Record<string, unknown>;
  toolData: {
    name: 'ask_user_question';
    userIntent?: string;
    questions: unknown[];
  };
  title?: string;
}): string {
  const { conversationId, toolData, title } = opts;
  const conversation = buildAguiConversation({ ...opts, answer: '' });
  const [userQuery, botResponse] = conversation.messages;
  conversation.messages = [
    userQuery,
    {
      messageType: 'tool_call',
      content: '',
      tools: [{ toolName: 'ask_user_question', toolResult: toolData }],
      createdAt: userQuery.createdAt,
      updatedAt: userQuery.updatedAt,
    },
    botResponse,
  ];
  return [
    frame('CUSTOM', {
      name: 'conversation_created',
      value: { conversationId, ...(title ? { title } : {}) },
    }),
    frame('CUSTOM', {
      name: 'ask_user_question',
      value: { status: 'tool_call', toolData },
    }),
    frame('RUN_FINISHED', {
      result: {
        conversation,
        meta: { requestId: 'req-e2e-agui-ask', timestamp: new Date().toISOString(), duration: 480 },
      },
    }),
  ].join('');
}
