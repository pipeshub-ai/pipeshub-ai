import test from 'node:test';
import * as assert from 'node:assert/strict';
import { createHash } from 'crypto';
import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';
import { indexRepository, LocalRepoApiError, LocalRepoClient } from '../local-repos/client';
import { LocalRepoStore } from '../local-repos/store';

/** In-memory server speaking the documented /api/v1/local-repos contract (handoffs/L9-C-api.md). */
async function fakeServer(limits = { maxFiles: 20000, maxFileBytes: 1048576, maxBatchFiles: 2, maxBatchBytes: 4194304, maxManifestEntries: 20000 }) {
  const repos = new Map<string, { name: string; files: Map<string, string>; generation: number }>();
  const calls: string[] = [];
  const server = http.createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};
    calls.push(`${req.method} ${req.url} ${req.headers.authorization}`);
    const send = (code: number, obj: unknown) => {
      res.statusCode = code;
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify(obj));
    };
    const m = /^\/api\/v1\/local-repos(?:\/([A-Za-z0-9_-]+))?(?:\/(manifest|files|commit))?$/.exec(req.url || '');
    if (!m) return send(404, { detail: 'no route' });
    const [, key, action] = m;
    if (req.headers.authorization !== 'Bearer tok') return send(401, { detail: 'no token' });
    if (!key) return send(200, { repos: [...repos].map(([repoKey, r]) => ({ repoKey, name: r.name, state: 'ready', files: r.files.size })) });
    if (req.method === 'PUT' && !action) {
      const created = !repos.has(key);
      if (created) repos.set(key, { name: body.name, files: new Map(), generation: 0 });
      return send(created ? 201 : 200, { repoKey: key, name: body.name, state: 'empty', files: 0, limits });
    }
    const repo = repos.get(key);
    if (!repo) return send(404, { detail: 'unknown repo' });
    if (action === 'manifest') {
      const upload: string[] = [];
      for (const f of body.files) if (repo.files.get(f.path) !== f.sha256) upload.push(f.path);
      if (body.complete) {
        const keep = new Set(body.files.map((f: { path: string }) => f.path));
        for (const p of [...repo.files.keys()]) if (!keep.has(p)) repo.files.delete(p);
      }
      return send(200, { upload, deleted: [], unchanged: body.files.length - upload.length, rejected: [] });
    }
    if (action === 'files') {
      if (body.files.length > limits.maxBatchFiles) return send(413, { detail: 'batch too large' });
      const stored: string[] = [];
      for (const f of body.files) {
        assert.equal(createHash('sha256').update(f.content, 'utf8').digest('hex'), f.sha256);
        repo.files.set(f.path, f.sha256);
        stored.push(f.path);
      }
      return send(200, { stored, rejected: [] });
    }
    if (action === 'commit') {
      repo.generation += 1;
      return send(202, { repoKey: key, name: repo.name, state: 'syncing', files: repo.files.size });
    }
    if (req.method === 'DELETE') {
      repos.delete(key);
      return send(202, { repoKey: key, state: 'deleting' });
    }
    return send(200, { repoKey: key, name: repo.name, state: 'ready', files: repo.files.size });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  return { base, repos, calls, close: () => server.close() };
}

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'ph-repo-'));
}

test('indexes a plain folder: skips binary, non-UTF-8 and ignored directories, uploads in batches, commits', async () => {
  const srv = await fakeServer();
  const root = tmp();
  fs.mkdirSync(path.join(root, 'src'));
  fs.mkdirSync(path.join(root, 'node_modules/x'), { recursive: true });
  for (const n of ['a.py', 'b.py', 'c.py']) fs.writeFileSync(path.join(root, 'src', n), `print("${n}")\n`);
  fs.writeFileSync(path.join(root, 'node_modules/x/i.js'), 'x');
  fs.writeFileSync(path.join(root, 'img.bin'), Buffer.from([1, 0, 2]));
  fs.writeFileSync(path.join(root, 'latin1.txt'), Buffer.from([0x63, 0x61, 0x66, 0xe9]));

  const client = new LocalRepoClient(srv.base, () => 'tok');
  const result = await indexRepository(client, root, 'k'.repeat(16), 'demo');
  assert.equal(result.uploaded, 3);
  assert.equal(result.skipped, 2);
  assert.equal(result.status.state, 'syncing');
  assert.deepEqual([...srv.repos.get('k'.repeat(16))!.files.keys()].sort(), ['src/a.py', 'src/b.py', 'src/c.py']);
  // 3 files with a batch limit of 2 -> 2 upload calls.
  assert.equal(srv.calls.filter((c) => c.startsWith('PUT') && c.includes('/files')).length, 2);
  srv.close();
  fs.rmSync(root, { recursive: true, force: true });
});

