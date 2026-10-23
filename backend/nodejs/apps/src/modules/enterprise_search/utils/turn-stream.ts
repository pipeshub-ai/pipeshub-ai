import { Readable } from 'stream';
import { Response } from 'express';
import { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import {
  IAIModel,
  IAIResponse,
  IChatSessionDocument,
  IChatSessionMessageDocument,
} from '../types/conversation.interfaces';
import { ICitation } from '../schema/citation.schema';
import { LeaseLostError } from '../services/conversation/turn/run-lease';
import { TurnGate } from '../services/conversation/turn/turn-gate';
import {
  outcomeForStatus,
  TurnOutcome,
} from '../services/conversation/turn/turn-lifecycle';
import {
  stampTurnRow,
  TurnRun,
  turnWrite,
} from '../services/conversation/turn/turn-run';
import { TurnStreamExtension } from '../services/conversation/turn/turn-extension';
import { conversationExtensions } from '../services/conversation/conversation-extensions';
import { trackRun } from './active-runs';
import { AGUIEventType, aguiErrorCodeFromPayload, frameAGUI } from './agui';
import { AIRequestRefusedError } from './ai-stream';
import {
  CHAT_ERROR_MESSAGES,
  userFacingChatError,
} from './chat-error-messages';
import {
  StreamedContentAccumulator,
  UpstreamAbortHandle,
} from './stream-lifecycle';
import {
  appendMessages,
  attachPopulatedCitations,
  markAgentConversationFailed,
  markConversationFailed,
  saveCompletedTurn,
  savePartialConversation,
  StreamFrame,
  turnViewerId,
} from './utils';

const logger = Logger.getInstance({ service: 'Turn stream' });

export const RUN_ID_HEADER = 'X-Run-Id';

/** `compression` middleware adds `flush`; plain Node responses do not. */
const flushResponse = (res: Response): void => {
  (res as Response & { flush?: () => void }).flush?.();
};

const messageOf = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

const stackOf = (error: unknown): string | undefined =>
  error instanceof Error ? error.stack : undefined;

/** Sends the SSE response headers; the run id, when there is one, rides in a header. */
export const writeSseHead = (res: Response, runId?: string): void => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*',
    'X-Accel-Buffering': 'no',
    ...(runId && { [RUN_ID_HEADER]: runId }),
  });
};

/** What the first frame tells the browser about a conversation Node just created. */
export interface ConversationCreatedFrame {
  conversationId: string;
  title?: string;
  projectId?: string;
  runId?: string;
  /** Fields the turn extension adds. */
  [field: string]: unknown;
}

/** The `conversation_created` frame that links the stream to its conversation (and, with a lease, its run). */
export const writeConversationCreated = (
  res: Response,
  value: ConversationCreatedFrame,
): void => {
  res.write(
    frameAGUI(AGUIEventType.CUSTOM, {
      name: 'conversation_created',
      value,
    }),
  );
  flushResponse(res);
};

/** Opens the SSE response and sends the first frame; the run id rides in a header and in that frame. */
export const openTurnSse = (
  res: Response,
  conversationId: string,
  runId?: string,
  extraFields: Record<string, unknown> = {},
): void => {
  writeSseHead(res, runId);
  writeConversationCreated(res, {
    conversationId,
    ...(runId && { runId }),
    ...extraFields,
  });
};

const runErrorFrame = (message: string, code: string): string =>
  frameAGUI(AGUIEventType.RUN_ERROR, { message, code });

/** The run was stopped by the server, not failed; the browser already treats `abort` as a stop. */
export const STOPPED_FRAME = runErrorFrame(
  'This answer was stopped. Ask again.',
  'abort',
);

/** This process is shutting down: the run stopped and its partial answer was saved. */
export const SHUTDOWN_FRAME = runErrorFrame(
  'PipesHub is restarting. Your answer so far was saved.',
  'abort',
);

export const TIMEOUT_FRAME = runErrorFrame(
  CHAT_ERROR_MESSAGES.timedOut,
  'timeout',
);

/** The answer of a run stopped before it ended: what streamed, shaped as the AI backend's own stopped answer. */
export const stoppedAnswer = (
  content: Pick<StreamedContentAccumulator, 'getText' | 'getParts'>,
): IAIResponse => {
  const parts = content.getParts();
  // The AI backend's stopped answer carries only these fields too.
  const answer: Pick<IAIResponse, 'answer' | 'citations' | 'status' | 'parts'> =
    {
      answer: content.getText(),
      citations: [],
      status: 'stopped',
      ...(parts.length > 0 && { parts }),
    };
  return answer as IAIResponse;
};

