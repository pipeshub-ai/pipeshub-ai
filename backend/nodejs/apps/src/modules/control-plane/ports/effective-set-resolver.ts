import type { Problem } from '../contracts/generated';
import type { EffectiveSet } from '../domain/objects';
import type { EffectiveRunConfig } from '../domain/policy-alias';
import type { PinSubject, Snapshot } from '../domain/resolution';

/**
 * The items a run may use (03-interfaces §4.3, 02-lld §5.2): grant ∩ visible ∩
 * enabled ∩ approved ∩ ceilings, with the first failing reason for every
 * granted item that is not usable. Pure: reads only the snapshot.
 */
export interface EffectiveSetResolver {
  resolve(
    subject: PinSubject,
    config: EffectiveRunConfig,
    snapshot: Snapshot,
  ): EffectiveSet | Problem;
}
