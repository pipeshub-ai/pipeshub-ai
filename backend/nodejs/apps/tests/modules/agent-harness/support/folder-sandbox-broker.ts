/**
 * A sandbox broker's `POST /v1/erase-session` (and `/v1/erase-org`, `/v1/reap`) over a real folder tree
 * (`<root>/<org>/<session>/…`, the data volume's layout, E1), served on a
 * loopback port for the production `HttpSandboxReaper`. It refuses what the
 * broker refuses (harness_core/sandbox/remote/server.py): no or another
 * credential 401, a body without string ids 400; `down` answers 503, as a
 * broker that cannot run its helper container does.
 */
import { createServer, IncomingMessage, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const BROKER_CREDENTIAL = 'qa15-worker-credential';

const body = async (request: IncomingMessage): Promise<unknown> => {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(chunk as Buffer);
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    return null;
  }
};

export class FolderSandboxBroker {
  readonly root = mkdtempSync(join(tmpdir(), 'pxh-qa15-broker-'));
  down = false;
  /** The label sets `/v1/reap` was asked for. */
  readonly reaped: Array<Record<string, string>> = [];
  private server: Server | null = null;

  /** A session's folder with one file in it, as an engine run leaves it. */
  plant(orgId: string, sessionId: string, content: string): void {
    const folder = join(this.root, orgId, sessionId);
    mkdirSync(folder, { recursive: true });
    writeFileSync(join(folder, 'notes.md'), content);
  }

  async start(): Promise<string> {
    this.server = createServer((request, response) => {
      const reply = (status: number, payload: unknown): void => {
        response.writeHead(status, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify(payload));
      };
      const routes = ['/v1/erase-session', '/v1/erase-org', '/v1/reap'];
      if (request.method !== 'POST' || !routes.includes(request.url ?? '')) {
        reply(404, { error: 'not_found' });
        return;
      }
      if (request.headers.authorization !== `Bearer ${BROKER_CREDENTIAL}`) {
        reply(401, { error: 'unauthorized' });
        return;
      }
      if (request.url === '/v1/reap') {
        void body(request).then((parsed) => {
          const labels = ((parsed ?? {}) as { labels?: Record<string, string> }).labels ?? {};
          this.reaped.push({ ...labels });
          reply(this.down ? 503 : 200, this.down ? { error: 'broker_unavailable' } : { reaped: 0 });
        });
        return;
      }
      if (request.url === '/v1/erase-org') {
        void body(request).then((parsed) => {
          const { orgId } = (parsed ?? {}) as Record<string, unknown>;
          if (typeof orgId !== 'string' || orgId.includes('/') || orgId.includes('..')) {
            reply(400, { error: 'orgId (one path segment) is required' });
            return;
          }
          if (this.down) {
            reply(503, { error: 'broker_unavailable' });
            return;
          }
          const folder = join(this.root, orgId);
          const folders = existsSync(folder) ? readdirSync(folder).length : 0;
          rmSync(folder, { recursive: true, force: true });
          reply(200, { sandboxes: 0, folders });
        });
        return;
      }
      void body(request).then((parsed) => {
        const { orgId, sessionId } = (parsed ?? {}) as Record<string, unknown>;
        if (typeof orgId !== 'string' || typeof sessionId !== 'string') {
          reply(400, { error: 'orgId and sessionId (strings) are required' });
          return;
        }
        if (this.down) {
          reply(503, { error: 'broker_unavailable' });
          return;
        }
        const folder = join(this.root, orgId, sessionId);
        const erased = existsSync(folder) ? 1 : 0;
        rmSync(folder, { recursive: true, force: true });
        reply(200, { erased });
      });
    });
    await new Promise<void>((resolve) => this.server!.listen(0, '127.0.0.1', resolve));
    return `http://127.0.0.1:${String((this.server!.address() as AddressInfo).port)}`;
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => (this.server ? this.server.close(() => resolve()) : resolve()));
    rmSync(this.root, { recursive: true, force: true });
  }
}