type MessagePart = NonNullable<IAIResponse['parts']>[number];

/** The frames a stopped run's transcript is built from. */
const TRANSCRIPT_FRAMES: ReadonlySet<string> = new Set([
  AGUIEventType.TEXT_MESSAGE_CONTENT,
  AGUIEventType.TOOL_CALL_START,
  'TOOL_CALL_ARGS',
  'TOOL_CALL_RESULT',
]);

/**
 * What a run streamed, in the shape the AI backend saves an answer in: the text written between
 * tool calls, the tool calls, and the text after the last one as the answer so far. A run the
 * server stops saves it, so its steps stay on the answer as a finished answer's do.
 */
export class StreamedTranscript {
  private rootRunId?: string;
  private readonly parts: MessagePart[] = [];
  private readonly calls = new Map<string, MessagePart>();

  /** One AG-UI frame of the stream: its type and its JSON. */
  feed(type: string | undefined, data: string): void {
    if (type === undefined || !TRANSCRIPT_FRAMES.has(type)) return;
    let frame: Record<string, unknown>;
    try {
      frame = JSON.parse(data) as Record<string, unknown>;
    } catch {
      // Not ours to fail: the frame is still forwarded.
      return;
    }
    const { toolCallId } = frame;
    if (type === AGUIEventType.TEXT_MESSAGE_CONTENT) {
      if (typeof frame.delta !== 'string' || !this.fromRoot(frame)) return;
      const last = this.parts.at(-1);
      if (last?.type === 'text') {
        last.content = (last.content ?? '') + frame.delta;
      } else {
        this.parts.push({ type: 'text', content: frame.delta });
      }
    } else if (type === AGUIEventType.TOOL_CALL_START) {
      // A call a person approved is kept wherever it ran, so the reply is never regenerated.
      const kept = this.fromRoot(frame) || frame.approved === true;
      if (typeof toolCallId !== 'string' || !kept) return;
      const call: MessagePart = {
        type: 'tool_call',
        toolCallId,
        toolName:
          typeof frame.toolCallName === 'string' ? frame.toolCallName : 'tool',
        // Until its result arrives: a call the stop cut short is saved as stopped.
        status: 'blocked',
        ...(typeof frame.displayName === 'string' && {
          displayName: frame.displayName,
        }),
        ...(frame.approved === true && { approved: true }),
      };
      this.parts.push(call);
      this.calls.set(toolCallId, call);
    } else if (type === 'TOOL_CALL_ARGS' || type === 'TOOL_CALL_RESULT') {
      const call = this.calls.get(String(toolCallId));
      if (!call) return;
      if (typeof frame.argsSummary === 'string') {
        call.argsSummary = frame.argsSummary;
      }
      if (typeof frame.resultSummary === 'string') {
        call.resultSummary = frame.resultSummary;
      }
      if (type === 'TOOL_CALL_RESULT') {
        call.status =
          typeof frame.status === 'string'
            ? (frame.status as MessagePart['status'])
            : 'completed';
      }
    }
  }

  /** The legacy protocol's running full text, which replaces the answer so far. */
  setAccumulatedText(text: string): void {
    const last = this.parts.at(-1);
    if (last?.type === 'text') {
      last.content = text;
    } else {
      this.parts.push({ type: 'text', content: text });
    }
  }

  /** The answer so far: the text after the last tool call. */
  getText(): string {
    const last = this.parts.at(-1);
    return last?.type === 'text' ? (last.content ?? '') : '';
  }

  /** The transcript, its answer so far marked as the AI backend marks the answer (`isFinal`). */
  getParts(): MessagePart[] {
    if (this.parts.length === 0) return [];
    const steps =
      this.parts.at(-1)?.type === 'text' ? this.parts.slice(0, -1) : this.parts;
    const answer: MessagePart & { isFinal: boolean } = {
      type: 'text',
      content: this.getText(),
      isFinal: true,
    };
    return [...steps, answer];
  }

  hasContent(): boolean {
    return this.parts.some(
      (part) => part.type === 'text' && part.content !== '',
    );
  }

