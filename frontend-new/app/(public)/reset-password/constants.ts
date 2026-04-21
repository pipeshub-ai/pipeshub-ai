import type { PasswordResetFailureVariant } from './types';

// ========================================
// Reset-password / invite-accept page constants
// ========================================

/**
 * Variants for the PasswordResetFailure screen.
 * - MISSING_TOKEN   : page opened without a token in the URL hash
 * - INVITE_EXPIRED  : token is expired and was issued as an invite (48h lifetime)
 * - RESET_EXPIRED   : token is expired and was issued as a password-reset (20m lifetime)
 */
export const PASSWORD_RESET_FAILURE_VARIANT = {
  MISSING_TOKEN: 'missing-token',
  INVITE_EXPIRED: 'invite-expired',
  RESET_EXPIRED: 'reset-expired',
} as const;

/**
 * i18n keys for each failure variant. Resolved at render time with `t()` so
 * copy respects the active locale; keep the English source of truth in the
 * locale files under `resetPassword.failure.*`.
 */
export const PASSWORD_RESET_FAILURE_MESSAGE_KEYS: Record<PasswordResetFailureVariant, string> = {
  [PASSWORD_RESET_FAILURE_VARIANT.MISSING_TOKEN]: 'resetPassword.failure.missingTokenMessage',
  [PASSWORD_RESET_FAILURE_VARIANT.INVITE_EXPIRED]: 'resetPassword.failure.inviteExpiredMessage',
  [PASSWORD_RESET_FAILURE_VARIANT.RESET_EXPIRED]: 'resetPassword.failure.resetExpiredMessage',
};

/**
 * Token-lifetime cutoff used to decide which expired-link message to show.
 * Backend issues invite tokens at 48h and password-reset tokens at 20m, so any
 * lifetime longer than this cutoff is treated as an invite.
 */
export const INVITE_VS_RESET_LIFETIME_CUTOFF_SECONDS = 60 * 60; // 1h
