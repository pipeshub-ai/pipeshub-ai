export type SyncFailureCode =
  | 'AUTH'
  | 'RATE_LIMIT'
  | 'PERMISSION'
  | 'NETWORK'
  | 'CANCELLED'
  | 'UNKNOWN'
  | string
  | null
  | undefined;

export interface SyncFailureCopy {
  code: SyncFailureCode;
  /** i18n key under workspace.connectors.syncProgress.failure.* */
  key: string;
  title: string;
  /** One-line explanation (not the raw exception). */
  summary: string;
  remediation: string;
}

const KEY_PREFIX = 'workspace.connectors.syncProgress.failure';

/**
 * Map a coarse backend failure code to org-facing title + summary + remediation.
 * English strings are fallbacks for `t(`${copy.key}.title`, { defaultValue })`.
 */
export function describeSyncFailure(code?: SyncFailureCode | null): SyncFailureCopy {
  switch ((code ?? 'UNKNOWN').toUpperCase()) {
    case 'AUTH':
      return {
        code: 'AUTH',
        key: `${KEY_PREFIX}.auth`,
        title: 'Authentication failed',
        summary: 'This connector could not sign in to the source.',
        remediation:
          'Reconnect in Manage → Authorize. If it still fails, an admin may need to approve the app or its scopes.',
      };
    case 'RATE_LIMIT':
      return {
        code: 'RATE_LIMIT',
        key: `${KEY_PREFIX}.rateLimit`,
        title: 'Rate limit reached',
        summary: 'The source temporarily limited how fast we can sync.',
        remediation: 'Wait a few minutes, then retry. If this keeps happening, reduce sync frequency.',
      };
    case 'PERMISSION':
      return {
        code: 'PERMISSION',
        key: `${KEY_PREFIX}.permission`,
        title: 'Missing access',
        summary: 'This account cannot read some of the content selected for sync.',
        remediation:
          'Grant access to the files or Shared Drives this connector should sync, then retry.',
      };
    case 'NETWORK':
      return {
        code: 'NETWORK',
        key: `${KEY_PREFIX}.network`,
        title: 'Connection problem',
        summary: 'We could not reach the source reliably.',
        remediation: 'Check network connectivity to the source, then retry the sync.',
      };
    case 'CANCELLED':
      return {
        code: 'CANCELLED',
        key: `${KEY_PREFIX}.cancelled`,
        title: 'Sync cancelled',
        summary: 'The sync stopped before it finished.',
        remediation: 'Start the sync again when you are ready.',
      };
    default:
      return {
        code: 'UNKNOWN',
        key: `${KEY_PREFIX}.unknown`,
        title: 'Sync failed',
        summary: 'Something went wrong while syncing.',
        remediation:
          'Retry the sync. If it fails again, check connector settings or contact an admin.',
      };
  }
}

/** Known OAuth/API codes that may still appear in older Redis failureReason values. */
const FRIENDLY_BY_ERROR_CODE: Record<string, string> = {
  unauthorized_client:
    'Google blocked this app from getting access tokens for the requested scopes.',
  invalid_grant: 'The saved Google sign-in expired or was revoked.',
  invalid_client: 'The Google app credentials look invalid or were revoked.',
  access_denied: 'Access was denied while authorizing this connector.',
};

function looksTechnical(reason: string): boolean {
  const t = reason.trim();
  if (!t) return true;
  if (t.startsWith('(') || t.startsWith('{') || t.startsWith('[')) return true;
  if (t.includes("{'error'") || t.includes('{"error"')) return true;
  if (t.includes('Traceback') || t.includes('Error:')) return true;
  return false;
}

/**
 * Prefer a friendly explanation over a raw exception dump.
 * Returns null when the code-level summary is enough (avoids repeating title).
 */
export function resolveFailureSummary(
  code: SyncFailureCode | null | undefined,
  failureReason?: string | null
): { key?: string; text: string; params?: Record<string, string> } {
  const base = describeSyncFailure(code);
  const raw = failureReason?.trim() ?? '';
  if (!raw) {
    return { key: `${base.key}.summary`, text: base.summary };
  }

  const lower = raw.toLowerCase();
  for (const [errorCode, friendly] of Object.entries(FRIENDLY_BY_ERROR_CODE)) {
    if (lower.includes(errorCode)) {
      return {
        key: `${KEY_PREFIX}.reasons.${errorCode}`,
        text: friendly,
      };
    }
  }

  if (looksTechnical(raw)) {
    return { key: `${base.key}.summary`, text: base.summary };
  }

  // Already a short human sentence from the backend — show it once.
  return { text: raw };
}
