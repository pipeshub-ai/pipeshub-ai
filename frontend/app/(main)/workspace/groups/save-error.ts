import {
  ErrorType,
  getUserFacingErrorMessage,
  isProcessedError,
  type ProcessedError,
} from '@/lib/api/api-error';

/**
 * A 400 from create or rename carries a sentence for the admin, such as
 * "Group already exists"; the sidebars show it themselves, so the global toast
 * skips it. Every other failure keeps the global toast.
 */
export function isGroupSaveRefusal(error: ProcessedError): boolean {
  return error.type === ErrorType.VALIDATION_ERROR;
}

/** The API's own words for a refused save, or undefined to keep the generic toast. */
export function groupSaveRefusalMessage(error: unknown): string | undefined {
  if (!isProcessedError(error) || !isGroupSaveRefusal(error)) return undefined;
  return getUserFacingErrorMessage(error, '') || undefined;
}
