import 'reflect-metadata';
import { expect } from 'chai';
import { Types } from 'mongoose';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ANSWERS_ONLY_HISTORY_FEATURE, resolveRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  AdmitRunCommand,
  rootRunDocument,
  rootRunRequirements,
  sessionJournalFeatures,
  withAnswersOnlyHistory,
} from '../../../src/modules/agent-harness/domain/admission';
import { GUARDED_WORKER_FEATURES, LEGACY_RUN_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { answersOnlyTurns, readByOthers } from '../../../src/modules/enterprise_search/utils/earlier-turns';

/**
 * A chat others read as a turn starts: its run's model is given the earlier turns' questions and answers' text, never
 * what their tools returned. Node decides it at admission, from the chat as it is then, and pins it on the run.
 */

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const PRIVATE = 'Alpha budget 4.2M, the board has not seen it';
const BEN = new Types.ObjectId();

const run = (features: string[]): HarnessRun =>
  ({
    runId: 'run-1', orgId: 'org-1', sessionId: 'ses-1', actorId: 'user-1', userMessageKey: 'm-1',
    idempotencyKey: 'k-1', payloadHash: 'h', harnessMajor: 2, profile: 'dispatched', status: 'queued',
    outcome: null, revision: 0, fence: 0, attemptId: null, workerId: null, leaseExpiresAt: null,
    absoluteDeadlineAt: Date.UTC(2026, 10, 11, 12), checkpoint: null,
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
    createdAt: Date.UTC(2026, 10, 11, 11), updatedAt: Date.UTC(2026, 10, 11, 11),
    requires: { foldVersion: 1, features: features as never },
  }) as HarnessRun;

/** As the admission pipeline's own tests make one. */
const command = (): AdmitRunCommand => ({
  orgId: 'o1',
  actorId: 'u1',
  sessionId: 's1',
  idempotencyKey: 'k',
  payloadHash: 'sha256:00',
  runId: 'r1',
  dispatchCommandId: 'dispatch:1',
  absoluteDeadlineAt: 2_000,
  input: {
    query: 'q',
    model: { chatMode: 'quick' },
    filters: {},
    attachments: [],
    recordIds: [],
    previousConversations: [],
  } as unknown as AdmitRunCommand['input'],
  harnessMajor: 2,
  via: 'session',
});

const placement = {
  sessionId: 's1',
  userMessageKey: 'm1',
  idempotencyKey: 'd',
  absoluteDeadlineAt: 2_000,
  input: command().input,
  now: 1_000,
};

describe('who else reads a chat as a turn starts (readByOthers)', () => {
  it('shared with a person or a team, or open to its project', () => {
    expect(readByOthers({ isShared: true, sharedWith: [{ userId: BEN, accessLevel: 'read' }] })).to.equal(true);
    expect(readByOthers({ isShared: true, sharedWith: [{ teamId: 't1', accessLevel: 'read' }] })).to.equal(true);
    expect(readByOthers({ projectVisibility: 'project' })).to.equal(true);
  });

  it('nobody: never shared, shared with nobody, unshared with its old rows kept, private in its project, or no chat', () => {
    expect(readByOthers({})).to.equal(false);
    expect(readByOthers({ isShared: true, sharedWith: [] })).to.equal(false);
    expect(readByOthers({ isShared: false, sharedWith: [{ userId: BEN, accessLevel: 'read' }] })).to.equal(false);
    expect(readByOthers({ projectVisibility: 'private' })).to.equal(false);
    expect(readByOthers(null)).to.equal(false);
    expect(readByOthers(undefined)).to.equal(false);
  });
});

describe('answersOnlyTurns', () => {
  const turns = [
    { role: 'user_query', content: 'what do our plans say?', attachments: [{ recordId: 'R' }] },
    {
      role: 'bot_response',
      content: 'The public Charlie plan lists three themes.',
      tool_results: [
        { tool_id: 'c1', tool_name: 'search', result: PRIVATE },
        { tool_id: 'ask_user_question', tool_name: 'internaltools__ask_user_question', result: '{"answers":["EU"]}' },
      ],
      referenceData: [{ name: 'Alpha plan', id: 'a1' }],
    },
  ];

  it('keeps the questions, their attachments, the answers and the cards the person answered; drops what tools returned', () => {
    const kept = answersOnlyTurns(turns);
    expect(JSON.stringify(kept)).to.not.include(PRIVATE);
    expect(kept.map((t) => t.content)).to.deep.equal(turns.map((t) => t.content));
    expect(kept[0]).to.deep.equal(turns[0]);
    expect(kept[1]).to.deep.equal({
      role: 'bot_response',
      content: 'The public Charlie plan lists three themes.',
      tool_results: [turns[1]!.tool_results![1]],
    });
  });

  it('leaves the turns it is given unchanged', () => {
    const before = JSON.stringify(turns);
    answersOnlyTurns(turns);
    expect(JSON.stringify(turns)).to.equal(before);
  });
});

describe('a run admitted in a chat others read', () => {
  it('requires a worker that gives its model the earlier answers only; a run in a chat nobody else reads does not', () => {
    const base = rootRunRequirements(rootRunDocument(command(), placement), command(), [], null, null);
    const shared = withAnswersOnlyHistory(base, true);
    const solo = withAnswersOnlyHistory(base, false);
    expect(solo).to.deep.equal(base);
    expect(shared?.features).to.include(ANSWERS_ONLY_HISTORY_FEATURE);
    expect(solo?.features).to.not.include(ANSWERS_ONLY_HISTORY_FEATURE);
  });

  it('is decided for each run, not kept for the session: a later turn follows the chat as it is then', () => {
    expect(sessionJournalFeatures(run([ANSWERS_ONLY_HISTORY_FEATURE]), command())).to.not.include(ANSWERS_ONLY_HISTORY_FEATURE);
  });

  it('pins x.pipeshub.history on the spec of a run that requires it, and on no other', () => {
    const pinned = resolveRunSpec({ run: run([ANSWERS_ONLY_HISTORY_FEATURE]), agentDefinition: null, via: 'session' }, DURABLE);
    expect((pinned.extensions?.['x.pipeshub'] as Record<string, unknown>)['history']).to.deep.equal({ earlierTurns: 'answers' });
    const plain = resolveRunSpec({ run: run([]), agentDefinition: null, via: 'session' }, DURABLE);
    expect(plain.extensions?.['x.pipeshub']).to.not.have.property('history');
  });

  it('never runs on a worker without it: not a guarded feature, and no run admitted before it needs it', () => {
    expect(GUARDED_WORKER_FEATURES).to.not.include(ANSWERS_ONLY_HISTORY_FEATURE);
    expect(LEGACY_RUN_FEATURES).to.not.include(ANSWERS_ONLY_HISTORY_FEATURE);
  });
});
