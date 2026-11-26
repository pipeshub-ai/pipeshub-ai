/**
 * PERF-B (QA22-F3, QA22-F4) on a MongoDB replica set (run as
 * execution-store.mongo.itest.ts says): the Node's reader of run journals
 * over the sealed, audited journal, and what erasure leaves it.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import { Conversation } from '../../../src/modules/enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../src/modules/enterprise_search/schema/agent.conversation.schema';
import { RunJournalReader } from '../../../src/modules/agent-harness/application/run-journal-reader';
import { SessionErasedError } from '../../../src/modules/agent-harness/domain/errors';
import { AgentSessionErasureModel } from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../../../src/libs/repositories/agent-harness/workspace/schema/workspace.schema';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { turnStarted } from './support/journal-fixtures';
import { append, enqueue, eraseFully, seed, world } from './support/session-erasure-world';

registerHarnessModels([
  AgentSessionErasureModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceKeyModel,
  AgentWorkspaceWriterModel,
  Citation,
  Conversation,
  AgentConversation,
] as unknown as Array<Model<never>>);

describe('PERF-B: the run journal reader on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('PERFB-13: reads through the sealed journal equal the run read whole, before and after more is appended', async () => {
    const w = world();
    const s = await seed(w, { running: false });
    const runId = s.runIds[0]!;
    const reader = new RunJournalReader({ journal: w.journal, journals: w.journals });
    const whole = async () => w.journals.resolve(await w.journal.readRun(s.orgId, runId, 0, 1_000));

    const first = await reader.read(s.orgId, runId);
    expect(first.length).to.be.greaterThan(2);
    expect(first).to.deep.equal(await whole());
    expect(first.every((event) => event.payloadRef === undefined), 'claim-checked payloads are resolved').to.equal(true);
    expect(first.some((event) => typeof (event.data?.['receipt'] as { body?: unknown } | undefined)?.body === 'string')).to.equal(true);

    await append(w, {
      orgId: s.orgId,
      sessionId: s.sessionId,
      streamId: s.sessionId,
      runId,
      attemptId: 'a1',
      fence: 1,
      writer: 'worker',
      events: [turnStarted(7)],
    });
    const second = await reader.read(s.orgId, runId);
    expect(second).to.deep.equal(await whole());
    expect(second.slice(0, first.length), 'what was held is what is answered').to.deep.equal(first);
    expect(await reader.read(s.orgId, runId, first.at(-1)!.seq)).to.deep.equal(second.slice(first.length));
  });

  it('PERFB-15: once the session is erased, a Node that had read its run answers as the store does: erased', async () => {
    const w = world();
    const s = await seed(w, { running: true });
    const reader = new RunJournalReader({ journal: w.journal, journals: w.journals });
    for (const runId of s.runIds) {
      expect((await reader.read(s.orgId, runId)).length, runId).to.be.greaterThan(0);
    }

    await enqueue(w, s);
    await eraseFully(w, s);

    for (const runId of s.runIds) {
      const answered = await reader.read(s.orgId, runId).then(
        (events) => events,
        (error: unknown) => error,
      );
      expect(answered, runId).to.be.instanceOf(SessionErasedError);
    }
  });
});
