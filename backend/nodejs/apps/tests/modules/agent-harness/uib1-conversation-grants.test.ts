/**
 * UI-B1 G5, ADR-GRANT-01 D8: a conversation's owner lists what "Allow for this chat" allows and takes a grant back.
 * The revocation reaches every open run of the conversation and the root stream later runs read, so neither Node's
 * held-grant answer (D7) nor a worker's read before an ask (D3) allows that call again.
 */
import { expect } from 'chai';
import { ConversationGrantService } from '../../../src/modules/agent-harness/application/conversation-grants';
import {
  answeredAsks,
  conversationGrantId,
  conversationGrantList,
  GRANT_REVOKED_EVENT,
  grantRevokedEvent,
  runGrantState,
} from '../../../src/modules/agent-harness/domain/conversation-grants';
import { auditProjection } from '../../../src/modules/agent-harness/domain/audit';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { validateJournalAppend, type JournalEvent, type PreparedJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { conversationGrantsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/conversation-grants';
import { conversationGrantRevocationSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/conversation-grant-revocation';

const FETCH = 'web@searxng/fetch_url';
const ONYX = 'sha256:4c5c0f51fdaf2b50038b8c674f654f5407e153408d5688d5b5fbcf38395aa0c3';
const PIPESHUB = 'sha256:e50a24657b577a620ef914bb453c46e4e282f380a38c19026372cd82626c16cb';
const SESSION = 'ses-1';

/** The conversation's journal: one array, read by stream or by run, as the stores read it. */
class Journal {
  readonly events: JournalEvent[] = [];
  private at = 0;

  add(stream: string, runId: string | undefined, type: string, writer: 'node' | 'worker', data: Record<string, unknown>): void {
    this.at += 1;
    this.events.push({
      orgId: 'org-1',
      sessionId: SESSION,
      streamId: stream,
      ...(runId !== undefined ? { runId } : {}),
      seq: this.events.filter((event) => event.streamId === stream).length + 1,
      type,
      writer,
      schemaVersion: 1,
      critical: false,
      visibility: 'owner',
      ts: new Date(Date.UTC(2026, 10, 9, 9, 0, this.at)).toISOString(),
      data,
    } as JournalEvent);
  }

  readStream = async (_orgId: string, streamId: string, after: number, limit: number) =>
    this.events.filter((event) => event.streamId === streamId && event.seq > after).slice(0, limit);

  readRun = async (_orgId: string, runId: string, after: number, limit: number) =>
    this.events.filter((event) => event.runId === runId && event.seq > after).slice(0, limit);

  append = async (batch: PreparedJournalAppend) => {
    for (const event of batch.events) {
      this.add(batch.streamId, batch.runId, event.type, batch.writer, event.data ?? {});
    }
    return { firstSeq: 0, lastSeq: 0 } as never;
  };
}

const offer = (ix: string, key: string, sensitivity = 'private') => ({
  interactionId: ix,
  toolId: FETCH,
  rule: 'flow_external_write',
  conversationGrant: true,
  sensitivity,
  destination: { key, label: key === ONYX ? 'onyx.app' : 'pipeshub.com' },
});

const run = (runId: string, parent?: string): HarnessRun =>
  ({
    orgId: 'org-1',
    sessionId: SESSION,
    runId,
    harnessMajor: 2,
    status: 'running',
    actorId: 'u-owner',
    ...(parent !== undefined ? { parent: { runId: parent, invocationId: 't1.0', depth: 1 } } : {}),
  }) as unknown as HarnessRun;

/**
 * A conversation whose root run (run-1, ended or open) allowed onyx.app on its own ask, and whose engine job
 * (job-1, open) allowed pipeshub.com, copied onto the root run; the job holds the root's grant by copy.
 */
function conversation(rootOpen: boolean) {
  const journal = new Journal();
  journal.add(SESSION, 'run-1', 'interaction.explained', 'worker', offer('ix.approve.t1.0', ONYX));
  journal.add(SESSION, 'run-1', 'interaction.requested', 'worker', { interactionId: 'ix.approve.t1.0', kind: 'approval' });
  journal.add(SESSION, 'run-1', 'interaction.resolved', 'node', { interactionId: 'ix.approve.t1.0', decision: 'approved', by: 'p_u-owner' });
  journal.add(SESSION, 'run-1', 'interaction.granted', 'node', { interactionId: 'ix.approve.t1.0', scope: 'conversation', toolId: FETCH, key: ONYX, sensitivity: 'public' });
  journal.add('job-1', 'job-1', 'interaction.granted', 'node', {
    interactionId: 'ix.approve.t1.0', scope: 'conversation', toolId: FETCH, key: ONYX, sensitivity: 'public', fromRunId: 'run-1',
  });
  journal.add('job-1', 'job-1', 'interaction.explained', 'worker', offer('ix.approve.t3.0', PIPESHUB));
  journal.add('job-1', 'job-1', 'interaction.requested', 'worker', { interactionId: 'ix.approve.t3.0', kind: 'approval' });
  journal.add('job-1', 'job-1', 'interaction.granted', 'node', {
    interactionId: 'ix.approve.t3.0', scope: 'conversation', toolId: FETCH, key: PIPESHUB, sensitivity: 'private', by: 'p_u-owner',
  });
  journal.add(SESSION, 'run-1', 'interaction.granted', 'node', {
    interactionId: 'ix.approve.t3.0', scope: 'conversation', toolId: FETCH, key: PIPESHUB, sensitivity: 'private', by: 'p_u-owner', fromRunId: 'job-1',
  });
  // The job asks for onyx.app again with private data: the public grant does not reach it, so it stays open.
  journal.add('job-1', 'job-1', 'interaction.explained', 'worker', offer('ix.approve.t4.0', ONYX, 'public'));
  journal.add('job-1', 'job-1', 'interaction.requested', 'worker', { interactionId: 'ix.approve.t4.0', kind: 'approval' });
  const open = [...(rootOpen ? [run('run-1')] : []), run('job-1', 'run-1')];
  let owner = true;
  const service = new ConversationGrantService<unknown>({
    access: {
      assertOwner: async () => {
        if (!owner) throw new HarnessNotFoundError();
      },
    },
    executions: { openSessionRuns: async () => open },
    journal,
    journals: {
      prepare: async (append) => {
        validateJournalAppend(append);
        return append;
      },
      resolve: async (events) => events,
    },
    transaction: (work) => work(undefined),
    members: { profiles: async () => new Map([['u-owner', { name: 'Ada Lovelace', email: 'ada@example.com' }]]) },
  });
  const scope = { orgId: 'org-1', actorId: 'u-owner', sessionId: SESSION };
  return { journal, service, scope, notOwner: () => (owner = false) };
}

describe('UI-B1 G5: conversation grants listed and revoked (ADR-GRANT-01 D8)', () => {
  it('UIB1-G1: a run holds a grant until a revocation of its tool and destination; a later grant holds again', () => {
    const journal = new Journal();
    journal.add('job-1', 'job-1', 'interaction.granted', 'node', { interactionId: 'ix.1', scope: 'conversation', toolId: FETCH, key: ONYX, sensitivity: 'private', fromRunId: 'run-1' });
    journal.add('job-1', 'job-1', 'interaction.explained', 'worker', offer('ix.2', ONYX));
    journal.add('job-1', 'job-1', 'interaction.requested', 'worker', { interactionId: 'ix.2', kind: 'approval' });
    const before = runGrantState(journal.events);
    expect(answeredAsks(before.openOffers, before.held).map(({ interactionId }) => interactionId)).to.deep.equal(['ix.2']);

    journal.add('job-1', 'job-1', GRANT_REVOKED_EVENT, 'node', { toolId: FETCH, key: ONYX, by: 'p_u-owner' });
    const after = runGrantState(journal.events);
    expect(after.held).to.deep.equal([]);
    expect(answeredAsks(after.openOffers, after.held), 'Node answers nothing with it (D7)').to.deep.equal([]);

    journal.add('job-1', 'job-1', 'interaction.granted', 'node', { interactionId: 'ix.3', scope: 'conversation', toolId: FETCH, key: ONYX, sensitivity: 'public', fromRunId: 'run-1' });
    expect(runGrantState(journal.events).held.map((grant) => grant.sensitivity)).to.deep.equal(['public']);
  });

  it('UIB1-G2: the revocation is a valid node event, audited by tool, key and who; never a worker\'s', () => {
    const event = grantRevokedEvent({ toolId: FETCH, key: ONYX }, 'p_u-owner');
    expect(() => validateJournalAppend({ orgId: 'org-1', sessionId: SESSION, streamId: SESSION, writer: 'node', events: [event] })).to.not.throw();
    expect(() => validateJournalAppend({ orgId: 'org-1', sessionId: SESSION, streamId: SESSION, writer: 'worker', events: [event] })).to.throw();
    expect(auditProjection(event.type, event.data ?? {})?.fields).to.deep.equal({ toolId: FETCH, key: ONYX, by: 'p_u-owner' });
  });

  it("UIB1-G3: the owner's list: each grant once, at its widest, named by the ask the person saw, with who and when", async () => {
    const { service, scope } = conversation(false);
    const grants = await service.list(scope);
    expect(conversationGrantsSchema.safeParse({ grants }).success, 'the list is its published contract').to.equal(true);
    expect(grants.map(({ grantId, destination, sensitivity, grantedBy, grantedByName, tool }) => ({ grantId, destination, sensitivity, grantedBy, grantedByName, tool }))).to.deep.equal([
      { grantId: conversationGrantId({ toolId: FETCH, key: ONYX }), destination: 'onyx.app', sensitivity: 'public', grantedBy: 'u-owner', grantedByName: 'Ada Lovelace', tool: { app: 'web', name: 'fetch_url' } },
      { grantId: conversationGrantId({ toolId: FETCH, key: PIPESHUB }), destination: 'pipeshub.com', sensitivity: 'private', grantedBy: 'u-owner', grantedByName: 'Ada Lovelace', tool: { app: 'web', name: 'fetch_url' } },
    ]);
    expect(JSON.stringify(grants), 'never the destination key').to.not.include(ONYX);
  });

  it('UIB1-G4: a revocation reaches every open run, and the root stream when no root run is open; the list drops it', async () => {
    const { journal, service, scope } = conversation(false);
    const grantId = conversationGrantId({ toolId: FETCH, key: PIPESHUB });
    const result = await service.revoke(scope, grantId);
    expect(conversationGrantRevocationSchema.safeParse(result).success).to.equal(true);
    expect(result.runs).to.equal(1);
    const written = journal.events.filter((event) => event.type === GRANT_REVOKED_EVENT);
    expect(written.map((event) => [event.streamId, event.runId ?? null])).to.deep.equal([
      ['job-1', 'job-1'],
      [SESSION, null],
    ]);
    expect(written.every((event) => event.data?.['by'] === 'p_u-owner' && event.data?.['key'] === PIPESHUB)).to.equal(true);
    expect((await service.list(scope)).map((grant) => grant.destination)).to.deep.equal(['onyx.app']);
    expect(runGrantState(journal.events.filter((event) => event.streamId === 'job-1')).held.map((grant) => grant.key)).to.deep.equal([ONYX]);
    // What the session's next root run reads on the root stream holds no pipeshub.com grant either.
    expect(conversationGrantList(journal.events.filter((event) => event.streamId === SESSION)).map((grant) => grant.key)).to.deep.equal([ONYX]);
  });

  it('UIB1-G5: with a root run open, its own stream carries the revocation; no session event is needed', async () => {
    const { journal, service, scope } = conversation(true);
    await service.revoke(scope, conversationGrantId({ toolId: FETCH, key: ONYX }));
    expect(journal.events.filter((event) => event.type === GRANT_REVOKED_EVENT).map((event) => [event.streamId, event.runId])).to.deep.equal([
      [SESSION, 'run-1'],
      ['job-1', 'job-1'],
    ]);
  });

  it('UIB1-G6: a grant the conversation does not hold is not found; someone who is not the owner changes nothing', async () => {
    const missing = conversation(false);
    let error: unknown;
    try {
      await missing.service.revoke(missing.scope, `cg_${'0'.repeat(40)}`);
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(HarnessNotFoundError);

    const other = conversation(false);
    other.notOwner();
    const before = other.journal.events.length;
    for (const call of [() => other.service.list(other.scope), () => other.service.revoke(other.scope, conversationGrantId({ toolId: FETCH, key: ONYX }))]) {
      let refused: unknown;
      try {
        await call();
      } catch (caught) {
        refused = caught;
      }
      expect(refused).to.be.instanceOf(HarnessNotFoundError);
    }
    expect(other.journal.events.length).to.equal(before);
  });
});
