/**
 * SLASH2 `/review` (C3) and `/init` (C4): PipesHub's own prompt commands,
 * expanded on the chat routes before routing like a SLASH1 command, so the
 * turn's text is the same whichever executor runs it and no `/x` reaches one
 * (S0). `/review` carries the chat's workspace diff; `/init` applies only in a
 * saved agent conversation. Neither ever reaches the skills service.
 */
import 'reflect-metadata';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { expect } from 'chai';
import type { Request, Response } from 'express';
import {
  INIT_BODY,
  pipeshubPromptCommands,
  REVIEW_BODY,
  reviewText,
  withArgs,
} from '../../../src/modules/agent-harness/application/pipeshub-prompt-commands';
import { WorkspaceDiff } from '../../../src/modules/agent-harness/application/session-workspace-service';
import { CHAT_QUERY_MAX_CHARS } from '../../../src/modules/agent-harness/domain/admission';
import {
  COMMAND_LOCAL,
  keepsTypedOnly,
  storedQuestion,
  workerCommand,
  type RunCommand,
} from '../../../src/modules/agent-harness/domain/commands';
import { buildUserQueryMessage } from '../../../src/modules/enterprise_search/utils/utils';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { WorkspaceError } from '../../../src/modules/agent-harness/domain/workspace';
import {
  expandChatCommand,
  pipeshubCommandsFor,
  regenerateChatCommand,
  regenerateQuery,
  type Expansion,
} from '../../../src/modules/enterprise_search/utils/chat-command';
import { ORG, OWNER, workspaceWorld } from './support/slash2-workspace-world';

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/command-expansion.json'), 'utf8'),
) as { cases: Array<{ id: string; body: string; args: string; expected: string }> };

const sha = (text: string) => `sha256:${createHash('sha256').update(text, 'utf8').digest('hex')}`;

const DIFF: WorkspaceDiff = {
  workspaceId: 'ws:s1',
  head: 3,
  from: 0,
  to: 3,
  files: [
    { path: 'a.txt', change: 'added', sizeBytes: 4, additions: 1, deletions: 0, diff: '--- /dev/null\n+++ b/a.txt\n@@ -0,0 +1 @@\n+one\n' },
    { path: 'img.png', change: 'added', sizeBytes: 3, additions: 0, deletions: 0, binary: true },
  ],
  additions: 1,
  deletions: 0,
  omitted: 0,
};

type Service = { diff: (scope: unknown, which: string) => Promise<WorkspaceDiff> };

async function send(
  body: Record<string, unknown>,
  params: Record<string, string>,
  service: Service | undefined,
): Promise<{ body: Record<string, unknown>; locals: Record<string, unknown>; error: unknown; skills: string[]; scopes: unknown[] }> {
  const skills: string[] = [];
  const scopes: unknown[] = [];
  const wrapped = service && {
    diff: (scope: unknown, which: string) => {
      scopes.push([scope, which]);
      return service.diff(scope, which);
    },
  };
  const handler = expandChatCommand(
    async (_req, name): Promise<Expansion> => {
      skills.push(name);
      return { kind: 'not_found' };
    },
    pipeshubCommandsFor(pipeshubPromptCommands(() => wrapped as never)),
  );
  const req = { body, params, headers: {}, user: { orgId: ORG, userId: OWNER } } as unknown as Request;
  const res = { locals: {} } as unknown as Response;
  const error = await new Promise<unknown>((resolve) => {
    void handler(req, res, resolve);
  });
  return { body: req.body as Record<string, unknown>, locals: res.locals, error, skills, scopes };
}

const has = (diff: WorkspaceDiff): Service => ({ diff: async () => diff });
const status = (error: unknown) => (error as { statusCode?: number }).statusCode;
const code = (error: unknown) => (error as { metadata?: { code?: string } }).metadata?.code;

describe('SLASH2 C3/C4: the shared vector holds Node’s bodies', () => {
  for (const [id, body] of [['pipeshub-review', REVIEW_BODY], ['pipeshub-init', INIT_BODY]] as const) {
    it(`${id}: Node's body and expansion are the vector's (Python and TypeScript read the same case)`, () => {
      const vector = VECTOR.cases.find((c) => c.id === id);
      expect(vector?.body).to.equal(body);
      expect(withArgs(body, vector?.args ?? '')).to.equal(vector?.expected);
    });
  }
});

