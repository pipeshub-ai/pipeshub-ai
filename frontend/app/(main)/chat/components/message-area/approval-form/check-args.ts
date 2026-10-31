import type { EditError } from '../../../types';
import { schemaType, type FieldSpec } from './schema-fields';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const empty = (v: unknown) => v === undefined || v === null || v === '';

type Problem = Omit<EditError, 'path'>;
const problem = (code: string, message: string, params?: Problem['params']): Problem => ({ code, message, ...(params ? { params } : {}) });

// The server checks the full schema; this catches what a person can fix before sending.
// Codes are what the card translates; `message` is the English fallback.
function check(field: FieldSpec, value: unknown): Problem | null {
  const { schema } = field;
  if (schema.enum?.length && !schema.enum.includes(value)) return problem('enum', 'Choose one of the listed values.');
  switch (schemaType(schema)) {
    case 'string': {
      if (typeof value !== 'string') return problem('type', 'Enter text.', { type: 'string' });
      if (schema.minLength !== undefined && value.length < schema.minLength)
        return problem('minLength', `Use at least ${schema.minLength} characters.`, { min: schema.minLength });
      if (schema.maxLength !== undefined && value.length > schema.maxLength)
        return problem('maxLength', `Use at most ${schema.maxLength} characters.`, { max: schema.maxLength });
      if (schema.format === 'email' && !EMAIL.test(value)) return problem('email', 'Enter a valid email address.');
      if (schema.pattern) {
        try {
          if (!new RegExp(schema.pattern).test(value)) return problem('pattern', 'Use the expected format.');
        } catch {
          // A pattern this browser can't compile is left to the server.
        }
      }
      return null;
    }
    case 'integer':
      return Number.isInteger(value) ? null : problem('type', 'Enter a whole number.', { type: 'integer' });
    case 'number':
      return typeof value === 'number' && Number.isFinite(value) ? null : problem('type', 'Enter a number.', { type: 'number' });
    case 'boolean':
      return typeof value === 'boolean' ? null : problem('type', 'Choose yes or no.', { type: 'boolean' });
    case 'array':
      return Array.isArray(value) ? null : problem('type', 'Enter a list.', { type: 'array' });
    default:
      return null;
  }
}

/** Problems a person can fix in the form, as the same `{ path, message }` the server sends, plus a `code`. */
export function checkArgs(fields: FieldSpec[], values: Record<string, unknown>): EditError[] {
  const errors: EditError[] = [];
  for (const field of fields) {
    const value = values[field.name];
    const found = empty(value) ? (field.required ? problem('required', 'This field is required.') : null) : check(field, value);
    if (found) errors.push({ path: `/${field.name}`, ...found });
  }
  return errors;
}
