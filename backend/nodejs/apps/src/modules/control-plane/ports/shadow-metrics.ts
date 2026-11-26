import type { ShadowDimension } from '../domain/shadow-diff';

/** `pipeshub_cp_shadow_mismatch_total{dimension}`. */
export interface ShadowMismatchCounter {
  inc(dimension: ShadowDimension): void;
}