test('a second run uploads only what changed and drops deleted files', async () => {
  const srv = await fakeServer();
  const root = tmp();
  execFileSync('git', ['init', '-q'], { cwd: root });
  fs.writeFileSync(path.join(root, '.gitignore'), 'ignored.txt\n');
  fs.writeFileSync(path.join(root, 'one.ts'), 'export const one = 1;\n');
  fs.writeFileSync(path.join(root, 'two.ts'), 'export const two = 2;\n');
  fs.writeFileSync(path.join(root, 'ignored.txt'), 'nope');
  const client = new LocalRepoClient(srv.base, () => 'tok');
  const key = 'a1b2c3d4e5f6a7b8';
  const first = await indexRepository(client, root, key, 'git-demo');
  assert.equal(first.uploaded, 3); // .gitignore, one.ts, two.ts; ignored.txt follows git's rules

  fs.writeFileSync(path.join(root, 'one.ts'), 'export const one = 11;\n');
  fs.rmSync(path.join(root, 'two.ts'));
  const second = await indexRepository(client, root, key, 'git-demo');
  assert.equal(second.uploaded, 1);
  assert.deepEqual([...srv.repos.get(key)!.files.keys()].sort(), ['.gitignore', 'one.ts']);
  srv.close();
  fs.rmSync(root, { recursive: true, force: true });
});

test('without a token nothing is sent; server errors carry the detail', async () => {
  const srv = await fakeServer();
  const noToken = new LocalRepoClient(srv.base, () => null);
  await assert.rejects(() => noToken.list(), (e: LocalRepoApiError) => e.status === 401);
  assert.equal(srv.calls.length, 0);
  const wrong = new LocalRepoClient(srv.base, () => 'other');
  await assert.rejects(() => wrong.list(), (e: LocalRepoApiError) => e.status === 401 && /no token/.test(e.message));
  const ok = new LocalRepoClient(srv.base, () => 'tok');
  await assert.rejects(() => ok.status('missingkey12345'), (e: LocalRepoApiError) => e.status === 404);
  srv.close();
});

test('a folder over the file limit is refused before anything is uploaded', async () => {
  const srv = await fakeServer({ maxFiles: 1, maxFileBytes: 1000, maxBatchFiles: 10, maxBatchBytes: 10000, maxManifestEntries: 10 });
  const root = tmp();
  fs.writeFileSync(path.join(root, 'a.txt'), 'a');
  fs.writeFileSync(path.join(root, 'b.txt'), 'b');
  const client = new LocalRepoClient(srv.base, () => 'tok');
  await assert.rejects(() => indexRepository(client, root, 'z'.repeat(16), 'big'), (e: LocalRepoApiError) => e.status === 413);
  assert.equal(srv.calls.filter((c) => c.includes('/files')).length, 0);
  srv.close();
  fs.rmSync(root, { recursive: true, force: true });
});

test('a file that cannot be read stops the sync: nothing is sent and the stored copy stays', async () => {
  const srv = await fakeServer();
  const root = tmp();
  fs.writeFileSync(path.join(root, 'keep.txt'), 'v1');
  fs.writeFileSync(path.join(root, 'other.txt'), 'x');
  const client = new LocalRepoClient(srv.base, () => 'tok');
  const key = 'r'.repeat(16);
  await indexRepository(client, root, key, 'locked');
  fs.chmodSync(path.join(root, 'keep.txt'), 0o000);
  const before = srv.calls.length;
  await assert.rejects(() => indexRepository(client, root, key, 'locked'), /keep\.txt/);
  assert.equal(srv.calls.slice(before).filter((c) => c.includes('/manifest') || c.includes('/commit')).length, 0);
  assert.ok(srv.repos.get(key)!.files.has('keep.txt'));
  fs.chmodSync(path.join(root, 'keep.txt'), 0o644);
  srv.close();
  fs.rmSync(root, { recursive: true, force: true });
});

