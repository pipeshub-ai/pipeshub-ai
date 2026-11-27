import { createServer, IncomingMessage, Server } from 'http';
import { AddressInfo } from 'net';
import type {
  V3HttpPort,
  V3Response,
} from '../../../../src/plugins/telemetry/v3/sender';

export interface RecordedRequest {
  url: string;
  path: string;
  body: any;
  headers: Record<string, string>;
}

type Scripted =
  | V3Response
  | undefined
  | ((request: RecordedRequest) => V3Response | undefined | null);

/**
 * A stand-in for the usage collector, behaving as the real one does where the
 * sender depends on it: it issues a secret once per install id, checks the
 * bearer credential, applies a (processId, sequence) pair once, adds counter
 * deltas into a total per series, and marks every /v3 answer with the contract
 * header. `legacyOnly` makes it a collector from before v3.
 */
export class FakeCollector implements V3HttpPort {
  readonly requests: RecordedRequest[] = [];
  readonly secrets = new Map<string, string>();
  readonly totals = new Map<string, number>();
  readonly gauges = new Map<string, number>();
  readonly events: any[] = [];
  readonly legacyMetrics: any[] = [];
  readonly legacyEvents: any[] = [];
  legacyOnly = false;
  private readonly seen = new Set<string>();
  private readonly scripted: Scripted[] = [];
  private issued = 0;

  /** Answer the next v3 request with this instead of handling it. `undefined`: no answer at all. */
  failNext(...responses: Scripted[]): void {
    this.scripted.push(...responses);
  }

  paths(): string[] {
    return this.requests.map((r) => r.path);
  }

  total(name: string, labels: Record<string, string> = {}): number {
    return this.totals.get(key(name, labels)) ?? 0;
  }

  async post(
    url: string,
    body: unknown,
    headers: Record<string, string>,
  ): Promise<V3Response | undefined> {
    const path = new URL(url).pathname;
    // What goes on the wire, so a later change to the sender's own copy does not change it.
    const request: RecordedRequest = {
      url,
      path,
      body: JSON.parse(JSON.stringify(body)),
      headers: { ...headers },
    };
    this.requests.push(request);

    if (path.endsWith('/collect-metrics')) {
      this.legacyMetrics.push(request.body);
      return { status: 200, contract: 'legacy', body: { success: true } };
    }
    if (path.endsWith('/collect-events')) {
      this.legacyEvents.push(request.body);
      return { status: 200, contract: 'legacy', body: { success: true } };
    }
    if (this.legacyOnly) {
      // Express's answer to a path it does not know: no contract header.
      return { status: 404, body: 'Cannot POST' };
    }
    if (this.scripted.length > 0) {
      const next = this.scripted.shift();
      const answer = typeof next === 'function' ? next(request) : next;
      if (answer !== null) return answer;
    }
    const v3 = (status: number, answer: unknown = {}): V3Response => ({
      status,
      contract: '3',
      body: answer,
    });

    if (path.endsWith('/v3/installs')) {
      const { installId } = request.body;
      const current = this.secrets.get(installId);
      if (current !== undefined) {
        if (
          request.headers.Authorization !== `Bearer ${installId}.${current}`
        ) {
          return v3(request.headers.Authorization ? 401 : 409);
        }
      }
      const secret = `secret-${++this.issued}-${'x'.repeat(30)}`;
      this.secrets.set(installId, secret);
      return v3(current === undefined ? 201 : 200, {
        installId,
        secret,
        rotated: current !== undefined,
      });
    }

    const match = /^Bearer ([^.]+)\.(.+)$/.exec(
      request.headers.Authorization ?? '',
    );
    if (!match || this.secrets.get(match[1]!) !== match[2]) return v3(401);
    const installId = match[1]!;
    const kind = path.endsWith('/v3/metrics') ? 'metrics' : 'events';
    const { processId, sequence } = request.body;
    const id = `${installId}|${kind}|${processId}|${sequence}`;
    if (this.seen.has(id)) {
      return v3(200, { success: true, duplicate: true, accepted: 0 });
    }
    this.seen.add(id);
    if (kind === 'metrics') {
      for (const point of request.body.points) {
        const series = key(point.name, point.labels);
        if (point.type === 'counter') {
          this.totals.set(series, (this.totals.get(series) ?? 0) + point.value);
        } else {
          this.gauges.set(series, point.value);
        }
      }
      return v3(200, {
        success: true,
        duplicate: false,
        accepted: request.body.points.length,
      });
    }
    this.events.push(...request.body.events);
    return v3(200, {
      success: true,
      duplicate: false,
      accepted: request.body.events.length,
    });
  }

  /** Serve this collector over real HTTP, for the axios adapter. */
  async listen(): Promise<{ baseUrl: string; close: () => Promise<void> }> {
    const server: Server = createServer((req, res) => {
      void readBody(req).then(async (text) => {
        const headers: Record<string, string> = {};
        if (typeof req.headers.authorization === 'string') {
          headers.Authorization = req.headers.authorization;
        }
        const answer = await this.post(
          `http://collector${req.url ?? '/'}`,
          text === '' ? {} : JSON.parse(text),
          headers,
        );
        if (answer === undefined) {
          req.socket.destroy();
          return;
        }
        if (answer.contract !== undefined) {
          res.setHeader('X-Usage-Contract', answer.contract);
        }
        if (answer.retryAfterSeconds !== undefined) {
          res.setHeader('Retry-After', String(answer.retryAfterSeconds));
        }
        res.statusCode = answer.status;
        res.setHeader('Content-Type', 'application/json');
        res.end(JSON.stringify(answer.body ?? {}));
      });
    });
    await new Promise<void>((resolve) =>
      server.listen(0, '127.0.0.1', resolve),
    );
    const { port } = server.address() as AddressInfo;
    return {
      baseUrl: `http://127.0.0.1:${String(port)}`,
      close: () =>
        new Promise<void>((resolve) => server.close(() => resolve())),
    };
  }
}

export function key(name: string, labels: Record<string, string> = {}): string {
  const parts = Object.keys(labels)
    .sort()
    .map((k) => `${k}=${labels[k] ?? ''}`);
  return `${name}{${parts.join(',')}}`;
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let text = '';
    req.on('data', (chunk: Buffer) => {
      text += chunk.toString('utf8');
    });
    req.on('end', () => resolve(text));
  });
}
