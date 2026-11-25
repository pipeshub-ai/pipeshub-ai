import http from 'http';
import { detachedInterval } from '../context/orgContext';

export interface HttpDrain {
  /**
   * Stops accepting, lets every accepted request be answered (at most
   * `deadlineMs`), then closes what is left: event streams, whose clients
   * reconnect elsewhere, and requests that outlived the deadline.
   */
  drain(deadlineMs: number): Promise<void>;
}

const RECHECK_MS = 100;

const contentType = (response: http.ServerResponse): string => {
  const stored = response.getHeader('content-type');
  if (stored !== undefined) return String(stored);
  // `writeHead(status, headers)` on a response with no header set sends them without storing them.
  const sent = (response as { _header?: string | null })._header ?? '';
  return /^content-type:\s*(.*)$/im.exec(sent)?.[1] ?? '';
};

// An event stream has no answer to wait for; it ends when the server says so.
const awaitsAnswer = (response: http.ServerResponse): boolean =>
  !contentType(response).startsWith('text/event-stream');

/** Tracks the responses `server` owes from now on, so a shutdown can wait for them. */
export function trackRequests(server: http.Server): HttpDrain {
  const open = new Set<http.ServerResponse>();
  let answered: (() => void) | null = null;
  const settle = (): void => {
    if (answered !== null && ![...open].some(awaitsAnswer)) answered();
  };
  server.on('request', (_request, response: http.ServerResponse) => {
    open.add(response);
    response.once('close', () => {
      open.delete(response);
      settle();
    });
  });
  return {
    async drain(deadlineMs: number): Promise<void> {
      // Not awaited: its callback also waits for upgraded sockets, which their own gateways close.
      server.close();
      let deadline: NodeJS.Timeout | undefined;
      // A request accepted just before the drain may still turn into an event stream.
      const recheck = detachedInterval(settle, RECHECK_MS, { keepAlive: true });
      await new Promise<void>((resolve) => {
        answered = resolve;
        deadline = setTimeout(resolve, deadlineMs);
        settle();
      });
      clearTimeout(deadline);
      clearInterval(recheck);
      server.closeAllConnections();
    },
  };
}
