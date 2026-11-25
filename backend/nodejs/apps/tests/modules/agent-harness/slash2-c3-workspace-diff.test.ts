/**
 * SLASH2 C3 `/diff` and `/undo`, Node's half: the unified diff, the session
 * workspace's changes over the chat and over its last writing turn (read with
 * the session key, never leaving Node), and GET …/workspace/diff with the
 * caller and org taken from the credential (owner only: 404; a destroyed key:
 * 410). `/undo` is the existing restore route at the `from` the turn scope
 * names; it never touches the conversation.
 */
import 'reflect-metadata';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import {
  DIFF_MAX_FILE_BYTES,
  DIFF_MAX_LINES,
  unifiedDiff,
} from '../../../src/modules/agent-harness/domain/workspace-diff';
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
import { workspaceCode } from './support/workspace-repository-contract';
import { ORG, OWNER, scope, workspaceWorld } from './support/slash2-workspace-world';

describe('SLASH2 C3: the unified diff', () => {
  it('a changed line in the middle: three lines of context, `diff -u` headers and ranges', () => {
    const before = 'a\nb\nc\nd\ne\nf\ng\nh\n';
    const after = 'a\nb\nc\nd\nE\nf\ng\nh\n';
    expect(unifiedDiff('src/x.txt', before, after)).to.deep.equal({
      diff: '--- a/src/x.txt\n+++ b/src/x.txt\n@@ -2,7 +2,7 @@\n b\n c\n d\n-e\n+E\n f\n g\n h\n',
      additions: 1,
      deletions: 1,
      truncated: false,
    });
  });

  it('a new file and a deleted file read against /dev/null', () => {
    expect(unifiedDiff('n.md', null, 'one\ntwo\n').diff).to.equal('--- /dev/null\n+++ b/n.md\n@@ -0,0 +1,2 @@\n+one\n+two\n');
    expect(unifiedDiff('d.md', 'gone\n', null).diff).to.equal('--- a/d.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n');
  });

  it('two changes far apart are two hunks; close ones share one', () => {
    const lines = Array.from({ length: 30 }, (_, i) => `l${String(i)}`);
    const far = [...lines];
    far[2] = 'X';
    far[25] = 'Y';
    const two = unifiedDiff('f', `${lines.join('\n')}\n`, `${far.join('\n')}\n`).diff;
    expect(two.match(/^@@/gm)).to.have.length(2);
    expect(two).to.contain('@@ -1,6 +1,6 @@').and.contain('@@ -23,7 +23,7 @@');
    const near = [...lines];
    near[10] = 'X';
    near[16] = 'Y';
    expect(unifiedDiff('f', `${lines.join('\n')}\n`, `${near.join('\n')}\n`).diff.match(/^@@/gm)).to.have.length(1);
  });

  it('an insertion between unchanged lines keeps them as context', () => {
    expect(unifiedDiff('f', 'a\nc\n', 'a\nb\nc\n').diff).to.equal('--- a/f\n+++ b/f\n@@ -1,2 +1,3 @@\n a\n+b\n c\n');
  });

  it('a long diff is cut and marked', () => {
    const after = Array.from({ length: DIFF_MAX_LINES * 2 }, (_, i) => `x${String(i)}`).join('\n');
    const out = unifiedDiff('big', null, after);
    expect(out.truncated).to.equal(true);
    expect(out.additions).to.equal(DIFF_MAX_LINES * 2);
    expect(out.diff.split('\n')).to.have.length(DIFF_MAX_LINES + 1);
  });
});

