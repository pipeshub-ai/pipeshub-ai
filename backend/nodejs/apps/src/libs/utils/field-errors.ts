import { BaseError } from '../errors/base.error';
import { ValidationError } from '../errors/validation.error';

/** One problem a client can attach to the form field it names. */
export interface FieldError {
  /** Request body property, e.g. `name`; nested ones are dot-separated. */
  field: string;
  message: string;
  code?: string;
}

const MAX_FIELD_ERRORS = 20;

function isFieldError(value: unknown): value is FieldError {
  if (typeof value !== 'object' || value === null) return false;
  const { field, message } = value as Record<string, unknown>;
  return (
    typeof field === 'string' &&
    field.length > 0 &&
    typeof message === 'string' &&
    message.trim().length > 0
  );
}

/**
 * The field-level errors a response may carry, or undefined when there are
 * none. Request-body issues drop the `body.` prefix so the name is the one the
 * form sent. Only a client error carries them: the text is the same wording
 * the response message already shows the reader.
 */
export function publicFieldErrors(error: BaseError): FieldError[] | undefined {
  if (error.statusCode < 400 || error.statusCode >= 500) return undefined;

  const raw: unknown[] =
    error instanceof ValidationError
      ? error.errors
      : Array.isArray(error.metadata?.fieldErrors)
        ? error.metadata.fieldErrors
        : [];

  const seen = new Set<string>();
  const out: FieldError[] = [];
  for (const item of raw) {
    if (!isFieldError(item)) continue;
    const field = item.field.replace(/^body\./, '');
    if (seen.has(field)) continue; // the first message per field is enough
    seen.add(field);
    out.push({
      field,
      message: item.message.trim(),
      ...(typeof item.code === 'string' && { code: item.code }),
    });
    if (out.length >= MAX_FIELD_ERRORS) break;
  }
  return out.length > 0 ? out : undefined;
}
