/**
 * SLASH3 SB0 `/files`, `/open`, `/download`, Node's half: the session
 * workspace read by its owner with the session key inside Node; the file route
 * never lets a browser render workspace content (nosniff, sandbox CSP, HTML as
 * text); the zip is streamed entry by entry and round-trips through Python's
 * `zipfile` (ZIP64 and a symbolic link included); 2 GiB is refused before a
 * byte is sent; each download is audited with ids and counts only.
 */
import 'reflect-metadata';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { isEngineConfigPath, SYMLINK_MODE, WorkspaceError } from '../../../src/modules/agent-harness/domain/workspace';
import { zipStream, ZipEntry } from '../../../src/modules/agent-harness/domain/workspace-zip';
import {
  ARCHIVE_MAX_BYTES,
  SessionWorkspaceService,
  WorkspaceDownloadAudit,
} from '../../../src/modules/agent-harness/application/session-workspace-service';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_SESSION_WORKSPACES,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { workspaceCode } from './support/workspace-repository-contract';
import { ORG, OWNER, scope, workspaceWorld } from './support/slash2-workspace-world';

const collect = async (entries: ZipEntry[], options: { forceZip64?: boolean } = {}): Promise<Buffer> => {
  const parts: Buffer[] = [];
  for await (const chunk of zipStream(entries, options)) parts.push(chunk);
  return Buffer.concat(parts);
};

