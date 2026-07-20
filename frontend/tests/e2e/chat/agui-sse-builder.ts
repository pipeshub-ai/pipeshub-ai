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
 * `USE_AGUI_PROTOCOL` in `chat/api.ts` is now the default, so every e2e spec
 * that mocks a chat stream response must speak this wire format instead of
 * the legacy `connected/status/answer_chunk/complete` one.
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
 * Full happy-path AG-UI event sequence, equivalent to the legacy
 * connected -> status -> answer_chunk -> complete sequence used before the
 * AG-UI migration. `RUN_FINISHED`'s `result` is exactly the payload Node's
 * `frameAGUI(AGUIEventType.RUN_FINISHED, { result: responsePayload })`
 * re-emits after persisting — see `es_controller.ts`.
 */
export function buildAguiSseBody(opts: AguiConversationOptions): string {
  const {
    conversationId,
    userMessageId,
    botMessageId,
    question,
    answer,
    modelInfo,
    requestId,
  } = opts;

  const responsePayload = {
    conversation: {
      _id: conversationId,
      userId: 'user-e2e',
      orgId: 'org-e2e',
      title: question.slice(0, 60),
      initiator: 'main',
      messages: [
        {
          _id: userMessageId,
          messageType: 'user_query',
          content: question,
          contentFormat: 'MARKDOWN',
          citations: [],
          followUpQuestions: [],
          referenceData: [],
          modelInfo,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          feedback: [],
        },
        {
          _id: botMessageId,
          messageType: 'bot_response',
          content: answer,
          contentFormat: 'MARKDOWN',
          citations: [],
          confidence: 'High',
          followUpQuestions: [],
          referenceData: [],
          modelInfo,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          feedback: [],
        },
      ],
      isShared: false,
      isDeleted: false,
      isArchived: false,
      lastActivityAt: Date.now(),
      status: 'active',
      modelInfo,
      sharedWith: [],
      conversationErrors: [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      __v: 0,
    },
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

/** `conversation_created` + one in-flight text delta, deliberately missing RUN_FINISHED — for stop/cancel tests. */
export function buildAguiPartialSseBody(conversationId: string, partialText: string): string {
  return [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId } }),
    frame('TEXT_MESSAGE_START'),
    frame('TEXT_MESSAGE_CONTENT', { delta: partialText }),
  ].join('');
}

/**
 * `conversation_created` + `CUSTOM(ask_user_question)` — deliberately no
 * `RUN_FINISHED`, matching the real backend keeping the stream open while
 * the `internaltools.ask_user_question` clarification card is interactive.
 * `toolData` shape matches `AskUserQuestionPayload` (see chat/types.ts).
 */
export function buildAguiAskUserQuestionSseBody(
  conversationId: string,
  toolData: {
    name: 'ask_user_question';
    userIntent?: string;
    questions: unknown[];
  },
  title?: string,
): string {
  return [
    frame('CUSTOM', {
      name: 'conversation_created',
      value: { conversationId, ...(title ? { title } : {}) },
    }),
    frame('CUSTOM', {
      name: 'ask_user_question',
      value: { status: 'tool_call', toolData },
    }),
  ].join('');
}
