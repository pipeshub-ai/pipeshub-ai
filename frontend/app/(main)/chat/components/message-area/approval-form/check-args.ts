import type { EditError } from '../../../types';
import { schemaType, type FieldSpec } from './schema-fields';

export const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

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
      // No `pattern` check here: a server's regex can run for ages, and a browser regex can't be
      // stopped. The server checks it under a time limit.
      return null;
    }
    case 'integer':
      return Number.isInteger(value) ? null : problem('type', 'Enter a whole number.', { type: 'integer' });
    case 'number':
      return typeof value === 'number' && Number.isFinite(value) ? null : problem('type', 'Enter a number.', { type: 'number' });
    case 'boolean':
      return typeof value === 'boolean' ? null : problem('type', 'Choose yes or no.', { type: 'boolean' });
    case 'array':
      if (!Array.isArray(value)) return problem('type', 'Enter a list.', { type: 'array' });
      if (schema.items?.format === 'email' && value.some((item) => typeof item !== 'string' || !EMAIL.test(item)))
        return problem('email', 'Enter a valid email address.');
      return null;
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
