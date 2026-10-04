import { CONNECTOR_SERVICE_ACCOUNT_JSON_FIELD_NAME } from '../constants';
import type { FieldValidation, SyncCustomField } from '../types';
import { getUrlValidationError, type UrlValidationError } from './url-field';

export type SyncCustomFieldValidationError =
  | 'fieldRequired'
  | 'minLength'
  | 'maxLength'
  | 'email'
  | UrlValidationError;

/**
 * Validates a single sync custom field (same rules as legacy
 * `use-connector-config` validateField for sync section).
 */
export function validateSyncCustomField(field: SyncCustomField, value: unknown): SyncCustomFieldValidationError | null {
  if (field.required) {
    if (field.fieldType === 'TAGS') {
      const arr = Array.isArray(value) ? value : [];
      const nonEmpty = arr.map((v) => String(v).trim()).filter((s) => s.length > 0);
      if (nonEmpty.length === 0) {
        return 'fieldRequired';
      }
    } else if (!value || (typeof value === 'string' && !value.trim())) {
      return 'fieldRequired';
    }
  }

  const validation: FieldValidation | undefined = field.validation;
  const { minLength, maxLength, format } = validation ?? {};

  if (minLength != null && value != null && value !== '') {
    const len = typeof value === 'string' ? value.length : String(value).length;
    if (len < minLength) {
      return 'minLength';
    }
  }

  // maxLength is string-oriented; TAGS values are arrays — String(array) is comma-joined, not a useful limit.
  if (
    maxLength != null &&
    field.fieldType !== 'TAGS' &&
    value != null &&
    value !== '' &&
    field.name !== CONNECTOR_SERVICE_ACCOUNT_JSON_FIELD_NAME
  ) {
    const len = typeof value === 'string' ? value.length : String(value).length;
    if (len > maxLength) {
      return 'maxLength';
    }
  }

  if (format === 'email' && value) {
    const asString = typeof value === 'string' ? value : String(value);
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(asString)) {
      return 'email';
    }
  }

  const needsUrlValidation = field.fieldType === 'URL' || format === 'url';
  if (needsUrlValidation && value != null && value !== '') {
    const asString = typeof value === 'string' ? value : String(value);
    if (asString.trim()) {
      const urlErr = getUrlValidationError(asString);
      if (urlErr) return urlErr;
    }
  }

  return null;
}

/** Run validation for all sync custom fields; keys are field names. */
export function collectSyncCustomFieldErrors(
  fields: SyncCustomField[],
  values: Record<string, unknown>,
  messageFor: (field: SyncCustomField, error: SyncCustomFieldValidationError) => string
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const error = validateSyncCustomField(field, values[field.name]);
    if (error) {
      errors[field.name] = messageFor(field, error);
    }
  }
  return errors;
}