  // shortcut: a sub-agent's own steps are left out (the AI backend nests them under its call); add them if stopped delegations need them.
  private fromRoot(frame: Record<string, unknown>): boolean {
    if (typeof frame.parentRunId === 'string') return false;
    if (typeof frame.runId !== 'string') return true;
    this.rootRunId ??= frame.runId;
    return frame.runId === this.rootRunId;
  }
}

/** When the server ends a run whose AI backend stalls; the handler's `TurnStreamExtension.runLimits` picks them. */
export interface RunLimits {
  /** No bytes at all for this long: the AI backend, or the connection to it, is gone. */
  readonly silenceMs: number;
  /** Nothing but heartbeats for this long: a model or a tool hangs. */
  readonly idleMs?: number;
  /** Stands in for `idleMs` while a tool call is open, because a tool sends nothing until it ends. */
  readonly toolIdleMs?: number;
  /** Ends the run this long after its stream opened, however it progresses. */
  readonly maxMs?: number;
}

export type RunLimit = 'silence' | 'idle' | 'max';

/** The AI backend sends a heartbeat every 15 s, so a minute without a byte means it is gone. */
export const DEFAULT_RUN_LIMITS: RunLimits = { silenceMs: 60_000 };

const WATCHDOG_TICK_MS = 5_000;

/** Holds one AI-backend stream to its limits, checking every 5 s; `onBreach` runs once. */
export class RunWatchdog {
  private readonly startedAt = Date.now();
  private lastByteAt = this.startedAt;
  private lastProgressAt = this.startedAt;
  private openToolCalls = 0;
  private partial = '';
  private readonly timer: NodeJS.Timeout;

  constructor(
    private readonly limits: RunLimits,
    onBreach: (limit: RunLimit) => void,
  ) {
    this.timer = setInterval(() => {
      const limit = this.breached(Date.now());
      if (limit === undefined) return;
      this.stop();
      onBreach(limit);
    }, WATCHDOG_TICK_MS);
    this.timer.unref();
  }

  /** Each chunk of the stream, as it arrives. */
  onChunk(chunk: Buffer | string): void {
    this.lastByteAt = Date.now();
    const frames = (this.partial + chunk.toString()).split('\n\n');
    this.partial = frames.pop() ?? '';
    for (const frame of frames) {
      const type = /^event:\s*(\S+)/m.exec(frame)?.[1];
      if (frame.trim() === '' || type === 'HEARTBEAT') continue;
      this.lastProgressAt = this.lastByteAt;
      if (type === 'TOOL_CALL_START') this.openToolCalls += 1;
      if (type === 'TOOL_CALL_END' && this.openToolCalls > 0) {
        this.openToolCalls -= 1;
      }
    }
  }

  stop(): void {
    clearInterval(this.timer);
  }

  private breached(now: number): RunLimit | undefined {
    const { silenceMs, idleMs, toolIdleMs, maxMs } = this.limits;
    const idle = this.openToolCalls > 0 ? (toolIdleMs ?? idleMs) : idleMs;
    if (maxMs !== undefined && now - this.startedAt >= maxMs) return 'max';
    if (now - this.lastByteAt >= silenceMs) return 'silence';
    if (idle !== undefined && now - this.lastProgressAt >= idle) return 'idle';
    return undefined;
  }
}

export interface TurnStreamOptions {
  res: Response;
  requestId?: string;
  startTime: number;
  orgId: Types.ObjectId | string;
  conversation: IChatSessionDocument;
  run: TurnRun;
  gate: TurnGate;
  modelInfo: IAIModel;
  agent: boolean;
  upstreamAbort: UpstreamAbortHandle;
  /** Which custom events are stored as cards, and how the answering agent is named on the final frame. */
  extension?: TurnStreamExtension;
  /** The question this turn answers, the first row of a turn-only final frame. */
  question: IChatSessionMessageDocument;
  /** Which rows the final frame carries, as the request asked (`streamFrameOf`). */
  frame: StreamFrame;
}

interface Finish {
  outcome: TurnOutcome;
  /** Last frame to send before ending the response; empty when the browser already has it. */
  frame: string;
}

type Frame = Record<string, unknown>;

