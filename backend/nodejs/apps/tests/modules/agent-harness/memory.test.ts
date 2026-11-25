/** P7.3 DM-1..DM-6: the pure rules of governed memory. */
import { expect } from 'chai';
import {
  MAX_RECALL_CANDIDATES,
  memoryContent,
  memoryPin,
  memoryPolicyOf,
  MemoryPolicy,
  proposedMemoryId,
  recalledMemoryView,
  recallCandidates,
  settlement,
  writeScopes,
} from '../../../src/modules/agent-harness/domain/memory';
import { TurnLabel } from '../../../src/modules/agent-harness/domain/turn-labels';
import { memoryFixture } from './support/memory-store-contract';

const COMPLETE: TurnLabel = { complete: true, sources: [{ kind: 'indexed', recordId: 'r1' }], integrity: 'first_party' };
// SEC-B-05: outside content reached the turn, or a worker that predates integrity did not say.
const EXTERNAL: TurnLabel = { ...COMPLETE, integrity: 'external' };
const UNSAID: TurnLabel = { complete: true, sources: [{ kind: 'indexed', recordId: 'r1' }] };
const INCOMPLETE: TurnLabel = { complete: false, sources: [] };
const USER_AUTO: MemoryPolicy = { mode: 'user', review: 'auto' };
const AGENT_AUTO: MemoryPolicy = { mode: 'agent', review: 'auto' };

