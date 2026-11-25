/**
 * CPD5-52, CPD5-53: `effectivePolicy` names the run's pin when there is one,
 * and `GET …/runs/{runId}/resolved` answers the pin of the run its scope
 * authorized (never a client-supplied id), an empty view for a run without one (CMDUI2).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { effectivePolicyOf } from '../../../src/modules/agent-harness/domain/effective-policy';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';

const DIGEST = `sha256:${'2'.repeat(64)}` as const;
const RESOLVED = {
  schemaVersion: 1 as const,
  headRevision: 41,
  routing: { path: 'durable' as const, reason: null },
  scope: { agentKey: 'agent-1', projectId: null },
  effectiveSet: { items: [], digest: DIGEST },
  effectiveConfig: { schemaVersion: 1 as const, dimensions: {} },
  llm: null,
  deploymentDigest: DIGEST,
  digest: DIGEST,
};
const RUN = { runId: 'run-1', sessionId: 'session-1', orgId: 'org-auth', actorId: 'user-auth' };

describe('CPD-5 the run’s pin on the run API', () => {
  it('CPD5-52: effectivePolicy names the pin it was derived from; a run without one has no field', () => {
    const spec = {
      model: { policy: [] },
      grants: { tools: [], sandboxProfile: 'none', egress: 'none' },
      budgets: { maxTurns: 1, maxTokens: 1, maxCostUsd: 1, activeTimeS: 1, maxChildren: 0 },
    } as unknown as RunSpec;
    expect(effectivePolicyOf(spec, RESOLVED).resolved).to.deep.equal({ headRevision: 41, digest: DIGEST });
    expect(effectivePolicyOf(spec)).not.to.have.property('resolved');
  });

  it('CPD5-53: answers the pin of the run its scope authorized, never a client-supplied one', async () => {
    const view = { resolved: RESOLVED, liveApplied: [], heldRequests: [], blockedRequests: [] };
    const runs = { get: sinon.stub().resolves(RUN), resolved: sinon.stub().resolves(view) };
    const res = { status: sinon.stub(), json: sinon.stub() };
    res.status.returns(res);
    await new AgentHarnessSessionController(runs as never).resolved(
      {
        user: { orgId: 'org-auth', userId: 'user-auth' },
        params: { agentKey: 'agent-1', sessionId: 'session-1', runId: 'run-1' },
        body: { orgId: 'attacker-org', runId: 'run-other' },
      } as never,
      res as never,
      sinon.stub() as never,
    );
    expect(runs.get.firstCall.args[1]).to.equal('run-1');
    expect(runs.resolved.calledOnceWithExactly(RUN)).to.equal(true);
    expect(res.json.firstCall.args[0]).to.deep.equal(view);
  });

  it('CPD5-53 (CMDUI2): a run the control plane did not pin answers an empty view, not a 404', async () => {
    const service = new SessionRunService({ resolvedView: async () => null } as never, {} as never);
    expect(await service.resolved(RUN as never)).to.deep.equal({ resolved: null, liveApplied: [], heldRequests: [], blockedRequests: [] });
  });
});
