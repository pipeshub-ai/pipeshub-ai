/**
 * SLASH3 SB2 `/upload`, Node's half: a person's files become one revision of
 * the session workspace (reason `upload`, its own holder), sealed with the
 * session's key in Node, under `uploads/` by default; `/undo` takes back
 * exactly it; a live writer refuses it and a moved head conflicts, as a
 * restore; a retry answers the revision it wrote; paths are checked as a
 * commit's, an engine's plug-in path is refused, replacing needs a confirm;
 * POST …/workspace/files is multipart, owner only, bounded.
 */
import { bufferedUploadFile } from '../../../src/modules/agent-harness/application/session-workspace-service';
import 'reflect-metadata';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { WorkspaceError } from '../../../src/modules/agent-harness/domain/workspace';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_SESSION_WORKSPACES,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { UPLOAD_MAX_FILE_BYTES } from '../../../src/modules/agent-harness/transport/schemas';
import { workspaceCode } from './support/workspace-repository-contract';
import { claims, ORG, OWNER, scope, WORKSPACE, workspaceWorld } from './support/slash2-workspace-world';

const file = (path: string, text: string) => bufferedUploadFile(path, Buffer.from(text), 'text/plain');
const upload = (baseRevision: number, files: ReturnType<typeof file>[], extra: Record<string, unknown> = {}) => ({
  baseRevision,
  idempotencyKey: `up-${String(baseRevision)}`,
  files,
  replace: false,
  ...extra,
});

describe('SLASH3 SB2: /upload (service)', () => {
  it('one revision with reason upload under uploads/; the next turn reads the files, sealed with the session key', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'one\n' });
    const result = await w.owner.upload(scope, upload(1, [file('data.csv', 'a,b\n'), file('sub/notes.md', '# n\n')]));
    expect(result).to.deep.include({ workspaceId: WORKSPACE, revision: 2, replayed: false, replaced: [] });
    expect(result.paths).to.deep.equal(['uploads/data.csv', 'uploads/sub/notes.md']);
    expect((await w.owner.versions(scope, {})).versions[0]).to.include({ revision: 2, reason: 'upload', restoredByUser: false });
    expect(await w.tree(w.run('run-b'))).to.deep.equal({ 'a.txt': 'one\n', 'uploads/data.csv': 'a,b\n', 'uploads/sub/notes.md': '# n\n' });
  });

  it('QAFIX4: an upload version names the paths it added, so a reloaded chat lists them; other versions name none', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'one\n', 'uploads/data.csv': 'old\n' });
    await w.owner.upload(scope, upload(1, [file('data.csv', 'a,b\n'), file('sub/notes.md', '# n\n')], { replace: true }));
    const [uploaded, written] = (await w.owner.versions(scope, {})).versions;
    expect(uploaded).to.deep.include({ revision: 2, reason: 'upload', paths: ['uploads/data.csv', 'uploads/sub/notes.md'] });
    expect(written).to.not.have.property('paths');
  });

  it('a named folder; /undo (the last turn) is exactly the upload, and restoring its `from` removes only it', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'one\n' });
    await w.owner.upload(scope, upload(1, [file('x.bin', 'x')], { folder: 'in/' }));
    const turn = await w.owner.diff(scope, 'turn');
    expect(turn).to.deep.include({ from: 1, to: 2 });
    expect(turn.files.map((f) => f.path)).to.deep.equal(['in/x.bin']);
    await w.owner.restore(scope, { revision: turn.from, baseRevision: turn.head, idempotencyKey: 'undo-up' });
    expect(await w.tree(w.run('run-b'))).to.deep.equal({ 'a.txt': 'one\n' });
  });

  it('a retry after a lost answer answers the revision it wrote; a moved head otherwise conflicts', async () => {
    const w = workspaceWorld();
    const first = await w.owner.upload(scope, upload(0, [file('a.txt', 'a')]));
    const again = await w.owner.upload(scope, upload(0, [file('a.txt', 'a')]));
    expect(again).to.deep.include({ revision: first.revision, replayed: true });
    expect(await workspaceCode(w.owner.upload(scope, { ...upload(0, [file('b.txt', 'b')]), idempotencyKey: 'other' }))).to.equal('WORKSPACE_CONFLICT');
  });

  it('a live writer (a run at work) refuses it with WORKSPACE_WRITER_BUSY', async () => {
    const w = workspaceWorld();
    const run = w.run('run-a');
    await w.workspaces.acquireWriter(claims(run), WORKSPACE, { ttlMs: 60_000 });
    expect(await workspaceCode(w.owner.upload(scope, upload(0, [file('a.txt', 'a')])))).to.equal('WORKSPACE_WRITER_BUSY');
  });

  it('refuses an engine\'s plug-in path, a traversing path, a duplicate and an empty upload; replacing needs the confirm', async () => {
    const w = workspaceWorld();
    for (const [files, folder] of [
      [[file('settings.json', '{}')], '.claude'],
      [[file('x', 'x')], 'a/.codex'],
      [[file('opencode.json', '{}')], ''],
      [[file('.mcp.json', '{}')], 'tools'],
      [[file('../escape', 'x')], undefined],
      [[file('a', '1'), file('a', '2')], undefined],
      [[], undefined],
    ] as const) {
      const request = upload(0, [...files], folder === undefined ? {} : { folder });
      expect(await workspaceCode(w.owner.upload(scope, request)), JSON.stringify(files.map((f) => f.path))).to.equal('WORKSPACE_INVALID_CHANGE');
    }
    await w.owner.upload(scope, upload(0, [file('a.txt', 'old')]));
    const refused = await workspaceCode(w.owner.upload(scope, { ...upload(1, [file('a.txt', 'new')]), idempotencyKey: 'r' }));
    expect(refused).to.equal('WORKSPACE_PATH_EXISTS');
    const replaced = await w.owner.upload(scope, { ...upload(1, [file('a.txt', 'new')]), idempotencyKey: 'r2', replace: true });
    expect(replaced.replaced).to.deep.equal(['uploads/a.txt']);
    expect((await w.tree(w.run('run-b')))['uploads/a.txt']).to.equal('new');
  });

  it('only the owner: a shared viewer or a stranger gets 404 and nothing is written', async () => {
    const w = workspaceWorld();
    for (const audience of ['shared', null] as const) {
      w.audience.value = audience;
      let refused: unknown;
      try {
        await w.owner.upload(scope, upload(0, [file('a.txt', 'a')]));
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessNotFoundError);
    }
    w.audience.value = 'owner';
    expect((await w.owner.versions(scope, {})).head).to.equal(0);
  });
});