describe('SLASH2 C3: /review', () => {
  it('becomes the review body, the focus and the chat’s diff; recorded as PipesHub’s, never sent to the skills service', async () => {
    const out = await send({ query: '/review the SQL', chatMode: 'agent' }, { conversationId: 's1' }, has(DIFF));
    expect(out.error).to.equal(undefined);
    expect(out.skills).to.deep.equal([]);
    expect(out.scopes).to.deep.equal([[{ orgId: ORG, actorId: OWNER, sessionId: 's1' }, 'chat']]);
    const expected =
      `${REVIEW_BODY}\n\nthe SQL\n\n2 files changed since this chat began, +1 −0.\n\n` +
      '```diff\n--- /dev/null\n+++ b/a.txt\n@@ -0,0 +1 @@\n+one\nadded img.png (binary)\n```';
    expect(out.body).to.deep.equal({ query: expected, chatMode: 'agent' });
    expect(out.locals[COMMAND_LOCAL]).to.deep.equal({
      name: 'review',
      version: '1.0.0',
      contentDigest: sha(REVIEW_BODY),
      args: { focus: 'the SQL' },
      typed: '/review the SQL',
      owner: 'pipeshub',
      files: 2,
    } satisfies RunCommand);
  });

  it('an agent conversation passes its agent; the structured API form expands the same way', async () => {
    const out = await send({ command: { name: 'review' } }, { agentKey: 'agent-1', conversationId: 's1' }, has(DIFF));
    expect(out.error).to.equal(undefined);
    expect(out.scopes[0]).to.deep.equal([{ orgId: ORG, actorId: OWNER, sessionId: 's1', agentKey: 'agent-1' }, 'chat']);
    expect((out.body.query as string).startsWith(`${REVIEW_BODY}\n\n2 files changed`)).to.equal(true);
  });

  it('refused with a reason, never sent as text: no workspace, no changes, a new chat, not the owner, a destroyed key', async () => {
    const empty = { ...DIFF, files: [], additions: 0 };
    const cases: Array<[string, Record<string, string>, Service | undefined, string]> = [
      ['no changes', { conversationId: 's1' }, has(empty), "This chat's workspace has no changes to review."],
      ['new chat', {}, has(DIFF), 'This chat has no workspace yet.'],
      ['no workspace service', { conversationId: 's1' }, undefined, 'This chat has no workspace yet.'],
      ['not the owner', { conversationId: 's1' }, { diff: async () => { throw new HarnessNotFoundError(); } }, 'This chat has no workspace yet.'],
      ['key destroyed', { conversationId: 's1' }, { diff: async () => { throw new WorkspaceError('WORKSPACE_KEY_DESTROYED', 'The workspace key of this session was destroyed'); } }, 'The workspace key of this session was destroyed'],
    ];
    for (const [name, params, service, message] of cases) {
      const out = await send({ query: '/review' }, params, service);
      expect(status(out.error), name).to.equal(400);
      expect(code(out.error), name).to.equal('command_not_applicable');
      expect((out.error as Error).message, name).to.equal(message);
      expect(out.body.query, name).to.equal('/review');
      expect(out.locals[COMMAND_LOCAL], name).to.equal(undefined);
    }
  });

  it('a huge diff is cut to fit one message, and says so', () => {
    const big = { ...DIFF, files: [{ ...DIFF.files[0]!, diff: 'x'.repeat(CHAT_QUERY_MAX_CHARS * 2) }] };
    const text = reviewText('', big);
    expect(text.length).to.be.at.most(CHAT_QUERY_MAX_CHARS);
    expect(text).to.contain('[The diff was cut here to fit one message.]');
    expect(text.endsWith('```')).to.equal(true);
  });

  it('expands from the real workspace: the same text whichever run wrote the files', async () => {
    const w = workspaceWorld();
    await w.write(w.run('run-a'), { 'a.txt': 'one\n' });
    const out = await send({ query: '/review' }, { conversationId: 's1' }, w.owner);
    expect(out.body.query).to.equal(
      `${REVIEW_BODY}\n\n1 file changed since this chat began, +1 −0.\n\n` +
        '```diff\n--- /dev/null\n+++ b/a.txt\n@@ -0,0 +1 @@\n+one\n```',
    );
  });
});

