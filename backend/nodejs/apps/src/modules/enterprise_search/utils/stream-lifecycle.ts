import type { IMessagePart } from '../types/conversation.interfaces';
import { Readable } from 'stream';
import { bindOrgContext } from '../../../libs/context/orgContext';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({ service: 'StreamLifecycle' });

/**
 * Minimal shape needed from Express's `Response` — kept narrow so this is
 * trivially testable without constructing a real response object.
 *
 * Watching `res` (not `req`) matters: Node's `IncomingMessage` (`req`) fires
 * `'close'` as soon as the REQUEST body finishes being read — which, for a
 * small JSON POST body, happens almost immediately, long before an SSE
 * response is anywhere near done — regardless of whether the client is
 * still connected (see the `http.IncomingMessage`/`ServerResponse` 'close'
 * docs and https://github.com/nodejs/node/issues/40775). A `req.on('close')`
 * listener registered after any await (e.g. the initial conversation-create
 * write) can race that already-fired event and get skipped by luck, or —
 * if setup is fast enough — catch it and wrongly treat a perfectly healthy,
 * still-streaming request as a disconnect. `res`'s `'close'` also fires on
 * *normal* completion, so `writableEnded`/`writableFinished` distinguish
 * "we finished the response ourselves" from "the client is actually gone".
 */
interface ResponseWatchable {
  on(event: 'close', listener: () => void): unknown;
  readonly writableEnded?: boolean;
  readonly writableFinished?: boolean;
}

/**
 * How long a run may keep going after its browser connection is gone (refresh,
 * closed tab, network drop) before it is abandoned and saved as stopped.
 */
export const DETACHED_RUN_MAX_MS = 30 * 60 * 1000;

/**
 * Handle returned by {@link attachUpstreamAbort}. Callers pass `signal` into
 * the AI-service stream call (`executeStream`/`startAIStream`).
 */
export interface UpstreamAbortHandle {
  /** Pass to `AIServiceCommand.executeStream(signal)` / `startAIStream(...)`. */
  readonly signal: AbortSignal;
  /** True once the browser has gone away. The run is NOT stopped by this:
   * it keeps draining the upstream stream and saves its own result. */
  isClientDisconnected(): boolean;
  /** True only once we tore the upstream down ourselves (the detached-run cap
   * expired). Lets `stream.on('error')` tell that apart from a genuine
   * upstream failure. */
  isAborted(): boolean;
  /** Bind the `Readable` returned by `executeStream()` so the detached-run cap
   * can destroy it and stops counting once the stream ends. */
  bindStream(stream: Readable): void;
}

/**
 * Watches the SSE response for the browser going away. A disconnect is not a
 * Stop: only the cancel endpoint stops a run. The upstream stream is left
 * running so Node's `stream.on('end')` handler persists the finished answer
 * (writes to the closed response are no-ops), and the user finds it on reload.
 *
 * `res` emits `close` on both normal completion and a disconnect, so
 * `writableEnded`/`writableFinished` gate it. Because a run no one is watching
 * must not go on forever, the first disconnect arms a `maxDetachedMs` timer;
 * if the stream is still open when it fires, the upstream is aborted and
 * `onAbandon` runs once (callers persist the partial text as stopped).
 * `onAbandon` should still check whether the run already finalized.
 *
 * Create it BEFORE the upstream fetch is issued so `signal` is on the request
 * from the start, then `bindStream()` once the `Readable` comes back.
 */
