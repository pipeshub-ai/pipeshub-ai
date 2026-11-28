/**
 * UI-B1, ADR-UI-01 D1 and D2 (G1, G7, G11): the reading of a run as a list word, the page cursor, a person's runs with
 * the names a list shows, the run state of their chat list and their seen mark, on the in-memory stores.
 */
import { expect } from 'chai';
import { Types } from 'mongoose';
import { InMemoryConversationSeenMarks } from '../../../src/modules/agent-harness/adapters/in-memory-conversation-seen';
import { RunListService } from '../../../src/modules/agent-harness/application/run-lists';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  conversationRunStates,
  decodeRunListCursor,
  encodeRunListCursor,
  listedEngine,
  MAX_CONVERSATION_STATES,
  matchesPersonRunQuery,
  runListState,
} from '../../../src/modules/agent-harness/domain/run-lists';
import type { RunLists } from '../../../src/modules/agent-harness/ports/run-lists';
import { describeConversationSeenContract } from './support/conversation-seen-contract';

const run = (overrides: Partial<HarnessRun> = {}): HarnessRun =>
  ({
    runId: 'run-1',
    orgId: 'org-1',
    sessionId: 'ses-1',
    actorId: 'user-1',
    status: 'running',
    outcome: null,
    createdAt: 1_000,
    updatedAt: 1_000,
    ...overrides,
  }) as HarnessRun;

