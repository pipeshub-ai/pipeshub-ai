import type { EditError } from '../../../types';
import { schemaType, type FieldSpec } from './schema-fields';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const empty = (v: unknown) => v === undefined || v === null || v === '';

// The server checks the full schema; this catches what a person can fix before sending.
function check(field: FieldSpec, value: unknown): string | null {
  const { schema } = field;
  if (schema.enum?.length && !schema.enum.includes(value)) return 'Choose one of the listed values.';
  switch (schemaType(schema)) {
    case 'string': {
      if (typeof value !== 'string') return 'Enter text.';
      if (schema.minLength !== undefined && value.length < schema.minLength) return `Use at least ${schema.minLength} characters.`;
      if (schema.maxLength !== undefined && value.length > schema.maxLength) return `Use at most ${schema.maxLength} characters.`;
      if (schema.format === 'email' && !EMAIL.test(value)) return 'Enter a valid email address.';
      if (schema.pattern) {
        try {
          if (!new RegExp(schema.pattern).test(value)) return 'Use the expected format.';
        } catch {
          // A pattern this browser can't compile is left to the server.
        }
      }
      return null;
    }
    case 'integer':
      return Number.isInteger(value) ? null : 'Enter a whole number.';
    case 'number':
      return typeof value === 'number' && Number.isFinite(value) ? null : 'Enter a number.';
    case 'boolean':
      return typeof value === 'boolean' ? null : 'Choose yes or no.';
    case 'array':
      return Array.isArray(value) ? null : 'Enter a list.';
    default:
      return null;
  }
}

/** Problems a person can fix in the form, as the same `{ path, message }` the server sends. */
export function checkArgs(fields: FieldSpec[], values: Record<string, unknown>): EditError[] {
  const errors: EditError[] = [];
  for (const field of fields) {
    const value = values[field.name];
    const message = empty(value) ? (field.required ? 'This field is required.' : null) : check(field, value);
    if (message) errors.push({ path: `/${field.name}`, message });
  }
  return errors;
}
