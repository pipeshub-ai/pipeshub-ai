import { Readable } from 'stream';
import { Response } from 'express';
import { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import {
  IAIModel,
  IAIResponse,
  IChatSessionDocument,
} from '../types/conversation.interfaces';
import { DRAFT_AGENT_TOOL } from '../services/collaboration/feed/draft-redaction';
import { LeaseLostError } from '../services/collaboration/leases/lease.types';
import { TurnGate } from '../services/collaboration/turn/turn-gate';
import {
  outcomeForStatus,
  TurnOutcome,
} from '../services/collaboration/turn/turn-lifecycle';
import {
  stampTurnRow,
  TurnRun,
  turnWrite,
} from '../services/collaboration/turn/turn-run';
import { AGUIEventType, aguiErrorCodeFromPayload, frameAGUI } from './agui';
import {
  CHAT_ERROR_MESSAGES,
  userFacingChatError,
} from './chat-error-messages';
import {
  isUpstreamAbortError,
  StreamedContentAccumulator,
  UpstreamAbortHandle,
} from './stream-lifecycle';
import {
  appendMessages,
  markAgentConversationFailed,
  markConversationFailed,
  saveCompletedTurn,
  savePartialConversation,
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
    'Content-Type': 'text/event-stream',
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
}

/** The `conversation_created` frame that links the stream to its conversation (and, with a lease, its run). */
export const writeConversationCreated = (
  res: Response,
  value: ConversationCreatedFrame,
): void => {
  res.write(
    frameAGUI(AGUIEventType.CUSTOM, { name: 'conversation_created', value }),
  );
  flushResponse(res);
};

/** Opens the SSE response and sends the first frame; the run id rides in a header and in that frame. */
export const openTurnSse = (
  res: Response,
  conversationId: string,
  runId?: string,
): void => {
  writeSseHead(res, runId);
  writeConversationCreated(res, { conversationId, ...(runId && { runId }) });
};

const runErrorFrame = (message: string, code: string): string =>
  frameAGUI(AGUIEventType.RUN_ERROR, { message, code });

/** The run was stopped by the server, not failed; the browser already treats `abort` as a stop. */
export const STOPPED_FRAME = runErrorFrame(
  'This turn was stopped because another run took over the conversation.',
  'abort',
);

const AGENT_DRAFT_EVENT = 'agent_draft';

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
 * Every way the turn can end (stream end, stream error, client close, lost lease) goes through
 * `claim()`, so exactly one of them writes the terminal state and settles.
 */
export class TurnStreamPump {
  private completeData: IAIResponse | null = null;
  private buffer = '';
  private upstreamErrorForwarded = false;
  private finished = false;
  private readonly content = new StreamedContentAccumulator();
  private writes: Promise<void> = Promise.resolve();
  private disconnectSave: Promise<void> = Promise.resolve();

  constructor(private readonly o: TurnStreamOptions) {}

  attach(stream: Readable): void {
    stream.on('data', (chunk: Buffer) => {
      this.onData(chunk);
    });
    stream.on('end', () => void this.onEnd());
    stream.on('error', (error: Error) => void this.onError(error));
  }

  /** The client closed the connection before the turn finished. Resolves once its terminal write is done. */
  onDisconnect(): Promise<void> {
    if (!this.claim()) return this.disconnectSave;
    this.disconnectSave = this.stopAfterDisconnect();
    return this.disconnectSave;
  }

  /** The turn failed after the stream opened: record it, release the lease, and end the stream with an error frame. */
  async fail(error: unknown): Promise<void> {
    if (!this.claim()) return;
    const message = userFacingChatError(error);
    let outcome: TurnOutcome = 'failed';
    let frame = runErrorFrame(message, 'internal_error');
    try {
      if (error instanceof LeaseLostError) {
        outcome = 'stopped';
        frame = STOPPED_FRAME;
      } else {
        await this.writes;
        await this.markFailed(message, 'internal_error', stackOf(error));
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
    if (eventType === AGUIEventType.TEXT_MESSAGE_CONTENT) {
      // Feeds the partial answer kept when the connection drops.
      const parsed = this.parse(data, 'TEXT_MESSAGE_CONTENT', false);
      if (parsed) this.content.feedTextMessageContent(parsed);
      return true;
    }
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
      if (parsed?.name === 'ask_user_question' && value) {
        this.persistToolCard('ask_user_question', value.toolData ?? value);
      }
      if (parsed?.name === AGENT_DRAFT_EVENT && value) {
        this.persistToolCard(DRAFT_AGENT_TOOL, value);
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
    this.enqueue(`Failed to persist ${toolName} tool_call message`, () =>
      turnWrite(run, null, async (dbSession) => {
        await appendMessages(
          conversation._id,
          conversation.orgId,
          [card],
          dbSession,
        );
      }),
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
    const { conversation, run, requestId } = this.o;
    if (this.completeData) {
      const saved = (await saveCompletedTurn(
        conversation,
        this.completeData,
        String(this.o.orgId),
        { modelInfo: this.o.modelInfo, run, agent: this.o.agent },
      )) as Frame;
      // Python omits `citations` on some agent answers despite the type.
      const recordsUsed = Array.isArray(this.completeData.citations)
        ? this.completeData.citations.length
        : 0;
      return {
        outcome: outcomeForStatus(saved.status as string | undefined),
        frame: frameAGUI(AGUIEventType.RUN_FINISHED, {
          result: {
            conversation: saved,
            recordsUsed,
            meta: {
              requestId,
              timestamp: new Date().toISOString(),
              duration: Date.now() - this.o.startTime,
              recordsUsed,
            },
          },
        }),
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

  private async finishAfterError(error: unknown): Promise<Finish> {
    const { conversation, requestId } = this.o;
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
    if (
      isUpstreamAbortError(error) ||
      upstreamAbort.isClientDisconnected() ||
      upstreamAbort.isAborted()
    ) {
      logger.debug('Stream aborted due to client disconnect', { requestId });
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
      // A completed answer that never reached `end` is not saved, as before.
      if (!this.completeData) {
        await savePartialConversation(
          conversation,
          this.content.getText(),
          null,
          { run },
        );
      }
    } catch (error: unknown) {
      logger.error('Failed to save partial conversation on disconnect', {
        requestId,
        error: messageOf(error),
      });
    }
    await this.o.gate.settle('stopped');
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
    if (frame !== '') this.o.res.write(frame);
    this.o.res.end();
  }
}
