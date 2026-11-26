/**
 * WP-64 (ask once): a connect the person skipped, or let time out, anywhere in a run's tree holds for
 * every run of it. Each run journals on its own stream and a worker reads only its own, so Node names the tree's
 * skips on the claim; the worker's kernels then refuse the item at once instead of showing another card.
 */
import { expect } from 'chai';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { runTreeOf, skippedConnectsIn } from '../../../src/modules/agent-harness/domain/answered-run';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';

const connect = (ix: string, instanceId: string) => ({
  type: 'interaction.requested',
  data: { interactionId: ix, kind: 'connect', connect: { item: 'GitHub', itemKind: 'toolset', instanceId } },
});
const resolved = (ix: string, decision: string) => ({ type: 'interaction.resolved', data: { interactionId: ix, decision } });

const PARENTS = [
  { runId: 'root', parentRunId: null },
  { runId: 'child', parentRunId: 'root' },
  { runId: 'grandchild', parentRunId: 'child' },
  { runId: 'sibling', parentRunId: 'root' },
  { runId: 'next-turn', parentRunId: null },
];

describe('WP-64 a run tree’s skipped connects', () => {
  it('WP64-TREE-1: the tree is the root and every run below it, never another turn of the conversation', () => {
    expect([...runTreeOf(PARENTS, 'grandchild')].sort()).to.deep.equal(['child', 'grandchild', 'root', 'sibling']);
    expect([...runTreeOf(PARENTS, 'next-turn')]).to.deep.equal(['next-turn']);
  });

  it('WP64-TREE-2: skipped and timed-out waits count; a connected one does not', () => {
    expect(
      skippedConnectsIn([
        connect('ix.connect.t1.0', 'gh'), resolved('ix.connect.t1.0', 'denied'),
        connect('ix.connect.t2.0', 'jira'), resolved('ix.connect.t2.0', 'expired'),
        connect('ix.connect.t3.0', 'slack'), resolved('ix.connect.t3.0', 'approved'),
        connect('ix.connect.t4.0', 'notion'),
      ]).sort(),
    ).to.deep.equal(['gh', 'jira']);
  });

  it('WP64-TREE-3: a claim names what a parent or a sibling skipped; a run alone in its tree reads nothing', async () => {
    const journals: Record<string, unknown[]> = {
      root: [connect('ix.connect.t1.0', 'gh'), resolved('ix.connect.t1.0', 'expired')],
      sibling: [connect('ix.connect.t2.0', 'jira'), resolved('ix.connect.t2.0', 'denied')],
      'next-turn': [connect('ix.connect.t1.0', 'notion'), resolved('ix.connect.t1.0', 'denied')],
    };
    const reads: string[] = [];
    const service = new ExecutionService({
      store: { sessionRunParents: async () => PARENTS } as never,
      journal: {
        readRun: async (_org: string, runId: string) => {
          reads.push(runId);
          return journals[runId] ?? [];
        },
      } as never,
      journals: {} as never,
      capabilities: { current: () => ({}) } as never,
    });
    const child = { orgId: 'o', sessionId: 's', runId: 'grandchild', parent: { runId: 'child', invocationId: 'i', depth: 2 } } as HarnessRun;
    expect(await service.treeSkippedConnects(child)).to.deep.equal(['gh', 'jira']);
    reads.length = 0;
    const alone = { orgId: 'o', sessionId: 's', runId: 'next-turn' } as HarnessRun;
    expect(await service.treeSkippedConnects(alone)).to.deep.equal([]);
    expect(reads, 'its own skips the worker reads itself').to.deep.equal([]);
  });
});
