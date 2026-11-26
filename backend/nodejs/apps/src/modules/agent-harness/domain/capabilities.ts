import { canonicalDigest } from '../contracts/canonical-json';
import { HarnessCapabilities } from '../contracts/generated/core/capabilities';
import { HarnessUnavailableError } from './errors';

/** The closed capability vocabulary (REPORT 11); pinned on a run with its digest. */
export type DeploymentCapabilities = HarnessCapabilities['caps'];

/** Re-probed on this period; a run keeps the set it was pinned with. */
export const CAPABILITY_REFRESH_MS = 60_000;

/** A capability. */
export type CapabilityKey = keyof DeploymentCapabilities;

/**
 * After a round in which some probe could not answer, the next round comes this soon, doubling up to
 * CAPABILITY_REFRESH_MS (ADR-CAP-01): a broker slow to start is asked again within seconds, not a minute.
 */
export const CAPABILITY_RETRY_MIN_MS = 2_000;

/** How long admission and a claim wait for one more probe round when a capability they read is unknown. */
export const CAPABILITY_SETTLE_WAIT_MS = 3_000;

/**
 * How long after its admission a run's first claim is deferred while the sandbox it may get is still unknown. Past
 * it the broker has not answered for a minute, which is an outage, not a stack starting: the claim pins what is
 * known and says so (ADR-CAP-01 D3).
 */
export const CAPABILITY_SETTLE_WINDOW_MS = 60_000;

/**
 * What a capability is worth when nothing proved it. Missing capabilities
 * remove tools; they never silently weaken isolation [D-3].
 */
export const LEAST_CAPABILITIES: Readonly<DeploymentCapabilities> =
  Object.freeze({
    'journal.durable': false,
    livebus: 'inproc',
    'sandbox.isolation': 'none',
    'sandbox.egress': 'none',
    'mcp.stdio': 'off',
    'blob.cas.gc': false,
    'triggers.always_on': false,
    'workers.dispatched': false,
  });

/** A code sandbox some probe proved (P5.11); `none` (the least value) runs no code. */
export const provesCodeSandbox = (
  capabilities: Pick<DeploymentCapabilities, 'sandbox.isolation'>,
): boolean => capabilities['sandbox.isolation'] !== 'none';

export interface CapabilitySnapshot {
  capabilities: DeploymentCapabilities;
  digest: `sha256:${string}`;
  probedAt: number;
  /**
   * The capabilities whose probe could not answer this round (a timeout, a refused connection): they hold their
   * least value, but nothing proved it. Absent: every probe answered (ADR-CAP-01 D1).
   */
  unsettled?: readonly CapabilityKey[];
}

/** Whether `key` was proved either way: a snapshot exists and its probe answered. */
export const capabilityKnown = (
  snapshot: CapabilitySnapshot | null,
  key: CapabilityKey,
): boolean => snapshot !== null && !(snapshot.unsettled ?? []).includes(key);

/** Every capability proved either way: what readiness reports as settled. */
export const capabilitiesSettled = (
  snapshot: CapabilitySnapshot | null,
): boolean => snapshot !== null && (snapshot.unsettled ?? []).length === 0;

/** What a snapshot says of a code sandbox: one is proved, none is, or the probe could not tell (ADR-CAP-01). */
export type CodeSandboxProof = 'proved' | 'none' | 'unknown';

export function codeSandboxProof(
  snapshot: CapabilitySnapshot | null,
): CodeSandboxProof {
  if (snapshot !== null && provesCodeSandbox(snapshot.capabilities)) {
    return 'proved';
  }
  return capabilityKnown(snapshot, 'sandbox.isolation') ? 'none' : 'unknown';
}

export const capabilitiesDigest = (
  capabilities: DeploymentCapabilities,
): `sha256:${string}` => canonicalDigest(capabilities);

export function capabilitySnapshot(
  capabilities: DeploymentCapabilities,
  probedAt: number,
  unsettled: readonly CapabilityKey[] = [],
): CapabilitySnapshot {
  const copy = { ...capabilities };
  return {
    capabilities: copy,
    digest: capabilitiesDigest(copy),
    probedAt,
    ...(unsettled.length > 0 ? { unsettled: [...new Set(unsettled)] } : {}),
  };
}

/**
 * The set a claim may pin. Durable runs fail closed on a journal that cannot
 * majority-acknowledge (standalone, PSA) or before the first probe [O-3].
 */
export function pinnableCapabilities(
  snapshot: CapabilitySnapshot | null,
): CapabilitySnapshot {
  if (snapshot === null) {
    throw new HarnessUnavailableError(
      'Agent harness capabilities have not been probed yet',
    );
  }
  if (!snapshot.capabilities['journal.durable']) {
    throw new HarnessUnavailableError(
      'Durable agent runs need a journal that majority-acknowledges commits',
    );
  }
  return snapshot;
}
