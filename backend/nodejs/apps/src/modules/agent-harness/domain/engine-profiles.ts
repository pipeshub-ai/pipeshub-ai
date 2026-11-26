/**
 * Engine profiles (E4a): release defaults narrowed by the org ceiling, the
 * agent and the request, each layer only within the one above it
 * (ARCHITECTURE 3.1 principle 5). A widening is refused naming its dimension
 * and layer, never clipped (principle 6). The dimensions are the
 * `engineProfile.*` rows of `policy-dimensions.json`, folded by the one policy
 * resolver (`./policy`), as Python's `harness_core.engines.profiles` does;
 * `contracts/harness/vectors/engine-profiles.json` holds both to one answer.
 */
import { canonicalDigest } from '../contracts/canonical-json';
import {
  EngineProfileNarrowing,
  engineProfileNarrowingSchema,
} from '../contracts/generated/core/engine-profile-narrowing';
import {
  PinnedEngineProfile,
  pinnedEngineProfileSchema,
} from '../contracts/generated/core/engine-profile';
import { REASONING_EFFORTS } from '../contracts/generated/core/enums';
import type { ErrorCodeOf } from '../contracts/generated/ext/pipeshub/errors';
import type { RunSpec } from '../contracts/generated/core/run-spec';
import {
  POLICY_DIMENSIONS,
  RELEASE_DEFAULTS,
  RELEASE_ENGINES,
} from '../contracts/generated/core/policy-data';
import { releaseIsolationFloor } from './engine-isolation';
import { DimensionRegistry, PolicyResolver } from './policy';

export type { EngineProfileNarrowing, PinnedEngineProfile };
export type ProfileLayerName = 'org' | 'agent' | 'request';
export type EngineExecutor = Extract<
  NonNullable<RunSpec['executor']>,
  { kind: 'engine' }
>;

const EFFORTS: ReadonlySet<string> = new Set(REASONING_EFFORTS);

/** `kind@version`: how the sandbox broker names an engine it cannot run (H1). */
export const engineRef = (engine: { kind: string; version: string }): string =>
  `${engine.kind}@${engine.version}`;

/**
 * This release's engines with their default profile, in allowlist order (the
 * first is the default engine): `harness_engines/allowlist.json`'s engines and
 * `policy/release.json`'s profile, both generated, at this install's isolation
 * floor (gVisor; the dev/test override's below it, `./engine-isolation`).
 */
export function releaseEngineProfiles(
  env: Readonly<Record<string, string | undefined>> = process.env,
): readonly PinnedEngineProfile[] {
  const isolationMin = releaseIsolationFloor(env);
  return Object.freeze(
    RELEASE_ENGINES.map((engine) =>
      Object.freeze({
        engine: { ...engine },
        ...structuredClone(RELEASE_DEFAULTS.engineProfile),
        isolationMin,
      }),
    ),
  );
}

export const RELEASE_ENGINE_PROFILES: readonly PinnedEngineProfile[] =
  releaseEngineProfiles();

/**
 * `/services/agentHarness/engineProfiles`: each org's ceiling, written by an
 * org admin. A ceiling is kept as stored and parsed where it is used, so one
 * org's bad entry never makes another org's unreadable.
 */
export interface EngineProfilesDocument {
  orgs: Record<
    string,
    { ceiling: unknown; updatedAt: number; updatedBy: string }
  >;
}

/** The catalogue's `engine_profile` group (ERR-UX2). */
export const ENGINE_PROFILE_REFUSAL_CODES = [
  'ENGINE_PROFILE_WIDENING',
  'ENGINE_PROFILE_INVALID',
] as const satisfies readonly ErrorCodeOf<'engine_profile'>[];
export type EngineProfileRefusalCode =
  (typeof ENGINE_PROFILE_REFUSAL_CODES)[number];

export interface EngineProfileRefusal {
  code: EngineProfileRefusalCode;
  /** The first dimension that widens (`limits.maxSteps`, `approvals.write`, ...); absent when invalid. */
  dimension?: string;
  layer?: ProfileLayerName;
  detail: string;
}