export function attachUpstreamAbort(
  res: ResponseWatchable,
  requestId: string | undefined,
  onAbandon?: () => void,
  maxDetachedMs: number = DETACHED_RUN_MAX_MS,
): UpstreamAbortHandle {
  const controller = new AbortController();
  let clientDisconnected = false;
  let boundStream: Readable | null = null;
  let streamClosed = false;
  let abandonTimer: ReturnType<typeof setTimeout> | null = null;

  const clearAbandonTimer = (): void => {
    if (abandonTimer) {
      clearTimeout(abandonTimer);
      abandonTimer = null;
    }
  };

  const onClose = bindOrgContext(() => {
    if (clientDisconnected) return;
    if (res.writableEnded || res.writableFinished) return;
    clientDisconnected = true;
    logger.info(
      'Client disconnected; letting the run finish in the background',
      { requestId },
    );
    abandonTimer = setTimeout(() => {
      abandonTimer = null;
      if (streamClosed) return;
      logger.warn('Detached run exceeded its time cap; abandoning it', {
        requestId,
        maxDetachedMs,
      });
      controller.abort();
      boundStream?.destroy();
      onAbandon?.();
    }, maxDetachedMs);
    abandonTimer.unref();
  });
  res.on('close', onClose);

  return {
    signal: controller.signal,
    isClientDisconnected: () => clientDisconnected,
    isAborted: () => controller.signal.aborted,
    bindStream(stream: Readable) {
      boundStream = stream;
      stream.once('close', () => {
        streamClosed = true;
        clearAbandonTimer();
      });
    },
  };
}

/**
 * Accumulates the root run's answer text from parsed `TEXT_MESSAGE_CONTENT`
 * AG-UI events as they pass through a stream handler, so a passive
 * disconnect (or explicit cancel) can persist whatever the user had already
 * seen instead of discarding it. Sub-agent deltas (`parentRunId` set) are
 * excluded — they are not the visible top-level answer the client rendered.
 */
export class StreamedContentAccumulator {
  private text = '';
  private rootRunId: string | null = null;
  private approvedCalls: IMessagePart[] = [];

  /** AG-UI `TEXT_MESSAGE_CONTENT` frames carry a `delta`, appended in order. */
  feedTextMessageContent(data: {
    runId?: unknown;
    parentRunId?: unknown;
    delta?: unknown;
  }): void {
    if (data.parentRunId) return;
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    if (runId) {
      if (this.rootRunId === null) {
        this.rootRunId = runId;
      } else if (this.rootRunId !== runId) {
        return;
      }
    }
    if (typeof data.delta === 'string') {
      this.text += data.delta;
    }
  }

  /**
   * Legacy protocol's `answer_chunk` carries `accumulated` — the running
   * full text, not a delta (see `LegacyFormatter.answer_delta`) — so this
   * replaces rather than appends.
   */
  setAccumulatedText(text: string): void {
    this.text = text;
  }

  getText(): string {
    return this.text;
  }

  /** An AG-UI `TOOL_CALL_START` frame's JSON: a call a person approved is kept as a part. */
  feedToolCallStart(dataLine: string): void {
    try {
      const data = JSON.parse(dataLine) as {
        toolCallId?: unknown;
        toolCallName?: unknown;
        approved?: unknown;
      };
      if (data.approved === true && typeof data.toolCallId === 'string') {
        this.approvedCalls.push({
          type: 'tool_call',
          toolCallId: data.toolCallId,
          toolName:
            typeof data.toolCallName === 'string' ? data.toolCallName : 'tool',
          status: 'completed',
          approved: true,
        });
      }
    } catch {
      // Not ours to fail: the frame is still forwarded.
    }
  }

  /** What a stopped reply saves besides its text: the approved calls it ran, so it isn't regenerated. */
  getParts(): IMessagePart[] {
    return [...this.approvedCalls];
  }

  hasContent(): boolean {
    return this.text.length > 0;
  }
}

/**
 * Runs an async finalizer from a place that cannot await it (an event
 * listener, a timer callback). A throw would otherwise be an unhandled
 * rejection, which can take the process down.
 */
export const runGuarded = (
  task: () => Promise<void>,
  context: Record<string, unknown> = {},
): void => {
  task().catch((error: unknown) => {
    logger.error('Failed to finalize the run', {
      ...context,
      error: error instanceof Error ? error.message : String(error),
    });
  });
};
