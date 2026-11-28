/**
 * A chat others read, as admission on a MongoDB replica set decides it: Ada's first turn searched, the search
 * returned a private document beside a public one, and her answer cited the public one. Each later run is admitted
 * with the chat as it is then. In a chat others read, the run requires a worker that gives its model the earlier
 * answers only, its spec says so (`x.pipeshub.history`), and the history it is admitted with holds nothing the
 * search returned. In a chat nobody else reads, nothing changes.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ANSWERS_ONLY_HISTORY_FEATURE } from '../../../src/modules/agent-harness/domain/run-spec';
import { CollaboratorBackend } from './support/collaborator-turns-contract';
import { admittedHistory, contractClock, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, resetHarnessMongo } from './support/harness-mongo';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { workerEvent } from './support/journal-fixtures';
import { mongoCollaboratorBackend, shareConversation } from './support/collaborator-mongo';

const PRIVATE = 'Alpha budget 4.2M, the board has not seen it';
const PUBLIC = 'The public Charlie plan lists three themes.';
const FIRST = 'what do our plans say?';

describe('a chat others read, as admission on a MongoDB replica set decides it', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
  let clock: ReturnType<typeof contractClock>;
  let backend: CollaboratorBackend;
  let orgId: string;
  let ada: string;
  let ben: string;

  const admission = (): RunAdmissionService =>
    new RunAdmissionService({
      store: backend.admissionStore,
      rollout: fixedRollout(() => V2),
      clock,
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
      modelAccess: testModelAccess(),
      roles: backend.roles,
      collaboratorTurns: { enabled: async () => true },
    });

  const send = async (sessionId: string | undefined, query: string) => {
    const outcome = await admission().submit({
      orgId,
      actorId: ada,
      ...(sessionId !== undefined ? { sessionId } : {}),
      idempotencyKey: randomUUID(),
      deadlineMs: 9_000,
      input: { query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (outcome.route !== 'durable') expect.fail('expected durable');
    return outcome.admission;
  };

  /** Claims `run` as a worker would: what it requires, and what its pinned spec says of the earlier turns. */
  const claim = async (run: HarnessRun) => {
    const claimed = await createContractHarness(backend, clock).claim(run, `worker-${run.runId}`);
    const subject = await backend.executionStore.readClaimSubject(orgId, run.runId);
    const ext = subject?.pinned?.spec?.extensions?.['x.pipeshub'] as Record<string, unknown> | undefined;
    if (ext === undefined) expect.fail('the claim pinned no spec');
    const required = (claimed.requires?.features ?? []).includes(ANSWERS_ONLY_HISTORY_FEATURE);
    expect(ext['history'] !== undefined, 'the spec pins it exactly when the run requires it').to.equal(required);
    if (required) expect(ext['history']).to.deep.equal({ earlierTurns: 'answers' });
    return { claimed, answersOnly: required };
  };

  const complete = async (run: HarnessRun, fence: number, text: string): Promise<void> => {
    const h = createContractHarness(backend, clock);
    await h.service.commit({
      commandId: `complete-${run.runId}`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: `worker-${run.runId}`,
      attemptId: `attempt-worker-${run.runId}`,
      fence,
      events: [
        workerEvent(
          'x.pipeshub.answer.finalized',
          { outcome: 'answered', answer: text, partTexts: [text], citations: [], confidence: null },
          { critical: false },
        ),
      ],
      legacyEvents: [],
      transition: { status: 'completed', outcome: 'answer' },
      outbox: [],
    });
  };

  const answer = async (run: HarnessRun, text: string): Promise<void> => {
    const { claimed } = await claim(run);
    await complete(run, claimed.fence, text);
  };

  /** Ada's first turn, answered, with the search under her answer as the chat keeps a turn's steps. */
  const adaSearched = async (): Promise<string> => {
    const first = await send(undefined, FIRST);
    await answer(first.run, PUBLIC);
    const updated = await ChatSessionMessage.updateOne(
      { sessionId: first.sessionId, messageType: 'bot_response' },
      {
        $set: {
          parts: [
            {
              type: 'tool_call',
              toolCallId: 'c1',
              toolName: 'search',
              status: 'completed',
              resultPreview: `Alpha: ${PRIVATE}. Charlie: three themes.`,
              resultSummary: `2 results: Alpha (${PRIVATE}), Charlie`,
            },
            { type: 'text', content: PUBLIC },
          ],
          referenceData: [{ name: 'Alpha plan', id: 'a1', type: 'record' }],
        },
      },
    );
    expect(updated.modifiedCount).to.equal(1);
    return first.sessionId;
  };

  const answersOnly = async (run: HarnessRun): Promise<boolean> => (await claim(run)).answersOnly;

  beforeEach(async () => {
    await resetHarnessMongo();
    clock = contractClock();
    backend = mongoCollaboratorBackend(clock);
    orgId = new Types.ObjectId().toString();
    ada = new Types.ObjectId().toString();
    ben = new Types.ObjectId().toString();
  });

  it('shared with Ben: Ada\'s next run gets her question and answer, and nothing the search returned', async () => {
    const sessionId = await adaSearched();
    await shareConversation(sessionId, ben, 'read');
    const second = await send(sessionId, 'what did the Alpha result say?');
    expect(await answersOnly(second.run)).to.equal(true);
    const history = await admittedHistory(backend, second.run);
    expect(JSON.stringify(history)).to.not.include(PRIVATE);
    expect(history.map((t) => [t['role'], t['content']])).to.deep.equal([
      ['user_query', FIRST],
      ['bot_response', PUBLIC],
    ]);
    expect(history.some((t) => t['referenceData'] !== undefined)).to.equal(false);
  });

  it('open to its project: the same', async () => {
    const sessionId = await adaSearched();
    await ChatSession.updateOne(
      { _id: sessionId },
      { $set: { projectId: new Types.ObjectId(), projectVisibility: 'project' } },
    );
    const second = await send(sessionId, 'and its budget?');
    expect(await answersOnly(second.run)).to.equal(true);
    expect(JSON.stringify(await admittedHistory(backend, second.run))).to.not.include(PRIVATE);
  });

  it('nobody else reads it: the next run reuses the search, as before', async () => {
    const sessionId = await adaSearched();
    const second = await send(sessionId, 'and its budget?');
    expect(await answersOnly(second.run)).to.equal(false);
    const history = await admittedHistory(backend, second.run);
    expect(JSON.stringify(history)).to.include(PRIVATE);
    expect((history[1]?.['tool_results'] as Array<{ tool_name: string }>).map((r) => r.tool_name)).to.deep.equal(['search']);
  });

  it('the turns first, then the share: an answer whose run was given the search says so, and no other does', async () => {
    const sessionId = await adaSearched();
    const second = await send(sessionId, 'and its budget?');
    expect(second.run.earlierResults, 'admitted with the search in its history').to.equal(true);
    await answer(second.run, 'It is 4.2M.');
    await shareConversation(sessionId, ben, 'read');
    const third = await send(sessionId, 'and the rest?');
    expect(third.run).to.not.have.property('earlierResults');
    await answer(third.run, 'I would search again for that.');

    const answers = await ChatSessionMessage.find({ sessionId, messageType: 'bot_response' }).sort({ seq: 1 }).lean();
    expect(answers.map((a) => [a.content, a.earlierResults])).to.deep.equal([
      [PUBLIC, undefined],
      ['It is 4.2M.', true],
      ['I would search again for that.', undefined],
    ]);
  });

  it('each run follows the chat as it is when it starts: shared, then unshared again', async () => {
    const sessionId = await adaSearched();
    const second = await send(sessionId, 'one');
    const solo = await claim(second.run);
    expect(solo.answersOnly).to.equal(false);
    await complete(second.run, solo.claimed.fence, 'two');

    await shareConversation(sessionId, ben, 'read');
    const third = await send(sessionId, 'three');
    const shared = await claim(third.run);
    expect(shared.answersOnly).to.equal(true);
    expect(JSON.stringify(await admittedHistory(backend, third.run))).to.not.include(PRIVATE);
    await complete(third.run, shared.claimed.fence, 'four');

    await ChatSession.updateOne({ _id: sessionId }, { $set: { isShared: false, sharedWith: [] } });
    const fourth = await send(sessionId, 'five');
    expect(await answersOnly(fourth.run)).to.equal(false);
    expect(JSON.stringify(await admittedHistory(backend, fourth.run))).to.include(PRIVATE);
  });
});