/**
 * Relays one AI-backend stream to the browser and finishes the turn: forwards frames, persists
 * `ask_user_question` cards as they appear, saves the answer or the failure, and settles the lease.
 * Every way the turn can end (stream end, stream error, client close, lost lease, a time limit,
 * a shutdown) goes through `claim()`, so exactly one of them writes the terminal state and settles.
 */
export class TurnStreamPump {
  private completeData: IAIResponse | null = null;
  private buffer = '';
  private upstreamErrorForwarded = false;
  private finished = false;
  private readonly content = new StreamedTranscript();
  private writes: Promise<void> = Promise.resolve();
  private disconnectSave: Promise<void> = Promise.resolve();
  private untrack?: () => void;
  private watchdog?: RunWatchdog;
  /** Cards this turn stored, for a turn-only final frame. */
  private readonly cards: IChatSessionMessageDocument[] = [];
  /** Set once the answer's write committed: from then on the turn is never marked failed. */
  private saved?: { answer: Frame; citations: ICitation[] };

  constructor(private readonly o: TurnStreamOptions) {}

  attach(stream: Readable): void {
    this.untrack = trackRun(
      (reason) =>
        reason === 'stop'
          ? this.endAsStopped()
          : this.endByServer(SHUTDOWN_FRAME),
      this.o.run.lease?.runId,
    );
    this.watchdog = new RunWatchdog(
      this.o.extension?.runLimits(this.o.run, this.o.conversation) ??
        DEFAULT_RUN_LIMITS,
      (limit) => {
        logger.warn('Run reached its time limit; stopping it', {
          requestId: this.o.requestId,
          conversationId: this.o.conversation._id,
          limit,
        });
        void this.endByServer(TIMEOUT_FRAME, true);
      },
    );
    stream.on('data', (chunk: Buffer) => {
      this.watchdog?.onChunk(chunk);
      this.onData(chunk);
    });
    stream.on('end', () => void this.onEnd());
    stream.on('error', (error: Error) => void this.onError(error));
  }

  /**
   * The browser went away and the run outlived the detached-run cap (see `attachUpstreamAbort`).
   * Resolves once its terminal write is done.
   */
  onDisconnect(): Promise<void> {
    // The answer (or its error) already arrived: finish the turn as the end of the stream would.
    if (this.completeData || this.upstreamErrorForwarded) return this.onEnd();
    if (!this.claim()) return this.disconnectSave;
    this.disconnectSave = this.stopAfterDisconnect();
    return this.disconnectSave;
  }

  /** The turn failed after the stream opened: record it, release the lease, and end the stream with an error frame. */
  async fail(error: unknown): Promise<void> {
    if (!this.claim()) return;
    const message = userFacingChatError(error);
    // A refusal the AI service named keeps its code, so the client can say what to do.
    const code =
      error instanceof AIRequestRefusedError ? error.code : 'internal_error';
    let outcome: TurnOutcome = 'failed';
    let frame = runErrorFrame(message, code);
    try {
      if (error instanceof LeaseLostError) {
        outcome = 'stopped';
        frame = STOPPED_FRAME;
      } else {
        await this.writes;
        await this.markFailed(message, code, stackOf(error));
      }
    } catch (dbError: unknown) {
      if (dbError instanceof LeaseLostError) {
        outcome = 'stopped';
        frame = STOPPED_FRAME;
      } else {
        logger.error('Failed to mark conversation as failed in catch block', {
          requestId: this.o.requestId,
          conversationId: this.o.conversation._id,
          error: messageOf(dbError),
        });
      }
    }
    await this.o.gate.settle(outcome);
    this.end(frame);
  }

  /** The lease was lost: stop the upstream, drop the answer, and end the stream as stopped. */
  onLost(): Promise<void> {
    if (!this.claim()) return Promise.resolve();
    return this.endStopped();
  }

  /**
   * The server ends the run (a time limit ran out, or this process is stopping): the upstream is
   * cancelled and what streamed is saved as a stopped answer, or as an error row when a run that
   * timed out has no text.
   */
  private async endByServer(frame: string, timedOut = false): Promise<void> {
    this.o.upstreamAbort.abort();
    // The answer (or its error) already arrived: finish the turn as the end of the stream would.
    if (this.completeData || this.upstreamErrorForwarded) return this.onEnd();
    if (!this.claim()) return;
    await (timedOut && !this.content.hasContent()
      ? this.failTimedOut()
      : this.stopAfterDisconnect());
    this.end(frame);
  }