describe('P7.3 governed memory: rules (DM)', () => {
  describe('DM-1: the policy document', () => {
    const cases: Array<[string, unknown, MemoryPolicy]> = [
      ['absent', null, { mode: 'off', review: 'auto' }],
      ['not an object', 'user', { mode: 'off', review: 'auto' }],
      ['no orgs', {}, { mode: 'off', review: 'auto' }],
      ['another org only', { orgs: { o2: { mode: 'user', review: 'auto' } } }, { mode: 'off', review: 'auto' }],
      ['an unknown mode', { orgs: { o1: { mode: 'all', review: 'auto' } } }, { mode: 'off', review: 'auto' }],
      ['an unknown review', { orgs: { o1: { mode: 'user', review: 'never' } } }, { mode: 'off', review: 'auto' }],
      ['user, always', { orgs: { o1: { mode: 'user', review: 'always', updatedAt: 1, updatedBy: 'p_a' } } }, { mode: 'user', review: 'always' }],
      ['agent, auto', { orgs: { o1: { mode: 'agent', review: 'auto' } } }, { mode: 'agent', review: 'auto' }],
    ];
    for (const [name, document, expected] of cases) {
      it(`${name} → ${expected.mode}/${expected.review}`, () => {
        expect(memoryPolicyOf(document, 'o1')).to.deep.equal(expected);
      });
    }

    it('one org\'s bad entry does not hide another\'s', () => {
      const document = { orgs: { bad: { mode: 7 }, good: { mode: 'user', review: 'auto' } } };
      expect(memoryPolicyOf(document, 'good')).to.deep.equal(USER_AUTO);
      expect(memoryPolicyOf(document, 'bad').mode).to.equal('off');
    });

    it('an inherited property is not an org entry', () => {
      expect(memoryPolicyOf({ orgs: {} }, 'constructor').mode).to.equal('off');
    });
  });

  describe('DM-2: settlement at the run\'s end', () => {
    const table: Array<[string, Parameters<typeof settlement>, string]> = [
      ['user, complete, auto', ['user', COMPLETE, USER_AUTO], 'accepted'],
      ['session, complete, auto', ['session', COMPLETE, USER_AUTO], 'accepted'],
      ['user, incomplete', ['user', INCOMPLETE, USER_AUTO], 'proposed'],
      ['user, unknown (no label)', ['user', null, USER_AUTO], 'proposed'],
      ['user, complete, review always', ['user', COMPLETE, { mode: 'user', review: 'always' }], 'proposed'],
      ['agent, complete', ['agent', COMPLETE, AGENT_AUTO], 'proposed'],
      ['agent, incomplete', ['agent', INCOMPLETE, AGENT_AUTO], 'discarded'],
      ['agent, unknown', ['agent', null, AGENT_AUTO], 'discarded'],
      ['user, complete, auto, outside content', ['user', EXTERNAL, USER_AUTO], 'proposed'],
      ['session, complete, auto, outside content', ['session', EXTERNAL, USER_AUTO], 'proposed'],
      ['agent, complete, outside content', ['agent', EXTERNAL, AGENT_AUTO], 'proposed'],
      ['user, complete, auto, integrity unsaid', ['user', UNSAID, USER_AUTO], 'proposed'],
    ];
    for (const [name, args, expected] of table) {
      it(`${name} → ${expected}`, () => {
        expect(settlement(...args)).to.equal(expected);
      });
    }
  });

  describe('DM-3: recall candidates', () => {
    const subject = { actorId: 'alice', agentKey: 'agent-1', sessionId: 'ses-1' };

    it('only accepted memories with a complete label that are the actor\'s or this agent\'s', () => {
      const mine = memoryFixture({ createdAt: 5 });
      const pending = memoryFixture({ status: 'pending', label: null });
      const proposed = memoryFixture({ status: 'proposed' });
      const unlabelled = memoryFixture({ label: INCOMPLETE });
      const bobs = memoryFixture({ ownerId: 'bob' });
      const mySession = memoryFixture({ scope: 'session', sessionId: 'ses-1', createdAt: 6 });
      const myOtherSession = memoryFixture({ scope: 'session', sessionId: 'ses-2' });
      const agent = memoryFixture({ scope: 'agent', agentKey: 'agent-1', ownerId: 'bob', createdAt: 7 });
      const otherAgent = memoryFixture({ scope: 'agent', agentKey: 'agent-2' });
      const all = [mine, pending, proposed, unlabelled, bobs, mySession, myOtherSession, agent, otherAgent];
      expect(recallCandidates(all, subject, AGENT_AUTO).map((m) => m.memoryId)).to.deep.equal([
        agent.memoryId,
        mySession.memoryId,
        mine.memoryId,
      ]);
      expect(recallCandidates(all, subject, USER_AUTO).map((m) => m.memoryId)).to.deep.equal([
        mySession.memoryId,
        mine.memoryId,
      ]);
      expect(recallCandidates(all, subject, { mode: 'off', review: 'auto' })).to.deep.equal([]);
      expect(recallCandidates(all, { ...subject, agentKey: null }, AGENT_AUTO).map((m) => m.memoryId)).to.not.include(
        agent.memoryId,
      );
      expect(recallCandidates(all, { ...subject, sessionId: null }, USER_AUTO).map((m) => m.memoryId)).to.deep.equal([
        mine.memoryId,
      ]);
    });

    it('newest first, at most the candidate bound', () => {
      const many = Array.from({ length: MAX_RECALL_CANDIDATES + 5 }, (_, i) => memoryFixture({ createdAt: i }));
      const picked = recallCandidates(many, subject, USER_AUTO);
      expect(picked).to.have.length(MAX_RECALL_CANDIDATES);
      expect(picked[0]?.createdAt).to.equal(MAX_RECALL_CANDIDATES + 4);
    });
  });

  describe('DM-4: the pin for the run\'s context', () => {
    const admission = {
      recalled: [
        { memoryId: 'm-user', scope: 'user' as const },
        { memoryId: 'm-session', scope: 'session' as const },
        { memoryId: 'm-agent', scope: 'agent' as const },
      ],
      writeScopes: ['user' as const, 'agent' as const],
    };

    it('personal: unchanged', () => {
      expect(memoryPin(admission, false)).to.deep.equal({
        recalled: ['m-user', 'm-session', 'm-agent'],
        writeScopes: ['user', 'agent'],
      });
    });

    it('shared: personal memories stay out and personal writes go to the conversation', () => {
      expect(memoryPin(admission, true)).to.deep.equal({
        recalled: ['m-session', 'm-agent'],
        writeScopes: ['session', 'agent'],
      });
    });

    it('nothing to pin → none', () => {
      expect(memoryPin(undefined, false)).to.equal(null);
      expect(memoryPin({ recalled: [], writeScopes: [] }, false)).to.equal(null);
      expect(memoryPin({ recalled: [{ memoryId: 'm', scope: 'user' }], writeScopes: [] }, true)).to.equal(null);
    });

    it('at most 20 pinned', () => {
      const many = Array.from({ length: 30 }, (_, i) => ({ memoryId: `m${i}`, scope: 'user' as const }));
      expect(memoryPin({ recalled: many, writeScopes: [] }, false)?.recalled).to.have.length(20);
    });

    it('write scopes by policy', () => {
      expect(writeScopes({ mode: 'off', review: 'auto' }, 'a')).to.deep.equal([]);
      expect(writeScopes(USER_AUTO, 'a')).to.deep.equal(['user', 'session']);
      expect(writeScopes(AGENT_AUTO, 'a')).to.deep.equal(['user', 'session', 'agent']);
      expect(writeScopes(AGENT_AUTO, null)).to.deep.equal(['user', 'session']);
    });

    it('R49PLAN-2: a person\'s own chat may keep a memory for this conversation alone', () => {
      const pin = memoryPin({ recalled: [], writeScopes: writeScopes(USER_AUTO, null) }, false);
      expect(pin?.writeScopes).to.deep.equal(['user', 'session']);
      // A shared context still sends personal writes to the conversation, once.
      const shared = memoryPin({ recalled: [], writeScopes: writeScopes(USER_AUTO, null) }, true);
      expect(shared?.writeScopes).to.deep.equal(['session']);
    });
  });

  describe('DM-5: content', () => {
    it('keeps trimmed text up to 500 characters', () => {
      expect(memoryContent('  Prefers tables.  ')).to.equal('Prefers tables.');
      expect(memoryContent('a'.repeat(500))).to.have.length(500);
      expect(memoryContent('line one\nline two\ttabbed')).to.equal('line one\nline two\ttabbed');
    });
    for (const [name, value] of [
      ['empty', '   '],
      ['too long', 'a'.repeat(501)],
      ['a control character', 'bell\u0007'],
      ['not text', 42],
    ] as const) {
      it(`refuses ${name}`, () => {
        expect(memoryContent(value)).to.equal(null);
      });
    }
  });

  describe('DM-6: a proposal\'s id', () => {
    const base = { orgId: 'o', runId: 'r', scope: 'user' as const, kind: 'fact' as const, content: 'x' };
    it('is the same for the same proposal and differs for any change', () => {
      expect(proposedMemoryId(base)).to.equal(proposedMemoryId({ ...base }));
      expect(proposedMemoryId(base)).to.match(/^mem_[0-9a-f]{40}$/);
      for (const change of [{ orgId: 'o2' }, { runId: 'r2' }, { scope: 'session' as const }, { kind: 'preference' as const }, { content: 'y' }]) {
        expect(proposedMemoryId({ ...base, ...change })).to.not.equal(proposedMemoryId(base));
      }
    });
  });

  describe('TL-1: the run\'s timeline shows what memory gave it', () => {
    it('ids, scopes and kinds of the run\'s last recall; never content or sources; null without one', () => {
      const event = (runId: string, memoryId: string) => ({
        type: 'x.pipeshub.memory.recalled',
        runId,
        data: {
          entries: [{ memoryId, scope: 'user', kind: 'fact', label: { complete: true, sources: [{ kind: 'indexed', recordId: 'r1' }] } }],
          chars: 120,
          truncated: true,
        },
      });
      const view = recalledMemoryView([event('run-1', 'm-old'), event('run-2', 'm-other'), event('run-1', 'm-new')], 'run-1');
      expect(view).to.deep.equal({ entries: [{ memoryId: 'm-new', scope: 'user', kind: 'fact' }], chars: 120, truncated: true });
      expect(JSON.stringify(view)).to.not.include('r1');
      expect(recalledMemoryView([event('run-2', 'm')], 'run-1')).to.equal(null);
    });
  });
});
