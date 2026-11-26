/**
 * SLASH1: a /command is expanded on the chat routes before routing, so every executor and legacy receive the same
 * text; the turn records which command produced it, Node journals `x.pipeshub.command.expanded` with run.admitted,
 * and the timeline's first row and the activity view name it.
 */
import 'reflect-metadata';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { expect } from 'chai';
import type { Request, Response } from 'express';
import { COMMAND_LOCAL, commandExpandedEvent, type RunCommand, workerCommand } from '../../../src/modules/agent-harness/domain/commands';
import { rootRunDocument, type AdmitRunCommand } from '../../../src/modules/agent-harness/domain/admission';
import type { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { admittedJournalAppend } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import { foldActivity } from '../../../src/modules/agent-harness/projection/activity';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { chatTurnInput } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import {
  argsText,
  expandChatCommand,
  parseTypedCommand,
  type Expansion,
} from '../../../src/modules/enterprise_search/utils/chat-command';
import { without } from './support/without';

const VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/command-expansion.json'), 'utf8'),
) as { typed: Array<{ text: string; expected: { name: string; args: string } | null }> };

const DIGEST = `sha256:${'a'.repeat(64)}`;
const TRIAGE: RunCommand = {
  name: 'triage',
  version: '1.0.3',
  contentDigest: DIGEST,
  args: { incident: 'INC-1', depth: 'quick' },
  typed: '/triage INC-1',
  owner: 'you',
};
const EXPANDED = 'Look up incident INC-1 in Jira. Depth: quick.';

interface Call {
  name: string;
  args: string;
}

async function run(
  body: Record<string, unknown>,
  result: Expansion,
): Promise<{ body: Record<string, unknown>; locals: Record<string, unknown>; error: unknown; calls: Call[] }> {
  const calls: Call[] = [];
  const handler = expandChatCommand(async (_req, name, args) => {
    calls.push({ name, args });
    return result;
  });
  const req = { body, headers: {}, user: { orgId: 'org-1', userId: 'user-1' } } as unknown as Request;
  const res = { locals: {} } as unknown as Response;
  const error = await new Promise<unknown>((resolve) => {
    void handler(req, res, resolve);
  });
  return { body: req.body as Record<string, unknown>, locals: res.locals, error, calls };
}

const expanded: Expansion = {
  kind: 'expanded',
  version: TRIAGE.version,
  contentDigest: DIGEST,
  owner: 'you',
  args: TRIAGE.args,
  expanded: EXPANDED,
};