export type ProfileDecision =
  | { profile: PinnedEngineProfile; refusal: null }
  | { profile: null; refusal: EngineProfileRefusal };

export const engineAdmits = (
  selectors: readonly string[],
  engine: Pick<PinnedEngineProfile['engine'], 'kind' | 'version'>,
): boolean =>
  selectors.includes(engine.kind) ||
  selectors.includes(`${engine.kind}@${engine.version}`);

const ACP_KIND = /^acp-[a-z][a-z0-9_-]{0,31}$/;

/** An ACP agent profile's engine (`acp-<agent>`, ACP-driver), as Python's `is_acp_kind` says. */
export const isAcpKind = (kind: string): boolean => ACP_KIND.test(kind);

/**
 * Who opts an ACP engine in, as Python's `AcpOptIn` says: `release`, every released one (no org sets a ceiling, so
 * none could name it); `org`, only one the org's ceiling names (a long-tail agent is the org admin's choice).
 */
export type AcpOptIn = 'org' | 'release';

/** The ACP opt-in of a ceiling source; none, or one that says nothing, is `release`. */
export const acpOptInOf = (
  ceilings: { readonly acpOptIn?: AcpOptIn } | null | undefined,
): AcpOptIn => ceilings?.acpOptIn ?? 'release';

/**
 * The releases an org may run: every native engine, and an ACP engine as `acpOptIn` says: any released one, or only
 * one the org's ceiling names by kind or `kind@version`, as Python's `opted_in` says.
 */
export function optedIn(
  releases: readonly PinnedEngineProfile[],
  orgEngines: readonly string[] | undefined,
  acpOptIn: AcpOptIn = 'release',
): PinnedEngineProfile[] {
  if (acpOptIn === 'release') return [...releases];
  return releases.filter(
    (r) =>
      !isAcpKind(r.engine.kind) || engineAdmits(orgEngines ?? [], r.engine),
  );
}

/** The `engineProfile.*` rows, relative to the profile: refusal order is row order. */
export const ENGINE_PROFILE_DIMENSIONS = DimensionRegistry.fromTable(
  POLICY_DIMENSIONS,
  'engineProfile',
);
const resolver = new PolicyResolver(ENGINE_PROFILE_DIMENSIONS);

/** Every dimension of `layer` at or below `profile`'s, applied; else the first widening, refused. */
export function narrowProfile(
  profile: PinnedEngineProfile,
  layer: EngineProfileNarrowing,
): ProfileDecision {
  const folded = resolver.foldPrefix([
    { kind: 'release', values: profile },
    { kind: 'request', values: layer },
  ]);
  if (folded.refusal !== null) {
    return {
      profile: null,
      refusal: {
        code:
          folded.refusal.code === 'invalid'
            ? 'ENGINE_PROFILE_INVALID'
            : 'ENGINE_PROFILE_WIDENING',
        dimension: folded.refusal.dimension,
        detail: folded.refusal.detail,
      },
    };
  }
  return {
    profile: folded.prefix.document() as PinnedEngineProfile,
    refusal: null,
  };
}

/** A layer as stored or sent: the schema's narrowing, or `ENGINE_PROFILE_INVALID` (never treated as absent). */
export function parseNarrowing(
  raw: unknown,
):
  | { narrowing: EngineProfileNarrowing; refusal: null }
  | { narrowing: null; refusal: EngineProfileRefusal } {
  const parsed = engineProfileNarrowingSchema.safeParse(raw);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return {
      narrowing: null,
      refusal: {
        code: 'ENGINE_PROFILE_INVALID',
        detail: issue
          ? `${issue.path.length > 0 ? issue.path.join('.') : 'layer'} ${issue.message}`
          : 'not an engine profile narrowing',
      },
    };
  }
  return { narrowing: parsed.data, refusal: null };
}

