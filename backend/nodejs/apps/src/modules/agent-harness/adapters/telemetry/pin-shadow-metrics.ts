import { countShadowMismatch } from '../../../control-plane';
import type { PinShadowMetrics } from '../../ports/pin-shadow';

export const prometheusPinShadowMetrics: PinShadowMetrics = {
  mismatch: countShadowMismatch,
};
