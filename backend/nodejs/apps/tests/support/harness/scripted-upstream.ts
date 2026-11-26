/**
 * Scripted AG-UI SSE upstream for Node controller tests: each incoming
 * request consumes the next scripted response (status + frames), so a
 * controller can be pointed at `baseUrl` instead of the Python service.
 */
import * as http from 'http';
import type { AddressInfo } from 'net';
import { frameAGUI } from '../../../src/modules/enterprise_search/utils/agui';

export type UpstreamStep =
  | {
      kind: 'event';
      type: string;
      fields?: Record<string, unknown>;
      delayMs?: number;
    }
  /** Destroy the socket mid-stream (upstream crash). */
  | { kind: 'cut' };

export interface ScriptedResponse {
  status?: number;
  steps: UpstreamStep[];
}

export interface RecordedRequest {
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

export const event = (
  type: string,
  fields: Record<string, unknown> = {},
  delayMs = 0,
): UpstreamStep => ({ kind: 'event', type, fields, delayMs });

/** A complete single-message run: RUN_STARTED, text in `chunks`, RUN_FINISHED. */
export const textRun = (opts: {
  threadId: string;
  runId: string;
  messageId?: string;
  chunks: string[];
}): ScriptedResponse => {
  const messageId = opts.messageId ?? `${opts.runId}-msg`;
  const ids = { threadId: opts.threadId, runId: opts.runId };
  return {
    steps: [
      event('RUN_STARTED', ids),
      event('TEXT_MESSAGE_START', { messageId, role: 'assistant' }),
      ...opts.chunks.map((delta) =>
        event('TEXT_MESSAGE_CONTENT', { messageId, delta }),
      ),
      event('TEXT_MESSAGE_END', { messageId }),
      event('RUN_FINISHED', ids),
    ],
  };
};

const pause = (ms: number): Promise<void> =>
  ms > 0
    ? new Promise((resolve) => setTimeout(resolve, ms))
    : Promise.resolve();

export class ScriptedUpstream {
  readonly requests: RecordedRequest[] = [];
  private readonly queue: ScriptedResponse[];
  private server: http.Server | null = null;

  constructor(responses: ScriptedResponse[]) {
    this.queue = [...responses];
  }

  get remaining(): number {
    return this.queue.length;
  }

  async start(): Promise<string> {
    const server = http.createServer((req, res) => {
      void this.handle(req, res);
    });
    this.server = server;
    await new Promise<void>((resolve) =>
      server.listen(0, '127.0.0.1', resolve),
    );
    const { port } = server.address() as AddressInfo;
    return `http://127.0.0.1:${port}`;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    if (!server) return;
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  private async handle(
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): Promise<void> {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    this.requests.push({
      method: req.method ?? '',
      url: req.url ?? '',
      headers: req.headers,
      body: Buffer.concat(chunks).toString('utf-8'),
    });

    const response = this.queue.shift();
    if (!response) {
      res
        .writeHead(500, { 'content-type': 'text/plain' })
        .end('scripted upstream exhausted');
      return;
    }
    res.writeHead(response.status ?? 200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    });
    res.flushHeaders();
    for (const step of response.steps) {
      if (step.kind === 'cut') {
        res.destroy();
        return;
      }
      await pause(step.delayMs ?? 0);
      // Wait for each frame to flush so a later `cut` never swallows it.
      await new Promise<void>((resolve) =>
        res.write(frameAGUI(step.type, step.fields), () => resolve()),
      );
    }
    res.end();
  }
}