export interface ProfileLayers {
  org?: unknown;
  /**
   * The agent's engine limits on its policy layer (ADR-SET-01), above its
   * definition's narrowing; a refusal of either names the `agent` layer.
   */
  agentPolicy?: unknown;
  agent?: unknown;
  request?: unknown;
}

const PROFILE_LAYERS = [
  ['org', 'org'],
  ['agentPolicy', 'agent'],
  ['agent', 'agent'],
  ['request', 'request'],
] as const satisfies ReadonlyArray<
  readonly [keyof ProfileLayers, ProfileLayerName]
>;

/**
 * The first release profile every layer's `engines` admits, narrowed by the
 * org ceiling, the agent (its policy layer, then its definition) and the
 * request in that order, among the releases `acpOptIn` opts in (`optedIn`).
 * Layers are raw (as stored or sent) and parsed here, so an invalid one is
 * refused, not skipped.
 */
export function resolveEngineProfile(
  layers: ProfileLayers,
  releases: readonly PinnedEngineProfile[] = RELEASE_ENGINE_PROFILES,
  acpOptIn: AcpOptIn = 'release',
): ProfileDecision {
  const parsed: Array<[ProfileLayerName, EngineProfileNarrowing]> = [];
  for (const [key, name] of PROFILE_LAYERS) {
    if (layers[key] === undefined || layers[key] === null) continue;
    const layer = parseNarrowing(layers[key]);
    if (layer.refusal !== null) {
      return { profile: null, refusal: { ...layer.refusal, layer: name } };
    }
    parsed.push([name, layer.narrowing]);
  }
  const org = parsed.find(([name]) => name === 'org')?.[1];
  let candidates = optedIn(releases, org?.engines, acpOptIn);
  const refusedEngine = (layer?: ProfileLayerName): ProfileDecision => ({
    profile: null,
    refusal: {
      code: 'ENGINE_PROFILE_WIDENING',
      dimension: 'engine',
      ...(layer !== undefined ? { layer } : {}),
      detail: 'no released engine is admitted by every layer',
    },
  });
  if (releases.length === 0) return refusedEngine();
  if (candidates.length === 0) return refusedEngine('org');
  for (const [name, layer] of parsed) {
    if (layer.engines === undefined) continue;
    const engines = layer.engines;
    candidates = candidates.filter((c) => engineAdmits(engines, c.engine));
    if (candidates.length === 0) return refusedEngine(name);
  }
  let profile = candidates[0] as PinnedEngineProfile;
  for (const [name, layer] of parsed) {
    const decision = narrowProfile(profile, layer);
    if (decision.refusal !== null) {
      return { profile: null, refusal: { ...decision.refusal, layer: name } };
    }
    profile = decision.profile;
  }
  return { profile: pinnedEngineProfileSchema.parse(profile), refusal: null };
}

/**
 * What a run's own request narrows (the `request` layer): the reasoning effort
 * it asks for. `minimal` is below every engine effort, so it narrows to `low`,
 * the least an engine runs at; absent, the request narrows nothing.
 */
export function requestNarrowing(
  reasoningEffort: string | undefined,
): EngineProfileNarrowing | undefined {
  if (reasoningEffort === undefined) return undefined;
  const effort = reasoningEffort === 'minimal' ? 'low' : reasoningEffort;
  return EFFORTS.has(effort)
    ? {
        reasoningEffort: effort as NonNullable<
          EngineProfileNarrowing['reasoningEffort']
        >,
      }
    : undefined;
}

/** `executor.engine.profileDigest`: the canonical digest of the pinned profile. */
export const profileDigest = (
  profile: PinnedEngineProfile,
): `sha256:${string}` => canonicalDigest(profile);

/** The executor a run with this resolved profile pins: the engine build plus the profile and its digest. */
export const engineExecutor = (
  profile: PinnedEngineProfile,
): EngineExecutor => ({
  kind: 'engine',
  engine: {
    kind: profile.engine.kind,
    version: profile.engine.version,
    binaryDigest: profile.engine.binaryDigest,
    profileDigest: profileDigest(profile),
    profile: structuredClone(profile),
  },
});
