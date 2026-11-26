import type { ErrorCode } from '../contracts/generated';
import type { UnavailableReason } from './objects';

/** The refusal a `required` item answers with when it is unavailable for this reason. */
export const REASON_CODES: Readonly<Record<UnavailableReason, ErrorCode>> = {
  not_installed: 'CP_NOT_INSTALLED',
  disabled: 'CP_DISABLED',
  not_approved: 'CP_NOT_APPROVED',
  policy_ceiling: 'CP_POLICY_CEILING',
  not_visible: 'CP_NOT_VISIBLE',
  not_granted: 'CP_NOT_GRANTED',
  credential_missing: 'CP_CREDENTIAL_MISSING',
  credential_expired: 'CP_CREDENTIAL_EXPIRED',
  credential_revoked: 'CP_CREDENTIAL_REVOKED',
};

/** The default cap on tools in one effective set (NFR-11). */
export const DEFAULT_EFFECTIVE_SET_CAP = 2000;