describe('SLASH3 SB2: POST …/workspace/files', () => {
  let server: Server;
  let base: string;
  const calls: unknown[][] = [];
  let originalLogger: unknown;

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: OWNER };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({
      assertOwner: async (s: { sessionId: string }) => {
        if (s.sessionId === 'not-mine') throw new HarnessNotFoundError();
      },
    });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_SESSION_WORKSPACES).toConstantValue({
      upload: async (s: { sessionId: string }, request: { files: Array<{ path: string; read(): Promise<Buffer> }> }) => {
        const files = await Promise.all(request.files.map(async (f) => [f.path, (await f.read()).toString()]));
        calls.push([s, { ...request, files }]);
        if (s.sessionId === 'not-mine') throw new HarnessNotFoundError();
        if (s.sessionId === 'busy') throw new WorkspaceError('WORKSPACE_WRITER_BUSY', 'busy');
        return { workspaceId: `ws:${s.sessionId}`, revision: 3, replayed: false, paths: [], replaced: [] };
      },
    });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
  });

  after(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const form = (parts: Array<[string, string]>, files: Array<[string, Buffer]>, fields: Record<string, string> = {}) => {
    const body = new FormData();
    body.set('baseRevision', '2');
    body.set('idempotencyKey', 'k-1');
    body.set('paths', JSON.stringify(files.map(([name]) => name)));
    for (const [key, value] of [...parts, ...Object.entries(fields)]) body.set(key, value);
    for (const [name, bytes] of files) body.append('files', new Blob([bytes], { type: 'text/plain' }), name.split('/').pop());
    return body;
  };

  it('takes the files in order with their paths, folder and confirm; org and caller from the credential', async () => {
    const res = await fetch(`${base}/agents/agent-1/conversations/s1/workspace/files`, {
      method: 'POST',
      body: form([['folder', 'in'], ['replace', 'true']], [['data.csv', Buffer.from('a,b')], ['sub/n.md', Buffer.from('#')]]),
    });
    expect(res.status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal([
      { orgId: ORG, actorId: OWNER, sessionId: 's1', agentKey: 'agent-1' },
      { baseRevision: 2, idempotencyKey: 'k-1', folder: 'in', replace: true, files: [['data.csv', 'a,b'], ['sub/n.md', '#']] },
    ]);
  });

  it('refuses paths that do not name each file, unknown fields, a stranger (404) and a busy writer (409)', async () => {
    const bad = form([], [['a.txt', Buffer.from('a')]]);
    bad.set('paths', JSON.stringify(['a.txt', 'b.txt']));
    expect((await fetch(`${base}/conversations/s1/workspace/files`, { method: 'POST', body: bad })).status).to.equal(400);
    expect((await fetch(`${base}/conversations/s1/workspace/files`, { method: 'POST', body: form([['orgId', 'org-2']], [['a', Buffer.from('a')]]) })).status).to.equal(400);
    expect((await fetch(`${base}/conversations/not-mine/workspace/files`, { method: 'POST', body: form([], [['a', Buffer.from('a')]]) })).status).to.equal(404);
    const busy = await fetch(`${base}/conversations/busy/workspace/files`, { method: 'POST', body: form([], [['a', Buffer.from('a')]]) });
    expect(busy.status).to.equal(409);
    expect(JSON.stringify(await busy.json())).to.contain('WORKSPACE_WRITER_BUSY');
  });

  it('a file over 100 MiB is refused 413 before it reaches the store', async () => {
    const count = calls.length;
    const res = await fetch(`${base}/conversations/s1/workspace/files`, {
      method: 'POST',
      body: form([], [['big.bin', Buffer.alloc(UPLOAD_MAX_FILE_BYTES + 1)]]),
    });
    expect(res.status).to.equal(413);
    expect(calls.length).to.equal(count);
  });
});