describe('UI-B1: run lists (ADR-UI-01 D1, D2)', () => {
  describe('the list word, the filter and the cursor', () => {
    it('UIB1-U1: one word per run: an open ask or an approval hold is "needs you", whatever else the run does', () => {
      expect(runListState(run({ status: 'queued' }))).to.equal('working');
      expect(runListState(run({ status: 'paused' }))).to.equal('working');
      expect(runListState(run({ status: 'cancelling' }))).to.equal('working');
      expect(runListState(run({ status: 'running', openInteractions: ['ix.approve.t1'] }))).to.equal('needs_you');
      expect(runListState(run({ status: 'waiting_approval' }))).to.equal('needs_you');
      expect(runListState(run({ status: 'completed', openInteractions: ['ix.approve.t1'] }))).to.equal('finished');
      expect(runListState(run({ status: 'failed' }))).to.equal('failed');
      expect(runListState(run({ status: 'cancelled' }))).to.equal('stopped');
    });

    it('UIB1-U2: the filter is the caller\'s own org and person; a job is a run another run handed work to', () => {
      const query = { orgId: 'org-1', actorId: 'user-1', limit: 10 };
      const job = run({ parent: { runId: 'root', invocationId: 't1', depth: 1 } });
      expect(matchesPersonRunQuery(run(), query)).to.equal(true);
      expect(matchesPersonRunQuery(run({ actorId: 'user-2' }), query)).to.equal(false);
      expect(matchesPersonRunQuery(run({ orgId: 'org-2' }), query)).to.equal(false);
      expect(matchesPersonRunQuery(job, { ...query, kind: 'job' })).to.equal(true);
      expect(matchesPersonRunQuery(job, { ...query, kind: 'root' })).to.equal(false);
      expect(matchesPersonRunQuery(run(), { ...query, kind: 'job' })).to.equal(false);
      expect(matchesPersonRunQuery(run({ agentKey: 'a' }), { ...query, agentKey: 'b' })).to.equal(false);
    });

    it('UIB1-U3: a cursor round-trips, and anything this server did not hand out is refused', () => {
      const cursor = { createdAt: 1_700_000_000_000, runId: 'run:with:colons' };
      expect(decodeRunListCursor(encodeRunListCursor(cursor))).to.deep.equal(cursor);
      for (const forged of ['', 'bm9wZQ', Buffer.from('-1:r').toString('base64url'), Buffer.from('12:').toString('base64url')]) {
        expect(decodeRunListCursor(forged), forged).to.equal(null);
      }
    });

    it("UIB1-U4: a job's engine is its pinned executor's; an engine run's its profile's; the loop has none", () => {
      expect(listedEngine({ engine: { kind: 'codex', version: '1' } }, null)).to.equal('codex');
      expect(listedEngine(null, { executor: { kind: 'engine', engine: { kind: 'claude_code' } } })).to.equal('claude_code');
      expect(listedEngine(null, { executor: { kind: 'loop' } })).to.equal(null);
      expect(listedEngine('garbage', 7)).to.equal(null);
    });

    it('UIB1-U5: a chat list says working, needs you (the person\'s own ask) and finished unseen against their mark', () => {
      const states = conversationRunStates(
        'user-1',
        ['working', 'asking', 'unseen', 'seen', 'never-opened', 'someone-else-asks'],
        [
          run({ runId: 'w', sessionId: 'working' }),
          run({ runId: 'a', sessionId: 'asking', openInteractions: ['ix.approve.t1'] }),
          run({ runId: 'o', sessionId: 'someone-else-asks', actorId: 'user-2', openInteractions: ['ix.approve.t1'] }),
        ],
        [
          { sessionId: 'unseen', runId: 'u', status: 'completed', endedAt: 5_000 },
          { sessionId: 'seen', runId: 's', status: 'completed', endedAt: 5_000 },
          { sessionId: 'never-opened', runId: 'n', status: 'failed', endedAt: 5_000 },
        ],
        new Map([
          ['unseen', 4_000],
          ['seen', 6_000],
        ]),
      );
      const by = new Map(states.map((state) => [state.sessionId, state]));
      expect(by.get('working')).to.deep.include({ runState: 'working', needsYou: false, activeRun: { runId: 'w', status: 'running' } });
      expect(by.get('asking')).to.deep.include({ runState: 'needs_you', needsYou: true });
      expect(by.get('someone-else-asks')).to.deep.include({ runState: 'working', needsYou: false });
      expect(by.get('unseen')).to.deep.include({ runState: null, finishedUnseen: true, seenUpTo: 4_000, activeRun: null });
      expect(by.get('seen')).to.deep.include({ finishedUnseen: false, seenUpTo: 6_000 });
      // The mark starts when the person first opens it: nothing is "unseen" from before.
      expect(by.get('never-opened')).to.deep.include({ finishedUnseen: false, seenUpTo: null });
    });
  });

  describe('RunListService', () => {
    const listed = (runs: HarnessRun[], engine: string | null = null) =>
      runs.map((item) => ({ run: item, engine }));
    const store = (runs: HarnessRun[]): RunLists & { asked: unknown[] } => {
      const asked: unknown[] = [];
      return {
        asked,
        personRuns: async (query) => {
          asked.push(query);
          return listed(runs.filter((item) => matchesPersonRunQuery(item, query)).slice(0, query.limit));
        },
        openRunsOfSessions: async () => [],
        lastEndedRuns: async () => [],
        sessionJobs: async () => [],
      };
    };
    const access = { assertAccess: async () => undefined };

    it('UIB1-U6: a page of one more than asked says where the next starts; the last page says none', async () => {
      const runs = [3, 2, 1].map((n) => run({ runId: `run-${n}`, createdAt: n * 1_000, agentKey: 'agent-1' }));
      const service = new RunListService({
        runs: store(runs),
        seen: new InMemoryConversationSeenMarks(),
        access,
        names: {
          agents: { names: async () => new Map([['agent-1', 'Triage']]) },
          chats: { titles: async () => new Map([['ses-1', 'Pricing']]) },
        },
      });
      const first = await service.personRuns({ orgId: 'org-1', actorId: 'user-1' }, { limit: 2 });
      expect(first.runs.map((item) => item.runId)).to.deep.equal(['run-3', 'run-2']);
      expect(first.runs[0]).to.deep.include({ agentName: 'Triage', conversationTitle: 'Pricing', state: 'working', job: false, endedAt: null });
      expect(decodeRunListCursor(first.nextCursor!)).to.deep.equal({ createdAt: 2_000, runId: 'run-2' });
      const second = await service.personRuns({ orgId: 'org-1', actorId: 'user-1' }, { limit: 2, after: decodeRunListCursor(first.nextCursor!)! });
      expect(second.runs.map((item) => item.runId)).to.deep.equal(['run-1']);
      expect(second.nextCursor).to.equal(null);
    });

    it("UIB1-U7: the store is asked for the caller's own org and person only; a limit is held to 1..100", async () => {
      const runs = store([]);
      const service = new RunListService({ runs, seen: new InMemoryConversationSeenMarks(), access });
      await service.personRuns({ orgId: 'org-1', actorId: 'user-1' }, { limit: 10_000, agentKey: 'agent-1', states: ['needs_you'] });
      await service.personRuns({ orgId: 'org-1', actorId: 'user-1' }, { limit: 0 });
      expect(runs.asked).to.deep.equal([
        { orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-1', states: ['needs_you'], limit: 101 },
        { orgId: 'org-1', actorId: 'user-1', limit: 2 },
      ]);
    });

    it('UIB1-U8 (G11): a stopped run says who stopped it and why, and when it ended', async () => {
      const stopped = run({ status: 'cancelled', updatedAt: 9_000, operatorStop: { scope: 'session', reason: 'maintenance', at: 8_000 } });
      const service = new RunListService({ runs: store([stopped]), seen: new InMemoryConversationSeenMarks(), access });
      const [view] = (await service.personRuns({ orgId: 'org-1', actorId: 'user-1' }, {})).runs;
      expect(view).to.deep.include({ state: 'stopped', endedAt: 9_000, operatorStop: { scope: 'session', reason: 'maintenance', at: 8_000 } });
    });

    it('UIB1-U9: the seen mark is set only on a conversation the caller may open, at the server\'s time', async () => {
      const seen = new InMemoryConversationSeenMarks();
      const refused = new RunListService({
        runs: store([]),
        seen,
        access: { assertAccess: async () => Promise.reject(new HarnessNotFoundError()) },
        now: () => 7_000,
      });
      const scope = { orgId: 'org-1', actorId: 'user-1', sessionId: 'ses-1' };
      let error: unknown;
      try {
        await refused.markSeen(scope);
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(HarnessNotFoundError);
      expect([...(await seen.read('org-1', 'user-1', ['ses-1']))]).to.deep.equal([]);

      const allowed = new RunListService({ runs: store([]), seen, access, now: () => 7_000 });
      expect(await allowed.markSeen(scope)).to.deep.equal({ seenUpTo: 7_000 });
    });

    it("UIB1-U10: a chat list's states are read once per conversation, at most 200, for the caller's own org and person", async () => {
      const asked: unknown[][] = [];
      const runs: RunLists = {
        personRuns: async () => [],
        openRunsOfSessions: async (orgId, ids) => (asked.push(['open', orgId, ids.length]), [run({ sessionId: 'a' })]),
        lastEndedRuns: async (orgId, actorId, ids) => (asked.push(['ended', orgId, actorId, ids.length]), []),
        sessionJobs: async () => [],
      };
      const seen = new InMemoryConversationSeenMarks();
      const service = new RunListService({ runs, seen, access });
      expect(await service.conversationStates({ orgId: 'org-1', actorId: 'user-1' }, [])).to.deep.equal([]);
      expect(asked).to.deep.equal([]);

      const ids = ['a', 'a', ...Array.from({ length: MAX_CONVERSATION_STATES + 5 }, (_, i) => `s${i}`)];
      const states = await service.conversationStates({ orgId: 'org-1', actorId: 'user-1' }, ids);
      expect(states).to.have.length(MAX_CONVERSATION_STATES);
      expect(states[0]).to.deep.include({ sessionId: 'a', runState: 'working' });
      expect(asked).to.deep.equal([
        ['open', 'org-1', MAX_CONVERSATION_STATES],
        ['ended', 'org-1', 'user-1', MAX_CONVERSATION_STATES],
      ]);
    });
  });

  const existing = new Set<string>();
  describeConversationSeenContract('in-memory', () => ({
    marks: new InMemoryConversationSeenMarks((orgId, sessionId) => existing.has(`${orgId}/${sessionId}`)),
    conversation: async (orgId) => {
      const id = new Types.ObjectId().toHexString();
      existing.add(`${orgId}/${id}`);
      return id;
    },
  }));
});