describe('SLASH1: commands on the chat routes', () => {
  describe('the typed form', () => {
    for (const vector of VECTORS.typed) {
      it(`parses ${JSON.stringify(vector.text)} as the shared vector says`, () => {
        expect(parseTypedCommand(vector.text)).to.deep.equal(vector.expected);
      });
    }

    it('an API client\'s named arguments become the text a person would type', () => {
      expect(argsText({ incident: 'INC-1', note: 'two "words"' })).to.equal('incident=INC-1 note="two \\"words\\""');
      expect(argsText(' INC-1 full ')).to.equal('INC-1 full');
      expect(argsText(undefined)).to.equal('');
    });
  });

  describe('expansion before routing', () => {
    it('a typed command becomes the query; the record goes to res.locals, never the body', async () => {
      const out = await run({ query: '/triage INC-1', chatMode: 'agent' }, expanded);
      expect(out.error).to.equal(undefined);
      expect(out.calls).to.deep.equal([{ name: 'triage', args: 'INC-1' }]);
      expect(out.body).to.deep.equal({ query: EXPANDED, chatMode: 'agent' });
      expect(out.locals[COMMAND_LOCAL]).to.deep.equal(TRIAGE);
    });

    it('a structured command (API/PAT) with no query is expanded the same way', async () => {
      const out = await run({ command: { name: 'triage', args: { incident: 'INC-1' } }, chatMode: 'agent' }, expanded);
      expect(out.error).to.equal(undefined);
      expect(out.calls).to.deep.equal([{ name: 'triage', args: 'incident=INC-1' }]);
      expect(out.body).to.deep.equal({ query: EXPANDED, chatMode: 'agent' });
      expect((out.locals[COMMAND_LOCAL] as RunCommand).typed).to.equal('/triage incident=INC-1');
    });

    it('a team command records its author and team as ids, never names (S4); a former member has no id', async () => {
      const team = await run(
        { query: '/triage INC-1', chatMode: 'agent' },
        { ...expanded, owner: 'team', authorId: 'u-asha', teamId: 'ops', authorFormer: false },
      );
      expect(team.locals[COMMAND_LOCAL]).to.deep.equal({ ...TRIAGE, owner: 'team', authorId: 'u-asha', teamId: 'ops' });
      const left = await run(
        { query: '/triage INC-1', chatMode: 'agent' },
        { ...expanded, owner: 'org', authorId: null, teamId: null, authorFormer: true },
      );
      expect(left.locals[COMMAND_LOCAL]).to.deep.equal({ ...TRIAGE, owner: 'org', authorFormer: true });
    });

    it('a worker built before S4 gets the command it knows: the run input drops who wrote it', () => {
      const shared: RunCommand = { ...TRIAGE, owner: 'team', authorId: 'u-asha', teamId: 'ops', authorFormer: false };
      const input = chatTurnInput({ query: EXPANDED, chatMode: 'agent' }, 'chat', 'send', shared) as RunInputSnapshot;
      expect(input.command).to.deep.equal(shared);
      const run = rootRunDocument({ runId: 'r1', orgId: 'o1', actorId: 'u1' } as AdmitRunCommand, {
        sessionId: 's1',
        userMessageKey: 'm1',
        idempotencyKey: 'k1',
        absoluteDeadlineAt: 1,
        input,
        now: 0,
      });
      // S1's strict worker model (`app/harness/contracts/state.py` RunCommand, extra=forbid) accepts exactly these.
      expect(Object.keys(run.input?.command ?? {}).sort()).to.deep.equal(['args', 'contentDigest', 'name', 'owner', 'typed', 'version']);
      expect(workerCommand(shared)).to.deep.equal({ ...TRIAGE, owner: 'team' });
      // Nor does the journal name them: only the stored message keeps the ids, and it goes with its session (E7).
      expect(Object.keys(commandExpandedEvent(shared, EXPANDED).data)).to.not.include.members(['authorId', 'teamId']);
    });

    it('text that is not a command is never sent to be expanded', async () => {
      for (const query of ['hello /triage', '/etc/hosts is broken', 'plain']) {
        const out = await run({ query }, expanded);
        expect(out.calls, query).to.deep.equal([]);
        expect(out.body.query).to.equal(query);
      }
    });

    it('a typed name that is none of the caller\'s commands stays plain text', async () => {
      const out = await run({ query: '/compact now' }, { kind: 'not_found' });
      expect(out.error).to.equal(undefined);
      expect(out.body.query).to.equal('/compact now');
      expect(out.locals[COMMAND_LOCAL]).to.equal(undefined);
    });

    it('a structured command that is not found is a 404 with its code', async () => {
      const out = await run({ command: { name: 'nope' } }, { kind: 'not_found' });
      expect(out.error).to.include({ statusCode: 404 });
      expect((out.error as { metadata: unknown }).metadata).to.deep.equal({ code: 'command_not_found', names: [] });
    });

    it('a missing argument is refused with the names at fault', async () => {
      const out = await run(
        { query: '/triage' },
        { kind: 'refused', code: 'command_argument_missing', message: 'Missing: incident.', names: ['incident'] },
      );
      expect(out.error).to.include({ statusCode: 400, message: 'Missing: incident.' });
      expect((out.error as { metadata: unknown }).metadata).to.deep.equal({
        code: 'command_argument_missing',
        names: ['incident'],
      });
    });

    it('a structured command whose query says something else is refused', async () => {
      const out = await run({ query: 'other text', command: { name: 'triage', args: 'INC-1' } }, expanded);
      expect(out.error).to.include({ statusCode: 400 });
      expect(out.calls).to.deep.equal([]);
    });

    it('without skill:read an API command is a 403 with its code, and typed text goes on as text', async () => {
      const structured = await run({ command: { name: 'triage' } }, { kind: 'forbidden' });
      expect(structured.error).to.include({ statusCode: 403, code: 'HTTP_COMMAND_FORBIDDEN' });
      expect((structured.error as { metadata: unknown }).metadata).to.deep.equal({
        code: 'command_forbidden',
        names: [],
      });
      const typed = await run({ query: '/triage INC-1' }, { kind: 'forbidden' });
      expect(typed.error).to.equal(undefined);
      expect(typed.body.query).to.equal('/triage INC-1');
    });

    it('when the skills service cannot answer, typed text goes on as text and an API command fails', async () => {
      const typed = await run({ query: '/triage INC-1' }, { kind: 'unavailable' });
      expect(typed.error).to.equal(undefined);
      expect(typed.body.query).to.equal('/triage INC-1');
      const structured = await run({ command: { name: 'triage' } }, { kind: 'unavailable' });
      expect(structured.error).to.include({ statusCode: 503 });
    });
  });

  describe('the run input', () => {
    const body = { query: EXPANDED, chatMode: 'agent' };

    it('carries the expanded command on a send (the body never does: a body with `command` is not a run input)', () => {
      expect(chatTurnInput(body, 'chat', 'send', TRIAGE)).to.deep.include({ query: EXPANDED, command: TRIAGE });
      expect(chatTurnInput({ ...body, command: TRIAGE }, 'chat')).to.equal(null);
    });

    it('a regenerate asks the stored question and its stored command, not one the request names', () => {
      expect(chatTurnInput({ ...body, query: '' }, 'chat', 'regenerate', TRIAGE)).to.not.have.property('command');
    });
  });

  describe('the journal, the timeline and the activity view', () => {
    const run = {
      orgId: 'org-1',
      sessionId: 'ses-1',
      runId: 'run-1',
      actorId: 'user-1',
      input: {
        query: EXPANDED,
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'agent' },
        command: TRIAGE,
      },
    };

    it('Node journals command.expanded with run.admitted, naming the text every executor gets', () => {
      const append = admittedJournalAppend(run, 'session');
      expect(append.events.map((event) => event.type)).to.deep.equal(['run.admitted', 'x.pipeshub.command.expanded']);
      expect(append.events[1]!.data).to.deep.equal({
        name: 'triage',
        version: '1.0.3',
        contentDigest: DIGEST,
        args: TRIAGE.args,
        expandedDigest: `sha256:${createHash('sha256').update(EXPANDED, 'utf8').digest('hex')}`,
        expandedChars: EXPANDED.length,
        owner: 'you',
      });
    });

    it('only a worker that reads input.command claims such a run', () => {
      expect(admissionRequirements({ agentKey: 'a1', input: run.input }).features).to.deep.equal(['command_input']);
    });

    it('a run typed as text journals no command', () => {
      const plain = without(run.input, 'command');
      const append = admittedJournalAppend({ ...run, input: plain }, 'session');
      expect(append.events.map((event) => event.type)).to.deep.equal(['run.admitted']);
    });

    it('the first saved part and the activity view name the command', () => {
      const events = admittedJournalAppend(run, 'session').events.map(
        (event, index) =>
          ({
            ...event,
            orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: index + 1, runId: 'run-1',
            ts: '2026-10-16T12:00:00.000Z', writer: 'node',
          }) as JournalEvent,
      );
      const timeline = foldTimeline(events, 'run-1');
      expect(buildParts(timeline)[0]).to.deep.equal({
        type: 'slash_command',
        commandName: 'triage',
        commandVersion: '1.0.3',
        expandedChars: EXPANDED.length,
        owner: 'you',
        runId: 'run-1',
      });
      expect(foldActivity(events, { runId: 'run-1' } as never).command).to.deep.equal({
        name: 'triage',
        version: '1.0.3',
        expandedChars: EXPANDED.length,
        owner: 'you',
      });
    });
  });
});
