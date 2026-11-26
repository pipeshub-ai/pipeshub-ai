import type { ErrorCode } from '../contracts/generated';
import type { PolicyRefusalCode } from './policy-alias';

/** The `Problem` code each policy refusal is answered with (02-lld §10). */
export const REFUSAL_CODES: Readonly<Record<PolicyRefusalCode, ErrorCode>> = {
  widening: 'CP_WIDENING',
  not_exposed: 'CP_NOT_EXPOSED',
  outside_ceiling: 'CP_OUTSIDE_CEILING',
  locked: 'CP_LOCKED',
  invalid: 'CP_INVALID_CONFIG',
};