/** What Python's zipfile reads back: each member's name, content (base64) and mode, after its CRC check. */
function pythonRead(zip: Buffer): Array<{ name: string; data: string; mode: number }> {
  const dir = mkdtempSync(join(tmpdir(), 'slash3-zip-'));
  try {
    const file = join(dir, 'a.zip');
    writeFileSync(file, zip);
    const out = execFileSync('python3', [
      '-I',
      '-c',
      [
        'import base64, json, sys, zipfile',
        'z = zipfile.ZipFile(sys.argv[1])',
        'assert z.testzip() is None',
        'print(json.dumps([{"name": i.filename, "data": base64.b64encode(z.read(i)).decode(), "mode": i.external_attr >> 16} for i in z.infolist()]))',
      ].join('\n'),
      file,
    ]);
    return JSON.parse(out.toString('utf8'));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const entry = (path: string, content: string | Buffer, mode?: number): ZipEntry => ({
  path,
  ...(mode !== undefined ? { mode } : {}),
  read: async () => Buffer.from(content),
});

describe('SLASH3 SB0: the streaming zip writer', () => {
  const entries = (): ZipEntry[] => [
    entry('out/report.txt', 'hello '.repeat(1000)),
    entry('data/ünïcode.csv', 'a,b\n1,2\n'),
    entry('bin.dat', Buffer.from([0, 1, 2, 3, 255])),
    entry('empty.txt', ''),
    entry('link', 'out/report.txt', SYMLINK_MODE),
  ];

  for (const forceZip64 of [false, true]) {
    it(`round-trips through Python zipfile${forceZip64 ? ' in its ZIP64 form' : ''}`, async () => {
      const read = pythonRead(await collect(entries(), { forceZip64 }));
      expect(read.map((m) => m.name)).to.deep.equal(['out/report.txt', 'data/ünïcode.csv', 'bin.dat', 'empty.txt', 'link']);
      expect(Buffer.from(read[0]!.data, 'base64').toString()).to.equal('hello '.repeat(1000));
      expect([...Buffer.from(read[2]!.data, 'base64')]).to.deep.equal([0, 1, 2, 3, 255]);
      expect(read[3]!.data).to.equal('');
      // The link is stored as a link: its mode says so and its content is the target, never the file it names.
      expect(read[4]).to.deep.equal({ name: 'link', data: Buffer.from('out/report.txt').toString('base64'), mode: SYMLINK_MODE });
      expect(read[0]!.mode).to.equal(0o100644);
    });
  }

  it('reads each entry only when it reaches it (never the whole archive at once)', async () => {
    const order: string[] = [];
    const lazy = (path: string): ZipEntry => ({ path, read: async () => { order.push(path); return Buffer.from(path); } });
    const stream = zipStream([lazy('a'), lazy('b')]);
    await stream.next();
    expect(order).to.deep.equal(['a']);
    while (!(await stream.next()).done) { /* drain */ }
    expect(order).to.deep.equal(['a', 'b']);
  });
});

describe('SLASH3 SB0: the session workspace as its owner reads it (service)', () => {
  it('/files lists the head under a folder with sizes; engine plug-in paths are tagged hidden', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'out/a.txt': 'aa', 'out/sub/b.txt': 'bbb', 'top.md': 'x', '.claude/settings.json': '{}' });
    const all = await w.owner.files(scope, {});
    expect(all).to.deep.include({ workspaceId: 'ws:s1', revision: 1, omitted: 0, totalBytes: 8 });
    expect(all.files).to.deep.equal([
      { path: '.claude/settings.json', sizeBytes: 2, hidden: true },
      { path: 'out/a.txt', sizeBytes: 2 },
      { path: 'out/sub/b.txt', sizeBytes: 3 },
      { path: 'top.md', sizeBytes: 1 },
    ]);
    const out = await w.owner.files(scope, { prefix: 'out' });
    expect(out.files.map((f) => f.path)).to.deep.equal(['out/a.txt', 'out/sub/b.txt']);
    expect(out.totalBytes).to.equal(5);
    // `out` is a folder, never a prefix of another name.
    await w.write(w.run('run-b'), { 'outside.txt': 'no' });
    expect((await w.owner.files(scope, { prefix: 'out' })).files.map((f) => f.path)).to.not.include('outside.txt');
  });

  it('an earlier revision, a revision past the head, a missing folder and a traversing path', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': '1' });
    await w.write(w.run('run-b'), { 'b.txt': '2' });
    expect((await w.owner.files(scope, { revision: 1 })).files.map((f) => f.path)).to.deep.equal(['a.txt']);
    expect(await workspaceCode(w.owner.files(scope, { revision: 9 }))).to.equal('WORKSPACE_REVISION_NOT_FOUND');
    expect(await workspaceCode(w.owner.files(scope, { prefix: 'nope' }))).to.equal('WORKSPACE_PATH_NOT_FOUND');
    expect(await workspaceCode(w.owner.file(scope, { path: '../etc/passwd' }))).to.equal('WORKSPACE_INVALID_CHANGE');
    expect(await workspaceCode(w.owner.file(scope, { path: 'gone.txt' }))).to.equal('WORKSPACE_PATH_NOT_FOUND');
  });

  it('no workspace yet: an empty list at revision 0', async () => {
    const w = workspaceWorld();
    expect(await w.owner.files(scope, {})).to.deep.include({ revision: 0, files: [], totalBytes: 0, omitted: 0 });
  });

  it('/open reads one file with the session key, cut at maxBytes with its full size alongside', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'big.log': 'x'.repeat(5000) });
    const whole = await w.owner.file(scope, { path: 'big.log' });
    expect(whole).to.deep.include({ path: 'big.log', revision: 1, sizeBytes: 5000, truncated: false });
    expect(whole.bytes.length).to.equal(5000);
    const cut = await w.owner.file(scope, { path: 'big.log', maxBytes: 100 });
    expect(cut).to.deep.include({ sizeBytes: 5000, truncated: true });
    expect(cut.bytes.toString()).to.equal('x'.repeat(100));
  });

  it('only the owner: a shared viewer or a stranger gets 404 on every read', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'x' });
    for (const audience of ['shared', null] as const) {
      w.audience.value = audience;
      for (const read of [
        () => w.owner.files(scope, {}),
        () => w.owner.file(scope, { path: 'a.txt' }),
        () => w.owner.archive(scope, {}),
      ]) {
        let refused: unknown;
        try {
          await read();
        } catch (error) {
          refused = error;
        }
        expect(refused, String(audience)).to.be.instanceOf(HarnessNotFoundError);
      }
    }
  });

  it('a destroyed session key answers WORKSPACE_KEY_DESTROYED', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'x' });
    await w.keys.shred(ORG, 's1');
    expect(await workspaceCode(w.owner.file(scope, { path: 'a.txt' }))).to.equal('WORKSPACE_KEY_DESTROYED');
  });

  it('GAPS2-2: a download the audit refuses is not served', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'b.txt': 'b' });
    const files = (w.owner as unknown as { objects: never }).objects;
    const refusing = new SessionWorkspaceService(
      { findStreamAccess: async () => ({ audience: 'owner' }) } as never,
      w.workspaces,
      files,
      async () => {
        throw new Error('audit chain unavailable');
      },
    );
    for (const download of [
      () => refusing.archive(scope, {}),
      () => refusing.file(scope, { path: 'b.txt', download: true }),
    ]) {
      const refused = await download().then(
        () => null,
        (error: unknown) => error,
      );
      expect((refused as Error | null)?.message).to.equal('audit chain unavailable');
    }
    expect((await refusing.file(scope, { path: 'b.txt' })).bytes.toString()).to.equal('b');
  });

  it('/download zips a folder and audits who, which chat, which path and how much; past 2 GiB it is refused before any read', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'out/a.txt': 'aa', 'b.txt': 'b' });
    const audits: WorkspaceDownloadAudit[] = [];
    const files = (w.owner as unknown as { objects: never }).objects;
    const audited = new SessionWorkspaceService(
      { findStreamAccess: async () => ({ audience: 'owner' }) } as never,
      w.workspaces,
      files,
      async (row) => {
        audits.push(row);
      },
    );
    const archive = await audited.archive(scope, { prefix: 'out' });
    expect(archive).to.deep.include({ revision: 1, files: 1, bytes: 2 });
    const read = pythonRead(await collect(archive.entries));
    expect(read.map((m) => [m.name, Buffer.from(m.data, 'base64').toString()])).to.deep.equal([['out/a.txt', 'aa']]);
    expect(audits).to.deep.equal([
      { type: 'workspace.downloaded', orgId: ORG, actorId: OWNER, sessionId: 's1', revision: 1, target: 'archive', path: 'out', files: 1, bytes: 2 },
    ]);
    await audited.file(scope, { path: 'b.txt', download: true });
    expect(audits[1]).to.deep.include({ target: 'file', path: 'b.txt', files: 1, bytes: 1 });
    await audited.file(scope, { path: 'b.txt' });
    expect(audits).to.have.length(2);
    await audited.archive(scope, {});
    expect(audits[2]).to.deep.include({ target: 'archive', files: 2, bytes: 3 });
    expect(audits[2]).not.to.have.property('path');

    let reads = 0;
    const huge = new SessionWorkspaceService(
      { findStreamAccess: async () => ({ audience: 'owner' }) } as never,
      {
        sessionFiles: async () => ({
          workspaceId: 'ws:s1',
          revision: 3,
          parentRevision: 2,
          contentHash: 'h',
          createdAt: 1,
          files: [
            { path: 'a.bin', objectId: 'o1', contentHash: 'c1', sizeBytes: ARCHIVE_MAX_BYTES, mimeType: 'x' },
            { path: 'b.bin', objectId: 'o2', contentHash: 'c2', sizeBytes: 1, mimeType: 'x' },
          ],
        }),
      } as never,
      async () => {
        reads += 1;
        return async () => Buffer.alloc(0);
      },
    );
    expect(await workspaceCode(huge.archive(scope, {}))).to.equal('WORKSPACE_TOO_LARGE');
    expect(reads).to.equal(0);
  });

  it('engine config paths match the Python rule (any segment a plug-in folder, or a project config file)', () => {
    for (const path of ['.claude/x', 'a/.codex/b', '.opencode/p.ts', 'opencode.json', 'sub/opencode.jsonc', '.mcp.json']) {
      expect(isEngineConfigPath(path), path).to.equal(true);
    }
    for (const path of ['claude/x', 'a.claude', 'mcp.json', 'uploads/data.csv']) {
      expect(isEngineConfigPath(path), path).to.equal(false);
    }
  });
});

