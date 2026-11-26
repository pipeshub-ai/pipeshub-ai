import type { PinShadowDimension } from '../domain/pin-shadow';

/** `pipeshub_cp_shadow_mismatch_total{dimension}` for what the pin is compared on (05-migration-plan §1 rule 6). */
export interface PinShadowMetrics {
  mismatch(dimension: PinShadowDimension): void;
}