  /** Someone stopped the run: it ends as the AI backend's own stop does, with what streamed saved as a stopped answer. */
  private endAsStopped(): Promise<void> {
    this.o.upstreamAbort.abort();
    if (!this.finished && !this.upstreamErrorForwarded) {
      this.completeData ??= stoppedAnswer(this.content);
    }
    return this.onEnd();
  }

  private async failTimedOut(): Promise<void> {
    try {
      await this.writes;
      await this.markFailed(CHAT_ERROR_MESSAGES.timedOut, 'timeout');
    } catch (error: unknown) {
      logger.error('Failed to record the run that ran out of time', {
        requestId: this.o.requestId,
        conversationId: this.o.conversation._id,
        error: messageOf(error),
      });
    }
    await this.o.gate.settle('failed');
  }

  /** The run is over: a shutdown no longer waits for it, and its limits stop counting. */
  private stopWatching(): void {
    this.untrack?.();
    this.watchdog?.stop();
  }

  private claim(): boolean {
    if (this.finished) return false;
    this.finished = true;
    return true;
  }

  private markFailed(
    message: string,
    errorType?: string,
    stack?: string,
  ): Promise<void> {
    const mark = this.o.agent
      ? markAgentConversationFailed
      : markConversationFailed;
    return mark(
      this.o.conversation,
      message,
      null,
      errorType,
      stack,
      undefined,
      this.o.run,
    );
  }

  /** Writes made while frames are still arriving run in order, and the terminal write waits for them. */
  private enqueue(label: string, task: () => Promise<void>): void {
    this.writes = this.writes.then(task).catch((error: unknown) => {
      if (error instanceof LeaseLostError) {
        void this.onLost();
        return;
      }
      logger.error(label, {
        requestId: this.o.requestId,
        conversationId: this.o.conversation._id,
        error: messageOf(error),
      });
    });
  }

  private onData(chunk: Buffer): void {
    if (this.finished) return;
    this.buffer += chunk.toString();
    const events = this.buffer.split('\n\n');
    this.buffer = events.pop() ?? '';

    let forward = '';
    for (const event of events) {
      if (event.trim() !== '' && this.handleEvent(event)) {
        forward += event + '\n\n';
      }
    }
    if (forward !== '') {
      this.o.res.write(forward);
      flushResponse(this.o.res);
    }
  }

  /** True when the frame goes on to the browser. */
  private handleEvent(event: string): boolean {
    const lines = event.split('\n');
    const eventType = lines
      .find((line) => line.startsWith('event:'))
      ?.replace('event:', '')
      .trim();
    const data = lines
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.replace(/^data: ?/, ''))
      .join('\n');
    if (data === '') return true;

