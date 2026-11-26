import { expect } from 'chai';
import { Types } from 'mongoose';
import {
  FOREIGN_TURN_STUB,
  MAX_OWN_RUNS,
  ownerHistoryTurns,
  projectHistoryTurns,
  runAudience,
  runViewAudience,
} from '../../../src/modules/agent-harness/domain/session-audience';
import { turnRefusal } from '../../../src/modules/agent-harness/domain/session-roles';
import { workerRun } from '../../../src/modules/agent-harness/domain/steering';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  formatPreviousConversations,
  historyTurns,
} from '../../../src/modules/enterprise_search/utils/utils';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';

const STUBS = [
  { role: 'user_query', content: FOREIGN_TURN_STUB },
  { role: 'bot_response', content: FOREIGN_TURN_STUB },
];

describe('SS2 CA-1: who may send a turn', () => {
  const rows: Array<[Parameters<typeof turnRefusal>, string | null]> = [
    [['owner', 'loop', false], null],
    [['owner', 'engine', true], null],
    [['collaborator', 'loop', true], null],
    [['collaborator', 'loop', false], 'COLLABORATOR_TURNS_DISABLED'],
    [['collaborator', 'loop'], 'COLLABORATOR_TURNS_DISABLED'],
    [['collaborator', 'engine', true], 'SHARED_SESSION_ENGINE'],
    [['viewer', 'loop', true], 'VIEWER_READ_ONLY'],
  ];
  for (const [args, expected] of rows) {
    it(`${args.join(', ')} → ${String(expected)}`, () => {
      expect(turnRefusal(...args)).to.equal(expected);
    });
  }
});

describe('SS2 AU-1: a run audience', () => {
  const context = { ownerId: 'alice', shared: false, actors: ['alice'] };

  it('is none in a personal context; present for a collaborator, a shared session, or a session someone else ran in', () => {
    expect(runAudience('alice', 'owner', context, ['r1'])).to.equal(null);
    expect(runAudience('bob', 'collaborator', context, [])).to.deep.equal({
      sessionRole: 'collaborator',
      ownerRef: 'p_alice',
      ownRuns: [],
    });
    expect(runAudience('alice', 'owner', { ...context, shared: true }, ['r2', 'r1'])).to.deep.equal({
      sessionRole: 'owner',
      ownerRef: 'p_alice',
      ownRuns: ['r1', 'r2'],
    });
    expect(runAudience('alice', 'owner', { ...context, actors: ['alice', 'bob'] }, [])?.sessionRole).to.equal('owner');
  });

  it(`lists at most the newest ${String(MAX_OWN_RUNS)} own runs; an older one reads as someone else's`, () => {
    const runs = Array.from({ length: MAX_OWN_RUNS + 5 }, (_, i) => `r${String(i).padStart(5, '0')}`);
    const audience = runAudience('bob', 'collaborator', context, runs)!;
    expect(audience.ownRuns).to.have.length(MAX_OWN_RUNS);
    expect(audience.ownRuns).to.not.include('r00000');
    expect(audience.ownRuns).to.include(runs[runs.length - 1]);
  });

  it('AU-8: a run is watched as owner by its actor only', () => {
    expect(runViewAudience({ actorId: 'bob' }, 'bob')).to.equal('owner');
    expect(runViewAudience({ actorId: 'bob' }, 'alice')).to.equal('shared');
  });

  it('FW-2: the worker never receives sessionRole on the run (its run model is strict; it reads the pinned audience)', () => {
    const run = { runId: 'r', actorId: 'bob', sessionRole: 'collaborator' } as unknown as HarnessRun;
    expect(workerRun(run)).to.deep.equal({ runId: 'r', actorId: 'bob' });
  });
});

describe('SS2 AU-3, AU-5: history entries as a sender may see them', () => {
  const owner = { role: 'user_query', content: 'owner asks', attachments: [{ recordId: 'R' }] };
  const ownerAnswer = { role: 'bot_response', content: 'from R', tool_results: [{ result: 'R' }] };
  const bobs = { role: 'user_query', content: 'bob asks', sender: 'p_bob' };
  const bobsAnswer = { role: 'bot_response', content: 'bob gets', sender: 'p_bob' };

  it("stubs each run of another participant's entries; keeps the sender's own as they are", () => {
    expect(projectHistoryTurns([owner, ownerAnswer, bobs, bobsAnswer], 'p_bob', 'p_alice')).to.deep.equal([
      ...STUBS,
      bobs,
      bobsAnswer,
    ]);
    expect(projectHistoryTurns([owner, ownerAnswer, bobs, bobsAnswer], 'p_alice', 'p_alice')).to.deep.equal([
      owner,
      ownerAnswer,
      ...STUBS,
    ]);
    const twice = projectHistoryTurns([owner, ownerAnswer, bobs], 'p_bob', 'p_alice');
    expect(projectHistoryTurns(twice, 'p_bob', 'p_alice')).to.deep.equal(twice);
    expect(JSON.stringify(projectHistoryTurns([ownerAnswer], 'p_bob', 'p_alice'))).to.not.include('R');
  });

  it("a collaborator's message carries its sender into its history entries; the owner's carry none", () => {
    const sender = new Types.ObjectId();
    expect(historyTurns({ messageType: 'user_query', content: 'q', senderId: sender } as IMessage)).to.deep.equal([
      { content: 'q', role: 'user_query', sender: `p_${String(sender)}` },
    ]);
    expect(historyTurns({ messageType: 'user_query', content: 'q' } as IMessage)).to.deep.equal([
      { content: 'q', role: 'user_query' },
    ]);
  });

  it('the legacy routes (the owner only) give the model a collaborator\'s turns as a stub', () => {
    const sender = new Types.ObjectId();
    const messages = [
      { messageType: 'user_query', content: 'owner asks' },
      { messageType: 'bot_response', content: 'owner gets' },
      { messageType: 'user_query', content: 'bob asks', senderId: sender },
      { messageType: 'bot_response', content: 'bob gets', senderId: sender },
    ] as IMessage[];
    const history = formatPreviousConversations(messages);
    expect(history.map((turn) => turn.content)).to.deep.equal([
      'owner asks',
      'owner gets',
      FOREIGN_TURN_STUB,
      FOREIGN_TURN_STUB,
    ]);
    expect(ownerHistoryTurns([{ role: 'user_query', content: 'x' }])).to.deep.equal([{ role: 'user_query', content: 'x' }]);
  });
});
