/**
 * SEC-LG-02 (C6.38): transcript access follows the session ACL, and the only
 * view that shows a run's content to someone outside its session (the
 * approvals queue to the org's approvers) puts each read on the org's audit
 * chain before the answer is sent.
 * The ACL half (every transcript route refuses an org admin who is not a
 * participant, as not found) is swept in `secaz07-idor-sweep.mongo.itest.ts`.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { ChainTranscriptReadAudit } from '../../../src/modules/agent-harness/adapters/chain-transcript-read-audit';
import { ApprovalQueueService } from '../../../src/modules/agent-harness/application/approval-queue';
import {
  AGENT_HARNESS_APPROVAL_QUEUE,
  AgentHarnessDependencies,
  bindAgentHarness,
} from '../../../src/modules/agent-harness/composition';
import { verifyAuditChain } from '../../../src/modules/agent-harness/domain/audit';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  TRANSCRIPT_READ_KIND,
  TranscriptRead,
} from '../../../src/modules/agent-harness/domain/transcript-read-audit';
import { ApprovalView } from '../../../src/modules/agent-harness/projection/timeline';

const ORG = 'org-a';
const SECRET_ARGS = '{"to":"ceo@example.test","body":"the Q3 layoffs list"}';

const run = (runId: string, actorId: string): HarnessRun =>
  ({
    runId,
    orgId: ORG,
    sessionId: `s-${runId}`,
    actorId,
    status: 'waiting_approval',
    updatedAt: 1,
    harnessMajor: 2,
    openInteractions: ['ix.approve.t1.0', 'ix.approve.t1.1', 'ix.unknown.t1.2'],
  }) as unknown as HarnessRun;

const RUNS = [run('r-ana', 'u-ana'), run('r-bo', 'u-bo'), run('r-approver', 'u-approver')];

const approval = (interactionId: string): ApprovalView => ({
  interactionId,
  toolCallId: `call-${interactionId}`,
  toolName: 'mail.send',
  args: SECRET_ARGS,
  argsSummary: 'Send the Q3 layoffs list',
  status: 'pending',
  afterTurns: 0,
  decidedBefore: 0,
  seq: 1,
});

/** Who is in which session: Bo shares Ana's chat, nobody else is in any. */
const PARTICIPANT: Record<string, Set<string>> = { 's-r-ana': new Set(['u-ana', 'u-bo']), 's-r-bo': new Set(['u-bo']) };

function world(options: { failing?: boolean } = {}) {
  const chain = new InMemoryAuditChainStore();
  let now = Date.parse('2026-10-27T10:00:00.000Z');
  const audit = new ChainTranscriptReadAudit<unknown>(chain, (work) => work(undefined), () => new Date((now += 1)));
  const reads: TranscriptRead[] = [];
  const recordRead = async (read: TranscriptRead): Promise<void> => {
    if (options.failing === true) throw new Error('the audit chain is unavailable');
    reads.push(read);
    await audit.record(read);
  };
  const store = {
    pendingApprovalRuns: async (orgId: string, filter: { actorId?: string }) =>
      RUNS.filter((r) => r.orgId === orgId && (filter.actorId === undefined || r.actorId === filter.actorId)),
  };
  const interactions = {
    standing: async (viewer: { actorId: string }) => ({ isApprover: viewer.actorId === 'u-approver', isOrgAdmin: viewer.actorId === 'u-admin' }),
    administersAnyTeam: async () => false,
    authorityFor: async (viewer: { actorId: string }, r: HarnessRun) => {
      const participant = PARTICIPANT[r.sessionId]?.has(viewer.actorId) === true;
      const approver = viewer.actorId === 'u-approver';
      const admin = viewer.actorId === 'u-admin';
      const actor = r.actorId === viewer.actorId;
      return { approve: approver || actor, deny: approver || admin || actor || participant, participant };
    },
  };
  const queue = new ApprovalQueueService(
    store as never,
    interactions as never,
    { approvals: async () => [approval('ix.approve.t1.0'), approval('ix.approve.t1.1')] },
    recordRead,
    // Whose runs are read is the reach's own test; here the queue reads the org's runs for whoever may decide them.
    { othersRuns: async () => true },
  );
  return { chain, queue, reads };
}

describe('SEC-LG-02 transcript reads by someone outside the session are audited (C6.38)', () => {
  it('LG2-1: an approver outside the sessions reads held calls with their arguments: one chain entry per run, never the content', async () => {
    const { chain, queue } = world();
    const items = await queue.pending({ orgId: ORG, actorId: 'u-approver', limit: 50 });
    expect(items.filter((item) => item.args === SECRET_ARGS).map((item) => item.runId)).to.include.members(['r-ana', 'r-bo']);

    const records = chain.records.get(ORG) ?? [];
    expect(records.map((record) => [record.kind, record.runId, record.sessionId, record.streamId, record.fields])).to.deep.equal([
      [TRANSCRIPT_READ_KIND, 'r-ana', 's-r-ana', 'txr:s-r-ana', { by: 'p_u-approver', of: 'p_u-ana', view: 'approvals_queue', calls: 2 }],
      [TRANSCRIPT_READ_KIND, 'r-bo', 's-r-bo', 'txr:s-r-bo', { by: 'p_u-approver', of: 'p_u-bo', view: 'approvals_queue', calls: 2 }],
    ]);
    expect(JSON.stringify(records)).to.not.include('layoffs');
    expect(verifyAuditChain(records).ok).to.equal(true);
  });

  it('LG2-2: a read inside the ACL is not an outsider read: the run’s actor, a participant, and an admin shown no arguments', async () => {
    for (const viewer of ['u-ana', 'u-bo', 'u-admin']) {
      const { chain, queue } = world();
      await queue.pending({ orgId: ORG, actorId: viewer, limit: 50 });
      expect(chain.records.get(ORG) ?? [], viewer).to.deep.equal([]);
    }
    const { queue } = world();
    for (const item of await queue.pending({ orgId: ORG, actorId: 'u-admin', limit: 50 })) {
      expect(item.argsHidden, 'AQ-2 still holds').to.equal(true);
    }
  });

  it('LG2-3: a read that cannot be audited sends nothing', async () => {
    const { queue } = world({ failing: true });
    let queueError: unknown;
    await queue.pending({ orgId: ORG, actorId: 'u-approver', limit: 50 }).catch((error: unknown) => {
      queueError = error;
    });
    expect(String(queueError)).to.include('audit chain is unavailable');
  });

  it('LG2-5: the production composition gives the queue the chain recorder', () => {
    const container = new Container();
    bindAgentHarness(container, {
      sessions: {},
      conversations: {},
      history: {},
      agentAccess: {},
      rollout: {},
      authority: {},
      keyEncryptionKey: {},
      memory: { policies: { read: async () => ({ value: null, revision: null }) } },
    } as unknown as AgentHarnessDependencies);
    const queue = container.get(AGENT_HARNESS_APPROVAL_QUEUE) as unknown as { recordRead?: unknown };
    expect(queue.recordRead).to.be.a('function');
  });
});
