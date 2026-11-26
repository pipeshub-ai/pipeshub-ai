/**
 * LC2FIX on a MongoDB replica set: a `/review` turn sends the workspace's diff
 * to the run, whose input is sealed, while the conversation's message keeps
 * only what was typed. LIFECYCLE-2 found the diff, a sealed secret file's
 * contents included, in `chatSessionMessages.content` in plaintext.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { sessionMessages } from './support/session-messages';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { appendMessages } from '../../../src/modules/enterprise_search/utils/utils';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  expandChatCommand,
  pipeshubCommandsFor,
  regenerateChatCommand,
  storedRegenerateCommand,
} from '../../../src/modules/enterprise_search/utils/chat-command';
import { pipeshubPromptCommands, reviewText } from '../../../src/modules/agent-harness/application/pipeshub-prompt-commands';
import type { WorkspaceDiff } from '../../../src/modules/agent-harness/application/session-workspace-service';
import { typedReviewMessages } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { contractClock, ContractHarness, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';
import { ChatTurnHarness, ORG, sseFrames, startChatTurnHarness, USER } from './support/chat-turn-harness';
import { persistedRun } from '../../contracts/support/surfaces';

const diffWith = (marker: string): WorkspaceDiff => ({
  workspaceId: 'ws:s1',
  head: 2,
  from: 0,
  to: 2,
  files: [
    {
      path: 'data/secret.txt',
      change: 'added',
      sizeBytes: marker.length,
      additions: 1,
      deletions: 0,
      diff: `--- /dev/null\n+++ b/data/secret.txt\n@@ -0,0 +1 @@\n+${marker}\n`,
    },
  ],
  additions: 1,
  deletions: 0,
  omitted: 0,
});

describe('LC2FIX: /review never writes the workspace into the message store, on a MongoDB replica set', function () {
  this.timeout(60_000);
  let conversation: Record<string, unknown>;
  let h: ContractHarness;
  let http: ChatTurnHarness | undefined;
  let diff: WorkspaceDiff;

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    clock.set(Date.now());
    h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const builtin = pipeshubCommandsFor(pipeshubPromptCommands(() => ({ diff: async () => diff })));
    http = await startChatTurnHarness({
      conversation,
      publicSurface: 'web',
      stores: { admission: h.backend.admissionStore, runs: h.backend.executionStore, journal: h.backend.journal, inputs: h.backend.inputs },
      commands: {
        send: expandChatCommand(async () => ({ kind: 'not_found' }), builtin),
        regenerate: regenerateChatCommand(storedRegenerateCommand, builtin),
      },
    });
  });
  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  const answer = async (run: HarnessRun, reply: string): Promise<void> => {
    const claimed = await h.claim(run);
    await h.service.commit({
      commandId: `${run.runId}:answer`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      events: [
        workerEvent(
          'x.pipeshub.answer.finalized',
          { outcome: 'answered', answer: reply, partTexts: [reply], citations: [], confidence: null },
          { critical: false },
        ),
      ],
      legacyEvents: [],
      transition: { status: 'completed', outcome: 'answer' },
      outbox: [],
    });
  };

  const turn = { chatMode: 'internal_search', modelKey: 'model-1' };
  const runOf = async (reply: Response): Promise<HarnessRun> => {
    expect(reply.status).to.equal(200);
    const frames = sseFrames(await reply.text());
    const { runId } = frames[0]?.data['value'] as { runId: string };
    return (await h.service.get(ORG, runId)) as HarnessRun;
  };
  /** Every stored message of the session, as the database holds it. */
  const stored = async (sessionId: string) =>
    JSON.stringify(await mongoose.connection.db!.collection('chatSessionMessages').find({ sessionId: new mongoose.Types.ObjectId(sessionId) }).toArray());

  it('the run is sent the diff (sealed); the message keeps "/review focus"; a regenerate reviews the workspace as it is now', async () => {
    const first = await h.admit({
      orgId: ORG,
      actorId: USER,
      input: { query: 'Write the notes.', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
    });
    await answer(first.run, 'Done.');
    const sessionId = first.sessionId;

    diff = diffWith('hidden=QZXV');
    const review = await runOf(await http!.post(`/conversations/${sessionId}/messages/stream`, 'session', { query: '/review the SQL', ...turn }));
    expect(review.input?.query).to.equal(reviewText('the SQL', diff));
    expect(await stored(sessionId)).not.to.contain('QZXV');
    const asked = ((await sessionMessages(sessionId)) as IMessage[]).filter((m) => m.messageType === 'user_query').pop();
    expect(asked?.content).to.equal('/review the SQL');
    expect(asked?.command).to.include({ name: 'review', typed: '/review the SQL', files: 1 });
    // The run input holds the expansion sealed, never in plaintext.
    const raw = JSON.stringify(await mongoose.connection.db!.collection('agentRuns').find({ runId: review.runId }).toArray());
    expect(raw).not.to.contain('QZXV');

    await answer(review, 'Nothing to fix.');
    const answerId = String(((await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>).pop()?._id);
    diff = diffWith('second=WKJP');
    const again = await runOf(await http!.post(`/conversations/${sessionId}/message/${answerId}/regenerate`, 'session', turn));
    expect(again.input?.query).to.equal(reviewText('the SQL', diff));
    const after = await stored(sessionId);
    expect(after).not.to.contain('QZXV');
    expect(after).not.to.contain('WKJP');
    expect(((await sessionMessages(sessionId)) as IMessage[]).filter((m) => m.messageType === 'user_query').map((m) => m.content)).to.deep.equal([
      'Write the notes.',
      '/review the SQL',
    ]);
  });

  it('a /review message saved before the fix keeps only what was typed after the boot migration; other messages are untouched', async () => {
    const first = await h.admit({
      orgId: ORG,
      actorId: USER,
      input: { query: 'Write the notes.', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
    });
    const command = { name: 'review', version: '1.0.0', contentDigest: `sha256:${'0'.repeat(64)}`, args: {}, typed: '/review', owner: 'pipeshub' };
    const slash1 = { ...command, name: 'summarize', typed: '/summarize', owner: 'you' };
    await appendMessages(first.sessionId, ORG, [
      { messageType: 'user_query', content: reviewText('', diffWith('hidden=QZXV')), command },
      { messageType: 'user_query', content: 'Summarize the notes.', command: slash1 },
    ] as unknown as IMessage[]);
    const db = mongoose.connection.db!;
    expect(await typedReviewMessages(db)).to.equal(1);
    expect(await typedReviewMessages(db)).to.equal(0);
    expect(await stored(first.sessionId)).not.to.contain('QZXV');
    expect(((await sessionMessages(first.sessionId)) as IMessage[]).map((m) => m.content)).to.include.members(['/review', 'Summarize the notes.']);
  });
});
