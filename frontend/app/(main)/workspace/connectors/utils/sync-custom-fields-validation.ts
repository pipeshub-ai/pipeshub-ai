import { CONNECTOR_SERVICE_ACCOUNT_JSON_FIELD_NAME } from '../constants';
import type { FieldValidation, SyncCustomField } from '../types';
import { getUrlValidationError, type UrlValidationError } from './url-field';

export type SyncCustomFieldValidationError =
  | 'fieldRequired'
  | 'minLength'
  | 'maxLength'
  | 'email'
  | 'numberRange'
  | 'numberMin'
  | 'numberMax'
  | UrlValidationError;

/** Booleans arrive as "true"/"false" strings from schema defaults, so values compare as strings. */
const sameValue = (a: unknown, b: unknown) => String(a) === String(b);

/**
 * Whether a field's `showWhen` holds for the current form values. A controlling
 * field with no value yet counts as its schema default.
 */
export function isSyncCustomFieldShown(
  field: SyncCustomField,
  values: Record<string, unknown>,
  fields: SyncCustomField[] = []
): boolean {
  if (!field.showWhen) return true;
  return Object.entries(field.showWhen).every(([name, allowed]) => {
    const current = values[name] ?? fields.find((f) => f.name === name)?.defaultValue;
    return Array.isArray(allowed) && allowed.some((v) => sameValue(v, current));
  });
}

function validateNumber(validation: FieldValidation | undefined, value: unknown): SyncCustomFieldValidationError | null {
  const { min, max } = validation ?? {};
  if (min == null && max == null) return null;
  if (value == null || value === '') return null;
  const num = Number(value);
  const outOfRange = !Number.isInteger(num) || (min != null && num < min) || (max != null && num > max);
  if (!outOfRange) return null;
  if (min != null && max != null) return 'numberRange';
  return min != null ? 'numberMin' : 'numberMax';
}

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
    } else if ((!value && value !== 0) || (typeof value === 'string' && !value.trim())) {
      return 'fieldRequired';
    }
  }

  const validation: FieldValidation | undefined = field.validation;
  // NUMBER fields carry their range in min/max; minLength/maxLength would compare text length.
  if (field.fieldType === 'NUMBER') return validateNumber(validation, value);
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

/** Run validation for the shown sync custom fields; hidden ones keep their values unchecked. Keys are field names. */
export function collectSyncCustomFieldErrors(
  fields: SyncCustomField[],
  values: Record<string, unknown>,
  messageFor: (field: SyncCustomField, error: SyncCustomFieldValidationError) => string
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    if (!isSyncCustomFieldShown(field, values, fields)) continue;
    const error = validateSyncCustomField(field, values[field.name]);
    if (error) {
      errors[field.name] = messageFor(field, error);
    }
  }
  return errors;
}

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** The message for a sync field error, with numbers formatted for the reader's locale. */
export function syncCustomFieldErrorMessage(
  t: Translate,
  field: SyncCustomField,
  error: SyncCustomFieldValidationError
): string {
  return t(`workspace.actions.validation.${error}`, {
    field: field.displayName,
    minLength: field.validation?.minLength,
    maxLength: field.validation?.maxLength,
    min: field.validation?.min,
    max: field.validation?.max,
  });
}
