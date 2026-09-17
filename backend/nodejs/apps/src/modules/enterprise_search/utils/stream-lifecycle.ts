import { Readable } from 'stream';
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
 * Handle returned by {@link attachUpstreamAbort}. Callers pass `signal` into
 * the AI-service stream call (`executeStream`/`startAIStream`) so a browser
 * disconnect propagates all the way to the upstream fetch — otherwise
 * `res.on('close')` would only ever tear down the Node-side `Readable`
 * wrapper while Python kept generating against a socket nobody was reading
 * (see Gap 1 in the Stop Generation plan).
 */
export interface UpstreamAbortHandle {
  /** Pass to `AIServiceCommand.executeStream(signal)` / `startAIStream(...)`. */
  readonly signal: AbortSignal;
  /** True once the client has disconnected — lets `stream.on('error')`
   * handlers distinguish "we did this on purpose" from a genuine upstream
   * failure (Python 5xx, network blip) without inspecting error internals. */
  isClientDisconnected(): boolean;
  /**
   * Bind the `Readable` returned by `executeStream()` so a disconnect that
   * arrives after the stream exists also destroys it directly. This is
   * belt-and-suspenders: aborting `signal` should already stop the upstream
   * fetch/reader on its own, but destroying the Readable guarantees no
   * further `data` events reach handlers once the client is confirmed gone.
   */
  bindStream(stream: Readable): void;
}

/**
 * Wires the SSE response's `close` event to an `AbortController`, returning
 * a handle callers thread through the AI-service stream call and the bound
 * `Readable`. Must be created BEFORE the upstream fetch is issued (its
 * `signal` needs to be on the request from the start) — call this
 * immediately before `startAIStream(...)`, then `bindStream()` once the
 * `Readable` comes back.
 *
 * `onDisconnect`, if given, runs once from the same `close` handler — used
 * by callers to persist a `StreamedContentAccumulator`'s partial text via
 * `savePartialConversation`. `res` emits `close` on BOTH a normal,
 * fully-completed response AND an abnormal disconnect, so this checks
 * `writableEnded`/`writableFinished` first and no-ops on normal completion.
 * `onDisconnect` should still itself check whether the run already
 * finalized (e.g. a `streamSettled` flag set by `stream.on('end')`/
 * `'error'`) before acting, for the case where both fire in the same tick.
 */
export function attachUpstreamAbort(
  res: ResponseWatchable,
  requestId: string | undefined,
  onDisconnect?: () => void,
): UpstreamAbortHandle {
  const controller = new AbortController();
  let clientDisconnected = false;
  let boundStream: Readable | null = null;

  res.on('close', () => {
    if (clientDisconnected) return;
    if (res.writableEnded || res.writableFinished) return;
    clientDisconnected = true;
    logger.debug('Client disconnected', { requestId });
    controller.abort();
    boundStream?.destroy();
    onDisconnect?.();
  });

  return {
    signal: controller.signal,
    isClientDisconnected: () => clientDisconnected,
    bindStream(stream: Readable) {
      boundStream = stream;
      // The close event may already have fired while we were still waiting
      // on `executeStream()` to resolve (e.g. Python was slow to respond) —
      // in that case `controller.abort()` already ran, but there was no
      // stream yet to destroy. Catch up now instead of waiting for a 'close'
      // event that already happened once.
      if (clientDisconnected && !stream.destroyed) {
        stream.destroy();
      }
    },
  };
}

/**
 * True for the `AbortError` produced by our own `attachUpstreamAbort`
 * controller (or Node's equivalent `ABORT_ERR`) — as opposed to a genuine
 * upstream failure. Stream error handlers use this (together with
 * `isClientDisconnected()`) to skip `markConversationFailed`/`res.write`
 * for a stop the user asked for.
 */
export function isUpstreamAbortError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const name = (error as { name?: unknown }).name;
  if (name === 'AbortError') return true;
  const code = (error as { code?: unknown }).code;
  return code === 'ABORT_ERR';
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

  hasContent(): boolean {
    return this.text.length > 0;
  }
}
