import { Response } from 'express';
import { FrameSink } from '../projection/stream-service';

/** A durable stream's response headers; no manual CORS header (REPORT 8). */
export const writeSseHead = (res: Response): void => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
};

export function responseSink(res: Response): FrameSink {
  return {
    write: (chunk) => {
      const ok = res.write(chunk);
      (res as Response & { flush?: () => void }).flush?.();
      return ok;
    },
    end: () => {
      if (!res.writableEnded) res.end();
    },
    onDrain: (listener) => {
      res.on('drain', listener);
    },
    get pending() {
      return res.writableLength;
    },
  };
}
