import type { Readable } from 'stream';
import type { Response } from 'express';

/**
 * Releases a source stream when the client of `res` goes away before the body was fully sent.
 *
 * `source.pipe(res)` only unpipes and pauses the source when the client leaves: an upstream HTTP read, or a file
 * handle, then stays open with nothing left to drain it. Destroying the source closes the upstream socket or the
 * descriptor.
 */
export function releaseOnClientClose(res: Response, source: Readable): void {
  const release = (): void => {
    if (!res.writableFinished && !source.destroyed) {
      source.destroy();
    }
  };
  // A client that left while the source was still being opened has already fired `close`: nothing would run later.
  if (res.destroyed) {
    release();
    return;
  }
  // A response without an event emitter can never signal a close: nothing to release on.
  if (typeof res.on === 'function') res.on('close', release);
}

/**
 * Returns a signal that aborts when the client of `res` goes away before the response was fully sent. Pass it to the
 * upstream request so that a client that leaves while the upstream is still answering (a slow conversion, an access
 * check) does not leave that request running.
 */
export function abortOnClientClose(res: Response): AbortSignal {
  const controller = new AbortController();
  if (res.destroyed) {
    controller.abort();
  } else if (typeof res.on === 'function') {
    res.on('close', () => {
      if (!res.writableFinished) controller.abort();
    });
  }
  return controller.signal;
}
