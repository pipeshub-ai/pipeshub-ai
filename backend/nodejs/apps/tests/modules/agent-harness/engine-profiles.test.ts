/**
 * E4a engine profiles, pure: the shared vector table (EP-7, EP-8), the
 * registry's coverage (EP-3), per-dimension narrowing (EP-1, EP-2), invalid
 * layers (EP-9) and the properties (EP-4..EP-6).
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pinnedEngineProfileSchema } from '../../../src/modules/agent-harness/contracts/generated/core/engine-profile';
import {
  EngineProfileNarrowing,
  engineExecutor,
  narrowProfile,
  parseNarrowing,
  type AcpOptIn,
  PinnedEngineProfile,
  ENGINE_PROFILE_DIMENSIONS,
  profileDigest,
  ProfileLayers,
  RELEASE_ENGINE_PROFILES,
  resolveEngineProfile,
} from '../../../src/modules/agent-harness/domain/engine-profiles';

interface VectorCase {
  id: string;
  behaviour: string;
  acpOptIn?: AcpOptIn;
  layers: ProfileLayers;
  expect:
    | { profile: PinnedEngineProfile; profileDigest: string }
    | { refusal: { code: 'widening' | 'invalid'; dimension?: string; layer?: string } };
}

const VECTORS = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/engine-profiles.json'),
    'utf8',
  ),
) as { release: PinnedEngineProfile[]; releases: PinnedEngineProfile[]; cases: VectorCase[] };

const asVector = (layers: ProfileLayers, acpOptIn: AcpOptIn = 'release'): VectorCase['expect'] => {
  const decision = resolveEngineProfile(layers, VECTORS.releases, acpOptIn);
  if (decision.refusal === null) {
    return { profile: decision.profile, profileDigest: profileDigest(decision.profile) };
  }
  return decision.refusal.code === 'ENGINE_PROFILE_INVALID'
    ? { refusal: { code: 'invalid' } }
    : {
        refusal: {
          code: 'widening',
          ...(decision.refusal.dimension !== undefined ? { dimension: decision.refusal.dimension } : {}),
          ...(decision.refusal.layer !== undefined ? { layer: decision.refusal.layer } : {}),
        },
      };
};

const TOP: PinnedEngineProfile = {
  engine: RELEASE_ENGINE_PROFILES[0]!.engine,
  mode: 'build',
  isolationMin: 'gvisor',
  egress: 'allowlist:pypi',
  tools: { maxTools: 40, effects: ['execute', 'read', 'workspace', 'write'] },
  limits: { maxSteps: 50, maxWallS: 1800, maxTokens: 1_000_000, maxUsd: 5 },
  subAgents: { enabled: true, maxDepth: 2, maxConcurrent: 3 },
  reasoningEffort: 'high',
  approvals: { read: 'allow', workspace: 'allow', write: 'ask', execute: 'ask' },
  skills: ['pdf', 'xlsx'],
  accountKinds: ['org_api_key', 'org_cloud', 'user_api_key'],
};

describe('E4a engine profiles: the shared vector table (EP-7, EP-8)', () => {
  it('the release profiles are this release (allowlist.json)', () => {
    expect(RELEASE_ENGINE_PROFILES).to.deep.equal(VECTORS.release);
    for (const profile of RELEASE_ENGINE_PROFILES) pinnedEngineProfileSchema.parse(profile);
  });

  for (const vector of VECTORS.cases) {
    it(`${vector.id}: ${vector.behaviour}`, () => {
      expect(asVector(vector.layers, vector.acpOptIn)).to.deep.equal(vector.expect);
    });
  }

  it('the pinned executor carries the profile and the digest Python computes', () => {
    for (const vector of VECTORS.cases) {
      if (!('profile' in vector.expect)) continue;
      const executor = engineExecutor(vector.expect.profile);
      expect(executor.engine.profileDigest).to.equal(vector.expect.profileDigest);
      expect(executor.engine.profile).to.deep.equal(vector.expect.profile);
      expect(executor.engine.binaryDigest).to.equal(vector.expect.profile.engine.binaryDigest);
    }
  });
});

describe('E4a engine profiles: the registry (EP-1, EP-2, EP-3, EP-9)', () => {
  it('EP-3: every key of the pinned profile has a dimension, in the table order both languages report', () => {
    const paths = ENGINE_PROFILE_DIMENSIONS.all().map(({ path }) => path);
    const governed = new Set(paths.map((path) => path.split('.')[0]));
    const hostOnly = ['access', 'resultSchema'];
    expect([...governed].filter((key) => !hostOnly.includes(key as string)).sort()).to.deep.equal(
      Object.keys(pinnedEngineProfileSchema.shape).sort(),
    );
    expect(paths).to.deep.equal([
      'engine', 'access', 'mode', 'isolationMin', 'egress', 'tools.maxTools', 'tools.effects',
      'limits.maxSteps', 'limits.maxWallS', 'limits.maxTokens', 'limits.maxUsd',
      'subAgents', 'reasoningEffort',
      'approvals.read', 'approvals.workspace', 'approvals.write', 'approvals.execute', 'skills',
      'accountKinds', 'resultSchema',
    ]);
  });

  const narrowings: Array<[EngineProfileNarrowing, Partial<PinnedEngineProfile>]> = [
    [{ engines: ['opencode'] }, {}],
    [{ mode: 'review' }, { mode: 'review' }],
    [{ isolationMin: 'microvm' }, { isolationMin: 'microvm' }],
    [{ isolationMin: 'k8s' }, { isolationMin: 'k8s' }],
    [{ egress: 'none' }, { egress: 'none' }],
    [{ tools: { maxTools: 10 } }, { tools: { ...TOP.tools, maxTools: 10 } }],
    [{ tools: { effects: ['read'] } }, { tools: { ...TOP.tools, effects: ['read'] } }],
    [{ limits: { maxWallS: 60, maxUsd: 0.5 } }, { limits: { ...TOP.limits, maxWallS: 60, maxUsd: 0.5 } }],
    [{ reasoningEffort: 'medium' }, { reasoningEffort: 'medium' }],
    [{ approvals: { write: 'deny', read: 'ask' } }, { approvals: { ...TOP.approvals, write: 'deny', read: 'ask' } }],
    [{ skills: ['pdf'] }, { skills: ['pdf'] }],
    [{ accountKinds: ['user_api_key'] }, { accountKinds: ['user_api_key'] }],
    [{ subAgents: { enabled: true, maxDepth: 1, maxConcurrent: 3 } }, { subAgents: { enabled: true, maxDepth: 1, maxConcurrent: 3 } }],
  ];
  for (const [layer, changed] of narrowings) {
    it(`EP-1 narrows ${JSON.stringify(layer)}`, () => {
      expect(narrowProfile(TOP, layer)).to.deep.equal({ profile: { ...TOP, ...changed }, refusal: null });
    });
  }

  const widenings: Array<[Partial<PinnedEngineProfile>, EngineProfileNarrowing, string]> = [
    [{}, { engines: ['codex'] }, 'engine'],
    [{ mode: 'review' }, { mode: 'plan' }, 'mode'],
    [{}, { isolationMin: 'docker' }, 'isolationMin'],
    [{ egress: 'none' }, { egress: 'allowlist:pypi' }, 'egress'],
    [{}, { egress: 'allowlist:npm' }, 'egress'],
    [{}, { tools: { maxTools: 41 } }, 'tools.maxTools'],
    [{ tools: { maxTools: null, effects: ['read'] } }, { tools: { effects: ['read', 'write'] } }, 'tools.effects'],
    [{}, { limits: { maxTokens: 1_000_001 } }, 'limits.maxTokens'],
    [{}, { limits: { maxUsd: 5.01 } }, 'limits.maxUsd'],
    [{ subAgents: { enabled: false, maxDepth: 1, maxConcurrent: 1 } }, { subAgents: { enabled: true, maxDepth: 1, maxConcurrent: 1 } }, 'subAgents.enabled'],
    [{}, { subAgents: { enabled: true, maxDepth: 3, maxConcurrent: 1 } }, 'subAgents.maxDepth'],
    [{ reasoningEffort: 'low' }, { reasoningEffort: 'medium' }, 'reasoningEffort'],
    [{}, { approvals: { write: 'allow' } }, 'approvals.write'],
    [{}, { approvals: { execute: 'allow' } }, 'approvals.execute'],
    [{}, { skills: ['docx'] }, 'skills'],
    [{}, { accountKinds: ['user_plan_injected'] }, 'accountKinds'],
  ];
  for (const [base, layer, dimension] of widenings) {
    it(`EP-2 refuses ${JSON.stringify(layer)} naming ${dimension}`, () => {
      const decision = narrowProfile({ ...TOP, ...base }, layer);
      expect(decision.profile).to.equal(null);
      expect(decision.refusal).to.include({ code: 'ENGINE_PROFILE_WIDENING', dimension });
    });
  }

  for (const raw of [
    { limits: { maxSteps: null } },
    { skills: null },
    { accountKinds: [] },
    { accountKinds: ['org_api_key', 'org_api_key'] },
    { network: 'open' },
    { engines: ['opencode', 'opencode'] },
    { tools: { effects: ['read', 'read'] } },
    { approvals: { network: 'deny' } },
    { limits: { maxSteps: '50' } },
    [],
    'plan',
  ]) {
    it(`EP-9 ${JSON.stringify(raw)} is invalid, never absent`, () => {
      expect(parseNarrowing(raw).refusal?.code).to.equal('ENGINE_PROFILE_INVALID');
      const decision = resolveEngineProfile({ agent: raw });
      expect(decision.refusal).to.include({ code: 'ENGINE_PROFILE_INVALID', layer: 'agent' });
    });
  }
});

// --- properties ------------------------------------------------------------------------------

const EFFECTS = ['execute', 'read', 'workspace', 'write'] as const;
const LEVELS = ['allow', 'ask', 'deny'] as const;
const RANK: Record<string, number> = { allow: 0, ask: 1, deny: 2, low: 0, medium: 1, high: 2 };
const ISOLATION: Record<string, number> = { docker: 0, gvisor: 1, k8s: 1, managed: 1, microvm: 2 };
const WITHIN: Record<string, string[]> = { build: ['build', 'plan', 'review'], plan: ['plan'], review: ['review'] };
const SKILLS = ['docx', 'pdf', 'pptx', 'xlsx'];
const ACCOUNT_KINDS = ['org_api_key', 'org_cloud', 'user_api_key', 'user_plan_injected', 'user_plan_sandbox_login'] as const;

const optInt = fc.option(fc.integer({ min: 1, max: 100 }), { nil: null });
const sortedSubset = <T extends string>(items: readonly T[]) =>
  fc.subarray([...items]).map((picked) => [...picked].sort());
const profileArb: fc.Arbitrary<PinnedEngineProfile> = fc.record({
  engine: fc.constant(RELEASE_ENGINE_PROFILES[0]!.engine),
  mode: fc.constantFrom('plan', 'build', 'review'),
  isolationMin: fc.constantFrom('docker', 'gvisor', 'k8s', 'managed', 'microvm'),
  egress: fc.constantFrom('none', 'allowlist:pypi', 'allowlist:npm'),
  tools: fc.record({ maxTools: optInt, effects: sortedSubset(EFFECTS) }),
  limits: fc.record({
    maxSteps: optInt,
    maxWallS: optInt,
    maxTokens: optInt,
    maxUsd: fc.option(fc.integer({ min: 0, max: 20 }).map((q) => q / 4), { nil: null }),
  }),
  subAgents: fc.record({
    enabled: fc.boolean(),
    maxDepth: fc.integer({ min: 1, max: 3 }),
    maxConcurrent: fc.integer({ min: 1, max: 3 }),
  }),
  reasoningEffort: fc.constantFrom(null, 'low', 'medium', 'high'),
  approvals: fc.record({
    read: fc.constantFrom(...LEVELS),
    workspace: fc.constantFrom(...LEVELS),
    write: fc.constantFrom(...LEVELS),
    execute: fc.constantFrom(...LEVELS),
  }),
  skills: fc.option(sortedSubset(SKILLS), { nil: null }),
  accountKinds: fc.subarray([...ACCOUNT_KINDS], { minLength: 1 }).map((picked) => [...picked].sort()),
}) as fc.Arbitrary<PinnedEngineProfile>;

const layerArb: fc.Arbitrary<EngineProfileNarrowing> = fc.record(
  {
    engines: fc.constantFrom(['opencode'], ['codex']),
    mode: fc.constantFrom('plan', 'build', 'review'),
    isolationMin: fc.constantFrom('docker', 'gvisor', 'k8s', 'managed', 'microvm'),
    egress: fc.constantFrom('none', 'allowlist:pypi', 'allowlist:npm'),
    tools: fc.record(
      { maxTools: fc.integer({ min: 1, max: 100 }), effects: sortedSubset(EFFECTS) },
      { requiredKeys: [] },
    ),
    limits: fc.record(
      {
        maxSteps: fc.integer({ min: 1, max: 100 }),
        maxWallS: fc.integer({ min: 1, max: 100 }),
        maxTokens: fc.integer({ min: 1, max: 100 }),
        maxUsd: fc.integer({ min: 0, max: 20 }).map((q) => q / 4),
      },
      { requiredKeys: [] },
    ),
    subAgents: fc.record({
      enabled: fc.boolean(),
      maxDepth: fc.integer({ min: 1, max: 3 }),
      maxConcurrent: fc.integer({ min: 1, max: 3 }),
    }),
    reasoningEffort: fc.constantFrom('low', 'medium', 'high'),
    approvals: fc.record(
      {
        read: fc.constantFrom(...LEVELS),
        workspace: fc.constantFrom(...LEVELS),
        write: fc.constantFrom(...LEVELS),
        execute: fc.constantFrom(...LEVELS),
      },
      { requiredKeys: [] },
    ),
    skills: sortedSubset(SKILLS),
    accountKinds: fc.subarray([...ACCOUNT_KINDS], { minLength: 1 }).map((picked) => [...picked].sort()),
  },
  { requiredKeys: [] },
) as fc.Arbitrary<EngineProfileNarrowing>;

const atMost = (r: number | null, p: number | null): boolean => p === null || (r !== null && r <= p);
const within = (r: PinnedEngineProfile, p: PinnedEngineProfile): boolean =>
  JSON.stringify(r.engine) === JSON.stringify(p.engine) &&
  WITHIN[p.mode]!.includes(r.mode) &&
  ISOLATION[r.isolationMin]! >= ISOLATION[p.isolationMin]! &&
  (r.egress === 'none' || r.egress === p.egress) &&
  atMost(r.tools.maxTools, p.tools.maxTools) &&
  r.tools.effects.every((e) => p.tools.effects.includes(e)) &&
  atMost(r.limits.maxSteps, p.limits.maxSteps) &&
  atMost(r.limits.maxWallS, p.limits.maxWallS) &&
  atMost(r.limits.maxTokens, p.limits.maxTokens) &&
  atMost(r.limits.maxUsd, p.limits.maxUsd) &&
  (!r.subAgents.enabled ||
    (p.subAgents.enabled &&
      r.subAgents.maxDepth <= p.subAgents.maxDepth &&
      r.subAgents.maxConcurrent <= p.subAgents.maxConcurrent)) &&
  (p.reasoningEffort === null ||
    (r.reasoningEffort !== null && RANK[r.reasoningEffort]! <= RANK[p.reasoningEffort]!)) &&
  (Object.keys(p.approvals) as Array<keyof PinnedEngineProfile['approvals']>).every(
    (k) => RANK[r.approvals[k]]! >= RANK[p.approvals[k]]!,
  ) &&
  (p.skills === null || (r.skills !== null && r.skills.every((s) => p.skills!.includes(s)))) &&
  r.accountKinds.every((k) => p.accountKinds.includes(k));

/** `a ⊕ b`: b's dimensions over a's (tools, limits and approvals per field). */
const merge = (a: EngineProfileNarrowing, b: EngineProfileNarrowing): EngineProfileNarrowing => {
  const merged: EngineProfileNarrowing = { ...a, ...b };
  for (const key of ['tools', 'limits', 'approvals'] as const) {
    if (a[key] !== undefined && b[key] !== undefined) {
      (merged as Record<string, unknown>)[key] = { ...a[key], ...b[key] };
    }
  }
  return merged;
};

