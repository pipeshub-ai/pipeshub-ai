import { expect } from 'chai';
import sinon from 'sinon';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  effectivePolicyOf,
  UNPINNED_POLICY,
} from '../../../src/modules/agent-harness/domain/effective-policy';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { toolPart } from '../../../src/modules/agent-harness/projection/transcript';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';

const DIGEST = `sha256:${'1'.repeat(64)}` as const;

const loopSpec = (): RunSpec =>
  ({
    schemaVersion: 2,
    harnessMajor: 2,
    foldVersion: 8,
    runKind: 'interactive',
    principalRef: 'p_user',
    model: { policy: [{ provider: 'openai', model: 'gpt-x' }], promptVersion: 'p1' },
    grants: {
      tools: [
        { toolId: 'slack@inst-1/post_message', descriptorDigest: DIGEST },
        { toolId: 'knowledgegraph@default/fetch_record', descriptorDigest: DIGEST },
      ],
      sandboxProfile: 'none',
      egress: 'none',
    },
    budgets: {
      maxTurns: 8,
      maxTokens: 100000,
      maxCostUsd: 1.5,
      activeTimeS: 600,
      calendarDeadline: '2027-01-01T00:00:00Z',
      maxChildren: 0,
    },
    capabilitiesDigest: DIGEST,
    extensions: {},
  }) as RunSpec;

const engineSpec = (): RunSpec =>
  ({
    ...loopSpec(),
    schemaVersion: 3,
    model: {
      policy: [
        {
          provider: 'anthropic',
          model: 'claude-x',
          access: { route: 'gateway', accountKind: 'org_cloud', accountId: 'cloud/acct-1', pays: 'org' },
        },
      ],
      promptVersion: 'p1',
    },
    executor: {
      kind: 'engine',
      engine: {
        kind: 'codex',
        version: '0.160.0',
        binaryDigest: DIGEST,
        profileDigest: DIGEST,
        profile: {
          engine: { kind: 'codex', version: '0.160.0', binaryDigest: DIGEST },
          mode: 'plan',
          isolationMin: 'docker',
          egress: 'none',
          tools: { maxTools: 64, effects: ['read', 'write'] },
          limits: { maxSteps: null, maxWallS: null, maxTokens: null, maxUsd: null },
          subAgents: { enabled: false, maxDepth: 1, maxConcurrent: 1 },
          reasoningEffort: null,
          approvals: { read: 'allow', workspace: 'allow', write: 'ask', execute: 'ask' },
          skills: null,
          accountKinds: ['org_api_key'],
        },
      },
    },
  }) as RunSpec;

describe('agent-harness effective policy (SEC1)', () => {
  it('PV-1: an engine run shows its grant, routes, budgets and profile, from the pinned spec', () => {
    expect(effectivePolicyOf(engineSpec())).to.deep.equal({
      pinned: true,
      tools: ['knowledgegraph@default/fetch_record', 'slack@inst-1/post_message'],
      sandboxProfile: 'none',
      egress: 'none',
      modelRoutes: ['anthropic:claude-x@org_cloud:cloud/acct-1'],
      budgets: { maxTurns: 8, maxTokens: 100000, maxCostUsd: 1.5, activeTimeS: 600, maxChildren: 0 },
      engine: {
        kind: 'codex',
        version: '0.160.0',
        mode: 'plan',
        egress: 'none',
        toolEffects: ['read', 'write'],
        maxTools: 64,
        approvals: { read: 'allow', workspace: 'allow', write: 'ask', execute: 'ask' },
      },
    });
  });

  it('PV-2: a loop run has no engine; an unpinned run claims nothing', () => {
    const policy = effectivePolicyOf(loopSpec());
    expect(policy.pinned).to.equal(true);
    expect(policy).to.not.have.property('engine');
    expect(policy.modelRoutes).to.deep.equal(['openai:gpt-x']);
    expect(effectivePolicyOf(null)).to.deep.equal({ pinned: false, tools: [], modelRoutes: [] });
  });

  it('PV-1: the service answers for the authorized run only, and unpinned when the store has none', async () => {
    const executions = { effectivePolicy: sinon.stub().resolves(null) };
    const service = new SessionRunService(executions as never, {} as never);
    const run = { orgId: 'org-1', runId: 'run-1' } as never;
    expect(await service.effectivePolicy(run)).to.deep.equal(UNPINNED_POLICY);
    expect(executions.effectivePolicy.calledOnceWithExactly('org-1', 'run-1')).to.equal(true);
  });

  it('PV-1: GET run asks for the policy of the run its scope authorized, never a client-supplied one', async () => {
    const run = {
      runId: 'run-1',
      sessionId: 'session-1',
      profile: 'dispatched',
      status: 'running',
      outcome: null,
      revision: 1,
      absoluteDeadlineAt: 100,
      createdAt: 1,
      updatedAt: 2,
      orgId: 'org-auth',
      actorId: 'user-auth',
    };
    const policy = effectivePolicyOf(engineSpec());
    const runs = { get: sinon.stub().resolves(run), effectivePolicy: sinon.stub().resolves(policy) };
    const res = { status: sinon.stub(), json: sinon.stub() };
    res.status.returns(res);
    await new AgentHarnessSessionController(runs as never).get(
      {
        user: { orgId: 'org-auth', userId: 'user-auth' },
        params: { agentKey: 'agent-1', sessionId: 'session-1', runId: 'run-1' },
        body: { orgId: 'attacker-org', runId: 'run-other' },
      } as never,
      res as never,
      sinon.stub<unknown[]>(),
    );
    expect(runs.effectivePolicy.calledOnceWithExactly(run)).to.equal(true);
    expect(res.json.firstCall.args[0].effectivePolicy).to.deep.equal(policy);
  });

  it('PV-3: a blocked call names the rule that blocked it', () => {
    let seq = 0;
    const event = (type: string, data: Record<string, unknown>): JournalEvent =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: (seq += 1),
        runId: 'run-1',
        attemptId: 'run-1.a1',
        fence: 1,
        type,
        schemaVersion: 1,
        critical: true,
        ts: `2026-10-08T12:00:${String(seq).padStart(2, '0')}.000Z`,
        writer: 'worker',
        visibility: 'owner',
        data,
      }) as JournalEvent;
    const timeline = foldTimeline(
      [
        event('turn.started', { turn: 1 }),
        event('model.response', {
          messageRef: inlineContentRef(
            canonicalJsonBytes({ text: '', toolCalls: [{ id: 'c1', name: 'post_message', args: {} }] }),
          ),
          toolCalls: [{ callId: 'c1', modelName: 'post_message', argsDigest: DIGEST }],
          usage: { inputTokens: 0, outputTokens: 0 },
          provider: 'p',
          transport: 't',
        }),
        event('tool.denied', { invocationId: 't1.0', rule: 'flow_external_write' }),
        event('tool.outcome', { invocationId: 't1.0', status: 'error' }),
      ],
      'run-1',
    );
    const call = timeline.turns[0]!.calls[0]!;
    expect(call).to.include({ status: 'blocked', blockedBy: 'flow_external_write' });
    expect(toolPart(call, 'run-1')).to.include({ status: 'blocked', blockedBy: 'flow_external_write' });
  });
});
