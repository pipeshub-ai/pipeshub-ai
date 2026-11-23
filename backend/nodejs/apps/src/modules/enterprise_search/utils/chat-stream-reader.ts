/**
 * The one reader of the AI service's AG-UI chat stream. It splits frames, feeds the partial-save
 * accumulator, decides what the browser sees, and hands the run's outcomes to the route's hooks.
 * Saving is the route's business: the hooks are where the routes differ.
 */
import type { Response } from 'express';
import type { IAIResponse } from '../types/conversation.interfaces';
import { AGUIEventType, readRunFrame } from './agui';
import { CHAT_ERROR_MESSAGES } from './chat-error-messages';
import { logBadFrame } from './chat-stream-run';
import type { StreamedContentAccumulator } from './stream-lifecycle';

/** The root run's RUN_ERROR, read once for every route. */
export interface RunErrorInfo {
  /** The frame's `message` when it is a non-empty string, else the generic failure text. */
  message: string;
  stack: string | undefined;
  /** The frame's payload, for its `code`. */
  data: Record<string, unknown>;
}

export interface AIStreamHooks {
  /** The root run's answer. Its frame is not forwarded: the route sends its own once saved. */
  onAnswer(result: IAIResponse): void;
  /** The root run's error. Its frame is forwarded after this returns. */
  onRunError(error: RunErrorInfo): void;
  /** A question the agent asks the user (`CUSTOM{name:'ask_user_question'}`). Its frame is forwarded. */
  onAskUserQuestion(payload: unknown): void;
}

const readFrame = (
  event: string,
): { eventType: string | undefined; dataLine: string } => {
  const lines = event.split('\n');
  const eventType = lines
    .find((line) => line.startsWith('event:'))
    ?.replace('event:', '')
    .trim();
  const dataLine = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.replace(/^data: ?/, ''))
    .join('\n');
  return { eventType, dataLine };
};

const runErrorInfo = (data: Record<string, unknown>): RunErrorInfo => ({
  message:
    (typeof data.message === 'string' && data.message) ||
    CHAT_ERROR_MESSAGES.failed,
  stack: typeof data.stack === 'string' ? data.stack : undefined,
  data,
});

/** True when the frame is forwarded to the browser. */
const handleFrame = (
  eventType: string | undefined,
  dataLine: string,
  accumulator: StreamedContentAccumulator | undefined,
  hooks: AIStreamHooks,
): boolean => {
  if (eventType === AGUIEventType.TOOL_CALL_START && dataLine) {
    accumulator?.feedToolCallStart(dataLine);
    return true;
  }
  if (
    eventType === AGUIEventType.RUN_FINISHED ||
    eventType === AGUIEventType.RUN_ERROR
  ) {
    const frame = readRunFrame(eventType, dataLine);
    if (frame.kind === 'answer') {
      hooks.onAnswer(frame.result);
      return false;
    }
    if (frame.kind === 'bad') {
      logBadFrame(eventType, dataLine);
      return false;
    }
    if (frame.kind === 'error') {
      // A throw here must not escape the stream's 'data' handler.
      try {
        hooks.onRunError(runErrorInfo(frame.data));
      } catch {
        logBadFrame(eventType, dataLine);
      }
    }
    return true;
  }
  if (eventType === AGUIEventType.TEXT_MESSAGE_CONTENT && dataLine) {
    // Feeds the partial answer a dropped connection still saves (savePartialConversation).
    try {
      accumulator?.feedTextMessageContent(
        JSON.parse(dataLine) as Record<string, unknown>,
      );
    } catch {
      // Not ours to fail: the frame is still forwarded.
    }
    return true;
  }
  if (eventType === AGUIEventType.CUSTOM && dataLine) {
    try {
      // The shape is unchecked: a frame that is not an object is not a question.
      const eventData = JSON.parse(dataLine) as {
        name?: unknown;
        value?: { toolData?: unknown } | null;
      } | null;
      if (eventData?.name === 'ask_user_question' && eventData.value) {
        hooks.onAskUserQuestion(eventData.value.toolData ?? eventData.value);
      }
    } catch {
      logBadFrame('CUSTOM', dataLine);
    }
  }
  return true;
};

/**
 * Reads one chunk of the AI stream and writes the frames the browser should see, once per chunk.
 * Returns the unfinished tail, to pass back with the next chunk.
 */
export const readAIStreamChunk = (
  chunk: Buffer,
  buffer: string,
  res: Response,
  accumulator: StreamedContentAccumulator | undefined,
  hooks: AIStreamHooks,
): string => {
  const events = (buffer + chunk.toString()).split('\n\n');
  const tail = events.pop() || '';
  let forwarded = '';
  for (const event of events) {
    if (!event.trim()) continue;
    const { eventType, dataLine } = readFrame(event);
    if (handleFrame(eventType, dataLine, accumulator, hooks)) {
      forwarded += event + '\n\n';
    }
  }
  if (forwarded) {
    res.write(forwarded);
    (res as Response & { flush?: () => void }).flush?.();
  }
  return tail;
};