describe('SLASH3 SB0: GET …/workspace/files, /file and /archive', () => {
  let server: Server;
  let base: string;
  const calls: unknown[][] = [];
  let originalLogger: unknown;

  const file = (path: string, bytes: Buffer) => ({ path, revision: 4, sizeBytes: bytes.length, bytes, truncated: false });

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
    const owner = (s: { sessionId: string }) => {
      if (s.sessionId === 'not-mine') throw new HarnessNotFoundError();
    };
    container.bind(AGENT_HARNESS_SESSION_WORKSPACES).toConstantValue({
      files: async (s: { sessionId: string }, query: unknown) => {
        calls.push(['files', s, query]);
        owner(s);
        return { workspaceId: `ws:${s.sessionId}`, revision: 4, createdAt: 1, files: [], totalBytes: 0, omitted: 0 };
      },
      file: async (s: { sessionId: string }, query: { path: string }) => {
        calls.push(['file', s, query]);
        owner(s);
        if (query.path === 'missing.txt') throw new WorkspaceError('WORKSPACE_PATH_NOT_FOUND', 'No file');
        if (query.path === 'page.html') return file(query.path, Buffer.from('<script>alert(1)</script>'));
        if (query.path === 'pic.png') return file(query.path, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0]));
        if (query.path === 'logo.svg') return file(query.path, Buffer.from('<svg onload="alert(1)"/>'));
        return file(query.path, Buffer.from([1, 0, 2]));
      },
      archive: async (s: { sessionId: string }, query: unknown) => {
        calls.push(['archive', s, query]);
        owner(s);
        if (s.sessionId === 'huge') throw new WorkspaceError('WORKSPACE_TOO_LARGE', 'too big');
        return { revision: 4, files: 1, bytes: 2, entries: [entry('out/a.txt', 'aa')] };
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

  it('/files answers on chat and agent sessions; org and caller from the credential; a trailing slash is a folder', async () => {
    const chat = await fetch(`${base}/conversations/s1/workspace/files?prefix=out/`);
    expect(chat.status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal(['files', { orgId: ORG, actorId: OWNER, sessionId: 's1' }, { prefix: 'out' }]);
    const agent = await fetch(`${base}/agents/agent-1/conversations/s1/workspace/files?revision=2`);
    expect(agent.status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal(['files', { orgId: ORG, actorId: OWNER, sessionId: 's1', agentKey: 'agent-1' }, { revision: 2 }]);
  });

  it('an org, user or unknown field in the query is refused, never used; a negative revision too', async () => {
    for (const query of ['orgId=org-2', 'actorId=user-2', 'revision=-1', 'prefix=']) {
      expect((await fetch(`${base}/conversations/s1/workspace/files?${query}`)).status, query).to.equal(400);
    }
    expect((await fetch(`${base}/conversations/s1/workspace/file`)).status).to.equal(400);
    expect((await fetch(`${base}/conversations/s1/workspace/file?path=a&maxBytes=999999999`)).status).to.equal(400);
  });

  it('not the owner: 404 on every route; a missing path: 404 with its code; too large: 413 before any byte', async () => {
    for (const path of ['workspace/files', 'workspace/file?path=a.txt', 'workspace/archive']) {
      expect((await fetch(`${base}/conversations/not-mine/${path}`)).status, path).to.equal(404);
    }
    const missing = await fetch(`${base}/conversations/s1/workspace/file?path=missing.txt`);
    expect(missing.status).to.equal(404);
    expect(JSON.stringify(await missing.json())).to.contain('WORKSPACE_PATH_NOT_FOUND');
    const huge = await fetch(`${base}/conversations/huge/workspace/archive`);
    expect(huge.status).to.equal(413);
    expect(huge.headers.get('content-type')).to.contain('json');
  });

  it('never a page: HTML and SVG are text, an image is its sniffed type, all with nosniff and a sandbox CSP', async () => {
    for (const [path, type] of [
      ['page.html', 'text/plain; charset=utf-8'],
      ['logo.svg', 'text/plain; charset=utf-8'],
      ['pic.png', 'image/png'],
      ['blob.bin', 'application/octet-stream'],
    ] as const) {
      const res = await fetch(`${base}/conversations/s1/workspace/file?path=${path}`);
      expect(res.status, path).to.equal(200);
      expect(res.headers.get('content-type'), path).to.equal(type);
      expect(res.headers.get('x-content-type-options'), path).to.equal('nosniff');
      expect(res.headers.get('content-security-policy'), path).to.contain('sandbox');
      expect(res.headers.get('content-disposition'), path).to.match(/^inline;/);
      expect(res.headers.get('x-workspace-revision')).to.equal('4');
    }
    const download = await fetch(`${base}/conversations/s1/workspace/file?path=page.html&download=1&maxBytes=10`);
    expect(download.headers.get('content-disposition')).to.match(/^attachment; filename="page.html"/);
    // A download is the whole file, and it is audited by the service: it is asked with `download`, never cut.
    expect(calls.at(-1)?.[2]).to.deep.equal({ path: 'page.html', download: true });
  });

  it('/archive streams a zip as an attachment that Python reads', async () => {
    const res = await fetch(`${base}/agents/agent-1/conversations/s1/workspace/archive?prefix=out`);
    expect(res.status).to.equal(200);
    expect(res.headers.get('content-type')).to.equal('application/zip');
    expect(res.headers.get('content-disposition')).to.match(/^attachment; filename="workspace-v4.zip"/);
    expect(res.headers.get('x-content-type-options')).to.equal('nosniff');
    const read = pythonRead(Buffer.from(await res.arrayBuffer()));
    expect(read.map((m) => m.name)).to.deep.equal(['out/a.txt']);
  });
});
