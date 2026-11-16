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
  res.on('close', () => {
    if (!res.writableFinished && !source.destroyed) {
      source.destroy();
    }
  });
}
