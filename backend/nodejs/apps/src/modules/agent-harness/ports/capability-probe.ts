import {
  CapabilityKey,
  CapabilitySnapshot,
  DeploymentCapabilities,
} from '../domain/capabilities';

/**
 * One backend's view of what the deployment can do. It reports only the
 * capabilities it can prove; anything it leaves out keeps its least value.
 * A probe that throws could not ask: what it `provides` is unknown for the
 * round, not disproved (ADR-CAP-01 D1).
 */
export interface CapabilityProbe {
  readonly name: string;
  readonly provides: readonly CapabilityKey[];
  probe(): Promise<Partial<DeploymentCapabilities>>;
}

/** The latest probed set; null until the first probe finished. */
export interface CapabilitySource {
  current(): CapabilitySnapshot | null;
  /** One more probe round (shared by concurrent callers). Absent: a fixed set that never changes. */
  refresh?(): Promise<CapabilitySnapshot>;
}