describe('SLASH2 C4: /init', () => {
  it('in a saved agent conversation: the drafting body, recorded as PipesHub’s; it reads no workspace', async () => {
    const out = await send({ query: '/init' }, { agentKey: 'agent-1', conversationId: 's1' }, has(DIFF));
    expect(out.error).to.equal(undefined);
    expect(out.scopes).to.deep.equal([]);
    expect(out.skills).to.deep.equal([]);
    expect(out.body.query).to.equal(INIT_BODY);
    expect(out.locals[COMMAND_LOCAL]).to.deep.equal({
      name: 'init',
      version: '1.0.0',
      contentDigest: sha(INIT_BODY),
      args: {},
      typed: '/init',
      owner: 'pipeshub',
    } satisfies RunCommand);
  });

  it('refused outside an agent conversation or before it is saved', async () => {
    for (const params of [{ conversationId: 's1' }, { agentKey: 'agent-1' }, {}]) {
      const out = await send({ query: '/init' }, params, has(DIFF));
      expect(code(out.error), JSON.stringify(params)).to.equal('command_not_applicable');
      expect(out.body.query).to.equal('/init');
    }
  });

  it('any other name still goes to the skills service (SLASH1 unchanged)', async () => {
    const out = await send({ query: '/summarize this' }, { conversationId: 's1' }, has(DIFF));
    expect(out.skills).to.deep.equal(['summarize']);
    expect(out.body.query).to.equal('/summarize this');
  });
});

describe('LC2FIX: a /review message keeps only what was typed', () => {
  const MARKER = 'hidden=QZXV';
  const SECRET: WorkspaceDiff = {
    ...DIFF,
    files: [{ path: 'data/secret.txt', change: 'added', sizeBytes: 12, additions: 1, deletions: 0, diff: `--- /dev/null\n+++ b/data/secret.txt\n@@ -0,0 +1 @@\n+${MARKER}\n` }],
  };

  it('the turn sends the diff; the stored message holds "/review focus" and the file count, never workspace content', async () => {
    const out = await send({ query: '/review the SQL' }, { conversationId: 's1' }, has(SECRET));
    expect(out.body.query).to.contain(MARKER);
    const command = out.locals[COMMAND_LOCAL] as RunCommand;
    expect(command.files).to.equal(1);
    const message = buildUserQueryMessage(out.body.query as string, undefined, 'agent', undefined, command);
    expect(message.content).to.equal('/review the SQL');
    expect(JSON.stringify(message)).not.to.contain(MARKER);
    expect(JSON.stringify(message)).not.to.contain('secret.txt');
    // The count is display data: the worker's command stays S1's fields.
    expect(workerCommand(command)).not.to.have.property('files');
  });

  it('/init and a SLASH1 command keep the text sent ("What was sent"): a pure function of the body and the typed arguments', async () => {
    const init = await send({ query: '/init' }, { agentKey: 'agent-1', conversationId: 's1' }, has(SECRET));
    const initCommand = init.locals[COMMAND_LOCAL] as RunCommand;
    expect(buildUserQueryMessage(init.body.query as string, undefined, 'agent', undefined, initCommand).content).to.equal(INIT_BODY);
    const user: RunCommand = { name: 'summarize', version: '1', contentDigest: sha('x'), args: {}, typed: '/summarize', owner: 'you' };
    expect(storedQuestion('Summarize this.', user)).to.equal('Summarize this.');
    expect(keepsTypedOnly({ ...user, name: 'review' })).to.equal(false);
  });

  const regenerate = async (stored: RunCommand | undefined, service: Service | undefined) => {
    const handler = regenerateChatCommand(
      async () => stored,
      pipeshubCommandsFor(pipeshubPromptCommands(() => service as never)),
    );
    const req = { body: {}, params: { conversationId: 's1', messageId: 'm1' }, headers: {}, user: { orgId: ORG, userId: OWNER } } as unknown as Request;
    const res = { locals: {} } as unknown as Response;
    const error = await new Promise<unknown>((resolve) => {
    void handler(req, res, resolve);
  });
    return { error, query: regenerateQuery(res) };
  };
  const REVIEW: RunCommand = { name: 'review', version: '1.0.0', contentDigest: sha(REVIEW_BODY), args: { focus: 'the SQL' }, typed: '/review the SQL', owner: 'pipeshub' };

  it('regenerating a /review answer expands it again from the workspace as it is now; anything else is left alone', async () => {
    const fresh = await regenerate(REVIEW, has(SECRET));
    expect(fresh.error).to.equal(undefined);
    expect(fresh.query).to.equal(reviewText('the SQL', SECRET));
    for (const stored of [undefined, { ...REVIEW, owner: 'you' as const }, { ...REVIEW, name: 'init' }]) {
      const out = await regenerate(stored, has(SECRET));
      expect(out.error).to.equal(undefined);
      expect(out.query).to.equal(undefined);
    }
  });

  it('regenerating a /review with nothing left to review is refused before anything is removed', async () => {
    const out = await regenerate(REVIEW, has({ ...SECRET, files: [] }));
    expect(status(out.error)).to.equal(400);
    expect(code(out.error)).to.equal('command_not_applicable');
    expect(out.query).to.equal(undefined);
  });
});
