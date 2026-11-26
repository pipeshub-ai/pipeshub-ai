/**
 * CH-1, CH-2 (E1): the ⊆ rule's `executor` dimension and an engine child's pin.
 * The same rule as Python's `harness_core.journal.children.child_executor`.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  childAccessRefusal,
  childExecutor,
  childFeatures,
  childGrants,
  childNeedsAccess,
  childRunSpec,
} from '../../../src/modules/agent-harness/domain/child-runs';
import {
  engineExecutor,
  profileDigest,
  RELEASE_ENGINE_PROFILES,
} from '../../../src/modules/agent-harness/domain/engine-profiles';
import { PinnedRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { spawnChildRunSchema } from '../../../src/modules/agent-harness/transport/schemas';

const table = JSON.parse(
  readFileSync(
    join(
      __dirname,
      '../../../../../../contracts/harness/vectors/child-grants.json',
    ),
    'utf8',
  ),
) as { parentSpec: RunSpec };

const RELEASE = RELEASE_ENGINE_PROFILES[0]!;
const loopParent: RunSpec = { ...table.parentSpec, executor: { kind: 'loop' } };
const engineParent: RunSpec = {
  ...table.parentSpec,
  executor: engineExecutor(RELEASE),
};

describe('child runs: the executor dimension (E1)', () => {
  it('CH-1: no executor asked is the loop, whatever the parent runs on', () => {
    expect(childExecutor(loopParent, undefined).executor).to.deep.equal({
      kind: 'loop',
    });
    expect(childExecutor(engineParent, undefined).executor).to.deep.equal({
      kind: 'loop',
    });
  });

  it("CH-1: an engine child narrows its engine parent's profile, pinned with its digest", () => {
    const decision = childExecutor(engineParent, {
      kind: 'engine',
      profile: { limits: { maxSteps: 5 } },
    });
    expect(decision.refusal).to.equal(null);
    const executor = decision.executor!;
    expect(executor.kind).to.equal('engine');
    if (executor.kind !== 'engine') return;
    expect(executor.engine.profile?.limits.maxSteps).to.equal(5);
    expect(executor.engine.profileDigest).to.equal(
      profileDigest(executor.engine.profile!),
    );
  });

  it("CH-1: a widening of the parent's engine is refused naming executor, never narrowed", () => {
    const steps = RELEASE.limits.maxSteps ?? 50;
    const decision = childExecutor(engineParent, {
      kind: 'engine',
      profile: { limits: { maxSteps: steps + 1 } },
    });
    expect(decision.refusal).to.deep.include({
      code: 'CHILD_GRANT_TOO_LARGE',
      dimension: 'executor',
    });
  });

  it("CH-1: a loop parent's engine child needs the org's engine authority", () => {
    expect(childExecutor(loopParent, { kind: 'engine' }).refusal).to.deep.equal(
      {
        code: 'CHILD_GRANT_TOO_LARGE',
        dimension: 'executor',
        items: ['engine'],
      },
    );
    expect(
      childExecutor(loopParent, { kind: 'engine' }, RELEASE).executor?.kind,
    ).to.equal('engine');
  });

  it('CH-2: the child spec pins the engine executor and the child needs executor_engine', () => {
    const grants = childGrants(loopParent, {}, 1).grants!;
    const executor = childExecutor(
      loopParent,
      { kind: 'engine' },
      RELEASE,
    ).executor!;
    const parent: PinnedRunSpec = {
      spec: loopParent,
      specDigest: `sha256:${'0'.repeat(64)}`,
      capabilities: {} as PinnedRunSpec['capabilities'],
      capabilitiesDigest: `sha256:${'0'.repeat(64)}`,
    };
    const pinned = childRunSpec(parent, grants, undefined, undefined, executor);
    expect(pinned.spec.executor).to.deep.equal(executor);
    expect(childFeatures(pinned)).to.include('executor_engine');
    expect(childFeatures(childRunSpec(parent, grants))).to.not.include(
      'executor_engine',
    );
  });

  it("CH-5 (A1): a loop parent's engine child gets an access route decided, pinned and held to the ⊆ rule", () => {
    const grants = childGrants(loopParent, {}, 1).grants!;
    const executor = childExecutor(loopParent, { kind: 'engine' }, RELEASE)
      .executor!;
    // The parent's first route pins no access: an engine child needs one decided.
    expect(childNeedsAccess(loopParent, grants, executor)).to.equal(true);
    expect(childNeedsAccess(loopParent, grants, { kind: 'loop' })).to.equal(
      false,
    );
    const decided = {
      route: { provider: 'anthropic', model: 'claude-x' },
      model: { modelKey: 'm_1', modelName: 'claude-x' },
      access: {
        route: 'gateway' as const,
        accountKind: 'org_api_key' as const,
        pays: 'org' as const,
      },
    };
    expect(childAccessRefusal(grants, decided)).to.equal(null);
    const parent: PinnedRunSpec = {
      spec: loopParent,
      specDigest: `sha256:${'0'.repeat(64)}`,
      capabilities: {} as PinnedRunSpec['capabilities'],
      capabilitiesDigest: `sha256:${'0'.repeat(64)}`,
    };
    const pinned = childRunSpec(
      parent,
      grants,
      undefined,
      undefined,
      executor,
      decided,
    );
    expect(pinned.spec.model.policy).to.deep.equal([
      { provider: 'anthropic', model: 'claude-x', access: decided.access },
    ]);
    // A route the parent does not hold (another model) is refused on `access`.
    expect(
      childAccessRefusal(grants, {
        ...decided,
        route: { provider: 'openai', model: 'gpt-x' },
      }),
    ).to.deep.include({ code: 'CHILD_GRANT_TOO_LARGE', dimension: 'access' });
    // A child granted only the parent's pinned (user-key) route needs nothing decided.
    const narrowed = childGrants(
      loopParent,
      { access: ['openai:gpt-5@user_api_key:users/user-1/openAI'] },
      1,
    ).grants!;
    expect(childNeedsAccess(loopParent, narrowed, executor)).to.equal(false);
  });

  it('CH-2: the admission body takes an engine executor, nothing else', () => {
    const base = { attemptId: 'a', fence: 1, invocationId: 'i', goal: 'g' };
    expect(
      spawnChildRunSchema.shape.body.safeParse({
        ...base,
        executor: { kind: 'engine' },
      }).success,
    ).to.equal(true);
    expect(
      spawnChildRunSchema.shape.body.safeParse({
        ...base,
        executor: { kind: 'loop' },
      }).success,
    ).to.equal(false);
    expect(
      spawnChildRunSchema.shape.body.safeParse({
        ...base,
        executor: { kind: 'engine', profile: { limits: { maxSteps: null } } },
      }).success,
    ).to.equal(false);
  });
});