describe('E4a engine profiles: properties (EP-4, EP-5, EP-6)', () => {
  it('EP-4: an accepted narrowing is within its base', () => {
    fc.assert(
      fc.property(profileArb, layerArb, (p, c) => {
        const { profile } = narrowProfile(p, c);
        return profile === null || within(profile, p);
      }),
      { numRuns: 400, seed: 0 },
    );
  });

  it('EP-5: composing narrowings equals one narrowing; narrowing twice by one layer changes nothing', () => {
    fc.assert(
      fc.property(profileArb, layerArb, layerArb, (p, a, b) => {
        const once = narrowProfile(p, a).profile;
        if (once === null) return true;
        expect(narrowProfile(once, a).profile).to.deep.equal(once);
        const twice = narrowProfile(once, b).profile;
        if (twice !== null) expect(narrowProfile(p, merge(a, b)).profile).to.deep.equal(twice);
        return true;
      }),
      { numRuns: 400, seed: 0 },
    );
  });

  const wideners = (p: PinnedEngineProfile): Array<[string, EngineProfileNarrowing]> => {
    const out: Array<[string, EngineProfileNarrowing]> = [['engine', { engines: ['codex'] }]];
    if (p.mode !== 'build') out.push(['mode', { mode: 'build' }]);
    if (ISOLATION[p.isolationMin]! > 0) out.push(['isolationMin', { isolationMin: 'docker' }]);
    out.push(['egress', { egress: p.egress === 'allowlist:pypi' ? 'allowlist:npm' : 'allowlist:pypi' }]);
    if (p.tools.maxTools !== null) out.push(['tools.maxTools', { tools: { maxTools: p.tools.maxTools + 1 } }]);
    if (p.tools.effects.length < 4) out.push(['tools.effects', { tools: { effects: [...EFFECTS] } }]);
    for (const key of ['maxSteps', 'maxWallS', 'maxTokens'] as const) {
      const value = p.limits[key];
      if (value !== null) out.push([`limits.${key}`, { limits: { [key]: value + 1 } }]);
    }
    if (p.limits.maxUsd !== null) out.push(['limits.maxUsd', { limits: { maxUsd: p.limits.maxUsd + 0.25 } }]);
    out.push(
      p.subAgents.enabled
        ? ['subAgents.maxDepth', { subAgents: { ...p.subAgents, maxDepth: p.subAgents.maxDepth + 1 } }]
        : ['subAgents.enabled', { subAgents: { enabled: true, maxDepth: 1, maxConcurrent: 1 } }],
    );
    if (p.reasoningEffort !== null && p.reasoningEffort !== 'high') out.push(['reasoningEffort', { reasoningEffort: 'high' }]);
    for (const kind of EFFECTS) {
      if (p.approvals[kind] !== 'allow') out.push([`approvals.${kind}`, { approvals: { [kind]: 'allow' } }]);
    }
    if (p.skills !== null && p.skills.length < SKILLS.length) out.push(['skills', { skills: [...SKILLS] }]);
    if (p.accountKinds.length < ACCOUNT_KINDS.length) out.push(['accountKinds', { accountKinds: [...ACCOUNT_KINDS] }]);
    return out;
  };

  it('EP-6: a layer strictly wider in one dimension is refused naming it', () => {
    fc.assert(
      fc.property(profileArb, fc.nat(), (p, pick) => {
        const options = wideners(p);
        const [dimension, layer] = options[pick % options.length]!;
        const decision = narrowProfile(p, layer);
        return decision.profile === null && decision.refusal.dimension === dimension;
      }),
      { numRuns: 400, seed: 0 },
    );
  });
});