    if (eventType === AGUIEventType.RUN_FINISHED) {
      // The root RUN_FINISHED's `result` IS `completion_data`; Node re-emits its own enriched
      // frame after saving. Nested (sub-agent) frames carry no `result` and pass through.
      const parsed = this.parse(data, 'RUN_FINISHED');
      if (!parsed?.result) return true;
      this.completeData = parsed.result as IAIResponse;
      logger.debug('Captured RUN_FINISHED result from AI backend', {
        requestId: this.o.requestId,
        conversationId: this.o.conversation._id,
        citationsCount: Array.isArray(this.completeData.citations)
          ? this.completeData.citations.length
          : 0,
      });
      return false;
    }
    // Feeds what a stopped run saves.
    this.content.feed(eventType, data);
    if (eventType === AGUIEventType.RUN_ERROR) {
      const parsed = this.parse(data, 'RUN_ERROR');
      if (!parsed) return true;
      this.upstreamErrorForwarded = true;
      const message =
        typeof parsed.message === 'string' && parsed.message !== ''
          ? parsed.message
          : CHAT_ERROR_MESSAGES.failed;
      this.enqueue('Failed to mark conversation from AI RUN_ERROR SSE', () =>
        this.markFailed(
          message,
          aguiErrorCodeFromPayload(parsed),
          typeof parsed.stack === 'string' ? parsed.stack : undefined,
        ),
      );
      return true;
    }
    if (eventType === AGUIEventType.CUSTOM) {
      const parsed = this.parse(data, 'CUSTOM', false);
      const value = parsed?.value as { toolData?: unknown } | undefined;
      if (typeof parsed?.name === 'string') {
        this.o.extension?.answer?.observe(parsed.name, parsed.value);
      }
      if (parsed?.name === 'ask_user_question' && value) {
        this.persistToolCard('ask_user_question', value.toolData ?? value);
      } else if (typeof parsed?.name === 'string' && value) {
        const toolName = conversationExtensions().turns.toolCardFor(
          parsed.name,
        );
        if (toolName) this.persistToolCard(toolName, value);
      }
    }
    return true;
  }

  private parse(
    data: string,
    label: string,
    logFailure = true,
  ): Frame | undefined {
    try {
      return JSON.parse(data) as Frame;
    } catch (parseError: unknown) {
      if (logFailure) {
        logger.error(`Failed to parse ${label} event data`, {
          requestId: this.o.requestId,
          parseError: messageOf(parseError),
          dataLine: data,
        });
      }
      return undefined;
    }
  }

  private persistToolCard(toolName: string, toolResult: unknown): void {
    const { conversation, run } = this.o;
    const card = stampTurnRow(
      {
        messageType: 'tool_call',
        content: '',
        tools: [{ toolName, toolResult }],
        createdAt: new Date(),
        updatedAt: new Date(),
      },
      run,
    );
    this.enqueue(
      `Failed to persist ${toolName} tool_call message`,
      async () => {
        const stored = await turnWrite(run, null, (dbSession) =>
          appendMessages(
            conversation._id,
            conversation.orgId,
            [card],
            dbSession,
          ),
        );
        if (this.o.frame === 'turn') this.cards.push(...stored);
      },
    );
  }

  /** What this turn stored before its answer: its question, then its cards. */
  private turnRows(): Frame[] {
    return [this.o.question, ...this.cards].map(
      (row) => row.toObject() as Frame,
    );
  }

  private async onEnd(): Promise<void> {
    logger.debug('Stream ended successfully', { requestId: this.o.requestId });
    if (!this.claim()) return;
    const { outcome, frame } = await this.writes
      .then(() => this.finishStream())
      .catch((error: unknown) => this.finishAfterError(error));
    // Release before the last frame: a browser that sends again on seeing it must not meet our lease.
    await this.o.gate.settle(outcome);
    this.end(frame);
  }

  private async finishStream(): Promise<Finish> {
    const { conversation, run } = this.o;
    if (this.completeData) {
      const saved = (await saveCompletedTurn(
        conversation,
        this.completeData,
        String(this.o.orgId),
        {
          modelInfo: this.o.modelInfo,
          run,
          agent: this.o.agent,
          frame: this.o.frame,
          ...(this.o.frame === 'turn' && { turnRows: this.turnRows() }),
          answer: this.o.extension?.answer,
          onSaved: (answer, citations) => {
            this.saved = { answer, citations };
          },
        },
      )) as Frame;
      const guest = await this.guestAgentView();
      if (guest && Array.isArray(saved.messages)) {
        saved.messages = (saved.messages as Frame[]).map((m) =>
          m.messageType === 'bot_response' && m.respondingAgentKey === guest.key
            ? { ...m, respondingAgent: guest }
            : m,
        );
      }
      return {
        outcome: outcomeForStatus(saved.status as string | undefined),
        frame: this.finishedFrame(saved, guest),
      };
    }
    if (this.upstreamErrorForwarded) {
      // The RUN_ERROR frame already reached the browser and its failure row was queued.
      return { outcome: 'failed', frame: '' };
    }
    await this.markFailed(CHAT_ERROR_MESSAGES.interrupted, 'no_response');
    return {
      outcome: 'failed',
      frame: runErrorFrame(CHAT_ERROR_MESSAGES.interrupted, 'no_response'),
    };
  }

  private async guestAgentView(): Promise<
    (Record<string, unknown> & { key?: unknown }) | undefined
  > {
    if (!this.o.run.respondingAgentKey) return undefined;
    return this.o.extension?.respondingAgentView(this.o.run);
  }

  /** The RUN_FINISHED frame Node sends once the answer is saved. */
  private finishedFrame(conversation: Frame, guest?: Frame): string {
    // Python omits `citations` on some agent answers despite the type.
    const citations = this.completeData?.citations;
    const recordsUsed = Array.isArray(citations) ? citations.length : 0;
    return frameAGUI(AGUIEventType.RUN_FINISHED, {
      result: {
        conversation,
        ...(guest && { respondingAgent: guest }),
        recordsUsed,
        meta: {
          requestId: this.o.requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - this.o.startTime,
          recordsUsed,
        },
      },
    });
  }

  /** The answer is stored but its frame failed: the turn stands, and the browser gets this turn's rows alone. */
  private async finishSaved(
    error: unknown,
    saved: { answer: Frame; citations: ICitation[] },
  ): Promise<Finish> {
    const { conversation, run, requestId } = this.o;
    logger.error('Failed to send the saved answer; sending this turn alone', {
      requestId,
      conversationId: conversation._id,
      error: messageOf(error),
    });
    let frame = '';
    try {
      frame = this.finishedFrame(
        (await attachPopulatedCitations(
          conversation.toObject(),
          [...this.turnRows(), saved.answer],
          saved.citations,
          null,
          turnViewerId(run, conversation),
          'turn',
        )) as Frame,
      );
    } catch (frameError: unknown) {
      logger.error('Failed to build the frame of a saved answer', {
        requestId,
        conversationId: conversation._id,
        error: messageOf(frameError),
      });
    }
    return {
      outcome: outcomeForStatus(conversation.status as string | undefined),
      frame,
    };
  }

  private async finishAfterError(error: unknown): Promise<Finish> {
    const { conversation, requestId } = this.o;
    if (this.saved) return this.finishSaved(error, this.saved);
    let cause = error;
    try {
      if (error instanceof LeaseLostError) throw error;
      if (this.completeData) {
        await this.markFailed(
          userFacingChatError(error),
          'internal_error',
          stackOf(error),
        );
      }
    } catch (inner: unknown) {
      if (inner instanceof LeaseLostError) {
        logger.warn('Lease lost before the answer was saved', {
          requestId,
          conversationId: conversation._id,
        });
        return { outcome: 'stopped', frame: STOPPED_FRAME };
      }
      cause = inner;
    }
    logger.error('Failed to save AI response to conversation', {
      requestId,
      conversationId: conversation._id,
      error: messageOf(cause),
    });
    return {
      outcome: 'failed',
      frame: runErrorFrame(CHAT_ERROR_MESSAGES.saveFailed, 'save_error'),
    };
  }

  private async onError(error: Error): Promise<void> {
    const { upstreamAbort, requestId, conversation } = this.o;
    if (upstreamAbort.isAborted()) {
      logger.debug('Stream abandoned after the detached-run cap', {
        requestId,
      });
      return;
    }
    if (!this.claim()) return;
    logger.error('Stream error', { requestId, error: error.message });
    let outcome: TurnOutcome = 'failed';
    let frame = runErrorFrame(userFacingChatError(error), 'stream_error');
    try {
      await this.writes;
      // The agent route never recorded a type or stack for a broken stream; keep its stored errors as they were.
      await (this.o.agent
        ? this.markFailed(userFacingChatError(error))
        : this.markFailed(
            userFacingChatError(error),
            'stream_error',
            error.stack,
          ));
    } catch (dbError: unknown) {
      if (dbError instanceof LeaseLostError) {
        outcome = 'stopped';
        frame = STOPPED_FRAME;
      } else {
        logger.error('Failed to mark conversation as failed', {
          requestId,
          conversationId: conversation._id,
          error: messageOf(dbError),
        });
      }
    }
    await this.o.gate.settle(outcome);
    this.end(frame);
  }

  private async stopAfterDisconnect(): Promise<void> {
    const { conversation, run, requestId } = this.o;
    try {
      await this.writes;
      await savePartialConversation(
        conversation,
        this.content.getText(),
        null,
        { run, parts: this.content.getParts() },
      );
    } catch (error: unknown) {
      logger.error('Failed to save partial conversation on disconnect', {
        requestId,
        error: messageOf(error),
      });
    }
    await this.o.gate.settle('stopped');
    this.stopWatching();
  }

  private async endStopped(): Promise<void> {
    logger.warn('Run lost its lease; stopping the stream', {
      requestId: this.o.requestId,
      conversationId: this.o.conversation._id,
    });
    this.o.upstreamAbort.abort();
    await this.o.gate.settle('stopped');
    this.end(STOPPED_FRAME);
  }

  private end(frame: string): void {
    this.stopWatching();
    if (frame !== '') this.o.res.write(frame);
    this.o.res.end();
  }
}
