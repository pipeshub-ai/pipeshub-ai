import type { FieldValidation, SyncCustomField } from '../types';

/**
 * Validates a single sync custom field (same rules as legacy
 * `use-connector-config` validateField for sync section).
 */
export function validateSyncCustomField(field: SyncCustomField, value: unknown): string {
  if (field.required) {
    if (field.fieldType === 'TAGS') {
      const arr = Array.isArray(value) ? value : [];
      const nonEmpty = arr.map((v) => String(v).trim()).filter((s) => s.length > 0);
      if (nonEmpty.length === 0) {
        return `${field.displayName} is required`;
      }
    } else if (!value || (typeof value === 'string' && !value.trim())) {
      return `${field.displayName} is required`;
    }
  }

  const validation: FieldValidation | undefined = field.validation;
  if (!validation) {
    return '';
  }

  const { minLength, maxLength, format } = validation;

  if (minLength != null && value != null && value !== '') {
    const len = typeof value === 'string' ? value.length : String(value).length;
    if (len < minLength) {
      return `${field.displayName} must be at least ${minLength} characters`;
    }
  }

  if (
    maxLength != null &&
    value != null &&
    value !== '' &&
    field.name !== 'serviceAccountJson'
  ) {
    const len = typeof value === 'string' ? value.length : String(value).length;
    if (len > maxLength) {
      return `${field.displayName} must be no more than ${maxLength} characters`;
    }
  }

  if (format && value) {
    const asString = typeof value === 'string' ? value : String(value);
    switch (format) {
      case 'email': {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(asString)) {
          return `${field.displayName} must be a valid email address`;
        }
        break;
      }
      case 'url': {
        try {
          void new URL(asString);
        } catch {
          return `${field.displayName} must be a valid URL`;
        }
        break;
      }
      default:
        break;
    }
  }

  return '';
}

/** Run validation for all sync custom fields; keys are field names. */
export function collectSyncCustomFieldErrors(
  fields: SyncCustomField[],
  values: Record<string, unknown>
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const message = validateSyncCustomField(field, values[field.name]);
    if (message) {
      errors[field.name] = message;
    }
  }
  return errors;
}