test('a git repository whose git command fails is refused, and nothing is uploaded', async () => {
  const srv = await fakeServer();
  const root = tmp();
  execFileSync('git', ['init', '-q'], { cwd: root });
  fs.writeFileSync(path.join(root, '.gitignore'), '.env\n');
  fs.writeFileSync(path.join(root, '.env'), 'SECRET=1');
  fs.writeFileSync(path.join(root, 'a.ts'), 'a');
  const bin = tmp();
  fs.writeFileSync(path.join(bin, 'git'), '#!/bin/sh\necho "fatal: detected dubious ownership in repository" >&2\nexit 128\n', { mode: 0o755 });
  const client = new LocalRepoClient(srv.base, () => 'tok');
  await assert.rejects(() => indexRepository(client, root, 'g'.repeat(16), 'g', { git: path.join(bin, 'git') }), /git/);
  await assert.rejects(() => indexRepository(client, root, 'h'.repeat(16), 'h', { git: null }), /git/);
  assert.equal(srv.calls.filter((c) => c.includes('/manifest') || c.includes('/files')).length, 0);
  srv.close();
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(bin, { recursive: true, force: true });
});

test('a plain folder follows its .gitignore files and never sends likely secrets', async () => {
  const srv = await fakeServer();
  const root = tmp();
  fs.mkdirSync(path.join(root, 'src/gen'), { recursive: true });
  fs.writeFileSync(path.join(root, '.gitignore'), '# build output\n*.log\n/out.txt\n');
  fs.writeFileSync(path.join(root, 'src/.gitignore'), 'gen/\n');
  for (const [f, c] of [['a.ts', 'a'], ['run.log', 'l'], ['out.txt', 'o'], ['src/b.ts', 'b'], ['src/out.txt', 'kept'], ['src/gen/c.ts', 'c'],
    ['.env', 'S=1'], ['.env.local', 'S=2'], ['server.pem', 'k'], ['id_rsa', 'k'], ['credentials.json', '{}']]) {
    fs.writeFileSync(path.join(root, f), c);
  }
  const client = new LocalRepoClient(srv.base, () => 'tok');
  const key = 'p'.repeat(16);
  await indexRepository(client, root, key, 'plain', { git: null });
  assert.deepEqual([...srv.repos.get(key)!.files.keys()].sort(), ['.gitignore', 'a.ts', 'src/.gitignore', 'src/b.ts', 'src/out.txt']);
  srv.close();
  fs.rmSync(root, { recursive: true, force: true });
});

test('the total size limit refuses a folder before anything is read past it, and missing limits are not unlimited', async () => {
  const srv = await fakeServer({ maxFiles: 100, maxFileBytes: 1000, maxBatchFiles: 10, maxBatchBytes: 10000, maxManifestEntries: 100, maxTotalBytes: 5 } as never);
  const root = tmp();
  fs.writeFileSync(path.join(root, 'a.txt'), 'aaaa');
  fs.writeFileSync(path.join(root, 'b.txt'), 'bbbb');
  const client = new LocalRepoClient(srv.base, () => 'tok');
  await assert.rejects(() => indexRepository(client, root, 't'.repeat(16), 'total', { git: null }), (e: LocalRepoApiError) => e.status === 413);
  assert.equal(srv.calls.filter((c) => c.includes('/manifest')).length, 0);
  srv.close();
  const bare = await fakeServer({} as never);
  fs.writeFileSync(path.join(root, 'big.txt'), 'x'.repeat(2 * 1024 * 1024));
  const key = 'u'.repeat(16);
  await indexRepository(new LocalRepoClient(bare.base, () => 'tok'), root, key, 'bare', { git: null });
  assert.ok(!bare.repos.get(key)!.files.has('big.txt'), 'a file over the default 1 MiB limit is skipped');
  bare.close();
  fs.rmSync(root, { recursive: true, force: true });
});

test('store: a folder keeps its key across restarts, nothing is written into the folder', () => {
  const dir = tmp();
  const root = tmp();
  const file = path.join(dir, 'local-repos.json');
  const a = new LocalRepoStore(file).ensure(root);
  const b = new LocalRepoStore(file).ensure(root);
  assert.equal(a.repoKey, b.repoKey);
  assert.match(a.repoKey, /^[0-9a-f]{32}$/);
  assert.deepEqual(fs.readdirSync(root), []);
  const store = new LocalRepoStore(file);
  store.remove(a.repoKey);
  assert.deepEqual(new LocalRepoStore(file).list(), []);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.rmSync(root, { recursive: true, force: true });
});