describe('SLASH2 C3: the session workspace’s changes (service)', () => {
  it('chat scope: every path changed since the chat began, sorted, with counts and diffs', async () => {
    const w = workspaceWorld();
    const a = w.run('run-a');
    const b = w.run('run-b');
    await w.write(a, { 'out/policy.md': 'one\ntwo\n', 'notes/draft.md': 'x\n' });
    await w.write(b, { 'out/policy.md': 'one\nTWO\nthree\n', 'notes/draft.md': null, 'img.png': Buffer.from([0, 1, 2]) });
    const diff = await w.owner.diff(scope, 'chat');
    expect(diff).to.deep.include({ workspaceId: 'ws:s1', head: 2, from: 0, to: 2, omitted: 0, additions: 3, deletions: 0 });
    expect(diff.files.map((f) => [f.path, f.change])).to.deep.equal([
      ['img.png', 'added'],
      ['out/policy.md', 'added'],
    ]);
    expect(diff.files[0]).to.deep.include({ binary: true, sizeBytes: 3 });
    expect(diff.files[1]?.diff).to.equal('--- /dev/null\n+++ b/out/policy.md\n@@ -0,0 +1,3 @@\n+one\n+TWO\n+three\n');
  });

  it('turn scope: only the latest writing turn (all its commits), from the revision before it', async () => {
    const w = workspaceWorld();
    const a = w.run('run-a');
    const b = w.run('run-b');
    await w.write(a, { 'a.txt': 'one\n', 'b.txt': 'keep\n' });
    await w.write(b, { 'a.txt': 'one\nmore\n' }, 'autosave');
    await w.write(b, { 'c.txt': 'new\n' }, 'turn_end');
    const turn = await w.owner.diff(scope, 'turn');
    expect(turn).to.deep.include({ head: 3, from: 1, to: 3, additions: 2, deletions: 0 });
    expect(turn.files.map((f) => [f.path, f.change, f.additions, f.deletions])).to.deep.equal([
      ['a.txt', 'modified', 1, 0],
      ['c.txt', 'added', 1, 0],
    ]);
  });

  it('/undo restores `from` on top of the head; the next turn and /diff see the tree before that turn', async () => {
    const w = workspaceWorld();
    const a = w.run('run-a');
    const b = w.run('run-b');
    await w.write(a, { 'a.txt': 'one\n' });
    await w.write(b, { 'a.txt': 'changed\n', 'b.txt': 'new\n' });
    const turn = await w.owner.diff(scope, 'turn');
    const restored = await w.owner.restore(scope, { revision: turn.from, baseRevision: turn.head, idempotencyKey: 'undo-1-on-2' });
    expect(restored).to.deep.include({ revision: 3, restoredFrom: 1, replayed: false });
    expect(await w.tree(w.run('run-c'))).to.deep.equal({ 'a.txt': 'one\n' });
    // The restore wrote the head: nothing is left to undo, and the versions list says who restored.
    expect((await w.owner.diff(scope, 'turn')).files).to.deep.equal([]);
    expect((await w.owner.versions(scope, {})).versions[0]).to.include({ revision: 3, reason: 'rollback', restoredByUser: true });
    expect((await w.owner.diff(scope, 'chat')).files.map((f) => f.path)).to.deep.equal(['a.txt']);
  });

  it('no workspace yet: an empty answer at revision 0', async () => {
    const w = workspaceWorld();
    for (const which of ['chat', 'turn'] as const) {
      expect(await w.owner.diff(scope, which)).to.deep.equal({
        workspaceId: 'ws:s1', head: 0, from: 0, to: 0, files: [], additions: 0, deletions: 0, omitted: 0,
      });
    }
  });

  it('a file too large to read is listed and marked, never read', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'big.log': 'x'.repeat(DIFF_MAX_FILE_BYTES + 1) });
    const [big] = (await w.owner.diff(scope, 'chat')).files;
    expect(big).to.deep.equal({ path: 'big.log', change: 'added', sizeBytes: DIFF_MAX_FILE_BYTES + 1, additions: 0, deletions: 0, tooLarge: true });
  });

  it('only the owner: a shared viewer or a stranger gets 404', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'x\n' });
    for (const audience of ['shared', null] as const) {
      w.audience.value = audience;
      let refused: unknown;
      try {
        await w.owner.diff(scope, 'chat');
      } catch (error) {
        refused = error;
      }
      expect(refused, String(audience)).to.be.instanceOf(HarnessNotFoundError);
    }
  });

  it('a destroyed session key answers WORKSPACE_KEY_DESTROYED', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'x\n' });
    await w.keys.shred(ORG, 's1');
    expect(await workspaceCode(w.owner.diff(scope, 'chat'))).to.equal('WORKSPACE_KEY_DESTROYED');
  });
});

describe('SLASH2 C3: GET …/workspace/diff', () => {
  let server: Server;
  let base: string;
  const calls: unknown[][] = [];
  let originalLogger: unknown;
  const user = { orgId: ORG, userId: OWNER };

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { ...user };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_SESSION_WORKSPACES).toConstantValue({
      diff: async (s: { sessionId: string }, which: string) => {
        calls.push([s, which]);
        if (s.sessionId === 'missing') throw new HarnessNotFoundError();
        if (s.sessionId === 'shredded') {
          throw new WorkspaceError('WORKSPACE_KEY_DESTROYED', 'The workspace key of this session was destroyed');
        }
        return { workspaceId: `ws:${s.sessionId}`, head: 2, from: 0, to: 2, files: [], additions: 0, deletions: 0, omitted: 0 };
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

  it('answers on chat and agent sessions; org and caller come from the credential, the scope from the query', async () => {
    const chat = await fetch(`${base}/conversations/s1/workspace/diff`);
    expect(chat.status).to.equal(200);
    expect(await chat.json()).to.include({ workspaceId: 'ws:s1', head: 2 });
    expect(calls.at(-1)).to.deep.equal([{ orgId: ORG, actorId: OWNER, sessionId: 's1' }, 'chat']);
    const agent = await fetch(`${base}/agents/agent-1/conversations/s1/workspace/diff?scope=turn`);
    expect(agent.status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal([{ orgId: ORG, actorId: OWNER, sessionId: 's1', agentKey: 'agent-1' }, 'turn']);
  });

  it('an org or user named in the query is refused, never used', async () => {
    for (const query of ['orgId=org-2', 'scope=turn&actorId=user-2', 'scope=all', 'scope=']) {
      const bad = await fetch(`${base}/conversations/s1/workspace/diff?${query}`);
      expect(bad.status, query).to.equal(400);
    }
    expect(calls.every(([s]) => (s as { orgId: string }).orgId === ORG)).to.equal(true);
  });

  it('not the owner: 404; a destroyed key: 410 with its code', async () => {
    expect((await fetch(`${base}/conversations/missing/workspace/diff`)).status).to.equal(404);
    const gone = await fetch(`${base}/agents/agent-1/conversations/shredded/workspace/diff`);
    expect(gone.status).to.equal(410);
    expect(JSON.stringify(await gone.json())).to.contain('WORKSPACE_KEY_DESTROYED');
  });
});
