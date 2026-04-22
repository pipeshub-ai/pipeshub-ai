import type { PASSWORD_RESET_FAILURE_VARIANT } from './constants';

// ========================================
// Reset-password / invite-accept page types
// ========================================

export type PasswordResetFailureVariant =
  (typeof PASSWORD_RESET_FAILURE_VARIANT)[keyof typeof PASSWORD_RESET_FAILURE_VARIANT];

export interface PasswordResetFailureProps {
  variant?: PasswordResetFailureVariant;
}
