/**
 * WP100-FB-N1..N4 (G-48, C20.49): an engine's provider fallback goes through the Model Gateway, with the loop's policy.
 *
 * The run's pin is the one source: Node pins the agent's other models as alternates (`alternateModelRoutes`, at most
 * two, an outage only) for a loop run, and now for an engine run and a code_task child reached through the org's
 * configured key (`engineAlternates`). The gateway's grant carries each alternate's triggers, the gateway moves an
 * engine's call there by the kernel's `ModelPolicy`, and a child's move shows on its parent's answer.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  childExecutor,
  childGrants,
  childRunSpec,
} from '../../../src/modules/agent-harness/domain/child-runs';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { grantedRoutes } from '../../../src/modules/agent-harness/domain/gateway-token';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import {
  ALTERNATE_MODEL_ON,
  engineAlternates,
  PinnedRunSpec,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { ChildInteractions } from '../../../src/modules/agent-harness/projection/child-interactions';

const table = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/child-grants.json'),
    'utf8',
  ),
) as { parentSpec: RunSpec };

const ORG_KEY = { route: 'gateway', accountKind: 'org_api_key', pays: 'org' } as const;
const ON = [...ALTERNATE_MODEL_ON];
const RELEASE = RELEASE_ENGINE_PROFILES[0]!;

const pinned = (spec: RunSpec): PinnedRunSpec => ({
  spec,
  specDigest: `sha256:${'0'.repeat(64)}`,
  capabilities: {} as PinnedRunSpec['capabilities'],
  capabilitiesDigest: `sha256:${'0'.repeat(64)}`,
});

/** A loop parent of an agent granting three models: its own and two alternates, as `ModelFallbackStep` pins them. */
const loopParent: RunSpec = {
  ...table.parentSpec,
  executor: { kind: 'loop' },
  model: {
    ...table.parentSpec.model,
    policy: [
      { provider: 'anthropic', model: 'claude-x' },
      { provider: 'openai', model: 'gpt-x', on: ON },
      { provider: 'azureopenai', model: 'gpt-y', on: ON },
    ],
  },
};

describe('WP-100: the alternate model for engines (G-48)', () => {
  it("WP100-FB-N1: the gateway's grant carries each alternate's triggers, never the first route's", () => {
    const spec: RunSpec = {
      ...loopParent,
      model: {
        ...loopParent.model,
        policy: [
          { provider: 'anthropic', model: 'claude-x', access: ORG_KEY },
          { provider: 'openai', model: 'gpt-x', on: ON, access: ORG_KEY },
        ],
      },
    };
    const { routes } = grantedRoutes(spec);
    expect(routes[0]).to.not.have.property('on');
    expect(routes[1]).to.deep.include({ provider: 'openai', model: 'gpt-x', on: ON, pays: 'org' });
  });

  it("WP100-FB-N2: an engine run on the org's key gets the alternates, reached the same way; a person's own key gets none", () => {
    const candidates = [
      { provider: 'openai', model: 'gpt-x', on: ON },
      { provider: 'azureopenai', model: 'gpt-y', on: ON },
    ];
    expect(engineAlternates(ORG_KEY, candidates)).to.deep.equal(
      candidates.map((route) => ({ ...route, access: ORG_KEY })),
    );
    for (const own of [
      { route: 'gateway', accountKind: 'user_api_key', accountId: 'users/u1/openAI', pays: 'user' },
      { route: 'gateway', accountKind: 'org_cloud', accountId: 'cloud/acct', pays: 'org' },
      { route: 'vendor_sandbox_login', accountKind: 'user_plan_sandbox_login', pays: 'vendor_plan' },
    ] as const) {
      expect(engineAlternates(own, candidates), own.accountKind).to.deep.equal([]);
    }
  });

  it("WP100-FB-N3: a code_task child of a loop parent keeps the parent's alternates after its own model, on the org's key", () => {
    const grants = childGrants(loopParent, {}, 1).grants!;
    const executor = childExecutor(loopParent, { kind: 'engine' }, RELEASE).executor!;
    const decided = {
      route: { provider: 'anthropic', model: 'claude-x' },
      model: { modelKey: 'm_1', modelName: 'claude-x' },
      access: ORG_KEY,
    };
    const child = childRunSpec(pinned(loopParent), grants, undefined, undefined, executor, decided);
    expect(child.spec.model.policy).to.deep.equal([
      { provider: 'anthropic', model: 'claude-x', access: ORG_KEY },
      { provider: 'openai', model: 'gpt-x', on: ON, access: ORG_KEY },
      { provider: 'azureopenai', model: 'gpt-y', on: ON, access: ORG_KEY },
    ]);
    // On a person's own key the child has its one route, as before (A1).
    const own = {
      route: { provider: 'anthropic', model: 'claude-x' },
      access: { route: 'gateway', accountKind: 'user_api_key', accountId: 'users/u1/anthropic', pays: 'user' } as const,
    };
    const onOwnKey = childRunSpec(pinned(loopParent), grants, undefined, undefined, executor, own);
    expect(onOwnKey.spec.model.policy).to.have.length(1);
  });

  it("WP100-FB-N4: a code_task child's move to another model shows on its parent's stream, once", async () => {
    const child = 'run-child';
    const event = (seq: number, type: string, data: Record<string, unknown>, writer: 'node' | 'worker'): JournalEvent =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq,
        runId: child,
        type,
        schemaVersion: 1,
        critical: false,
        ts: '2026-11-06T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent;
    const journal = [
      event(1, 'run.admitted', { runKind: 'child', principalRef: 'p_user' }, 'node'),
      // Written by Node from the Model Gateway's report: the engine's call moved.
      event(2, 'model.fallback', { from: { provider: 'anthropic', model: 'claude-x' }, to: { provider: 'openai', model: 'gpt-x' }, reason: '5xx' }, 'node'),
    ];
    const relay = new ChildInteractions(async () => journal, 'run-1');
    relay.track([{ childRunId: child, role: 'code_task', spawnSeq: 3, live: true }]);
    const notices = (await relay.pull(child)).map((f) => f.frame).filter((f) => f['name'] === 'notice');
    expect(notices).to.deep.equal([
      { type: 'CUSTOM', name: 'notice', value: { code: 'model_switched', item: 'gpt-x' }, runId: child },
    ]);
    expect((await relay.pull(child)).filter((f) => f.frame['name'] === 'notice')).to.deep.equal([]);
  });
});
