import { metricsBackend } from '../../../../libs/services/telemetry/metrics-backend';
import type { ShadowMismatchCounter } from '../../ports/shadow-metrics';

// The one registration of this name: the pin shadow (CPD-5) and the
// effective-set shadow (CPD-7) both count here, and a second would refuse to start.
const mismatches = metricsBackend.createCounter({
  name: 'pipeshub_cp_shadow_mismatch_total',
  help: "Admissions on which the control plane and today's decision disagree, by dimension (05 §1 rule 6)",
  labelNames: ['dimension'],
});

export function countShadowMismatch(dimension: string): void {
  mismatches.inc({ dimension });
}

export const prometheusShadowMismatches: ShadowMismatchCounter = {
  inc: countShadowMismatch,
};
