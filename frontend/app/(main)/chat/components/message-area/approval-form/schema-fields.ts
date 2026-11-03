import { toolDisplayName } from '../../../../workspace/mcp-servers/tool-rules';
import type { JsonSchema } from '../../../types';

export type FieldControl = 'text' | 'textarea' | 'email' | 'date' | 'datetime' | 'number' | 'switch' | 'select' | 'chips' | 'json';

export type FieldSpec = {
  name: string;
  label: string;
  required: boolean;
  control: FieldControl;
  /** Original enum values, so a numeric enum is sent back as a number. */
  options?: { value: unknown; label: string }[];
  schema: JsonSchema;
};

const LONG_TEXT_WORD = new Set(['body', 'description', 'content', 'message', 'text', 'comment', 'markdown', 'html']);
const LONG_TEXT_FORMAT = new Set(['markdown', 'html', 'textarea']);

/** The name's last word, so `email_body` and `issueDescription` match but `message_id` and `contentType` don't. */
export const lastWord = (name: string) => (name.split(/[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])/).pop() ?? '').toLowerCase();

function longText(name: string, schema: JsonSchema): boolean {
  return LONG_TEXT_FORMAT.has(schema.format ?? '') || (schema.maxLength ?? 0) > 200 || LONG_TEXT_WORD.has(lastWord(name));
}

/** A string field that reads as a body of text: by its name, a `maxLength` over 200, or a markdown/html/textarea format. */
export const isLongText = (field: FieldSpec): boolean => field.control === 'textarea';

/** `anyOf`/`oneOf` of one schema plus null is that schema, nullable; any other union stays as is. */
export function unwrapNullable(schema: JsonSchema): JsonSchema {
  const union = schema.anyOf ?? schema.oneOf;
  const rest = union?.filter((s) => s.type !== 'null');
  return union && rest?.length === 1 && union.length === 2 ? { ...rest[0], title: schema.title ?? rest[0].title } : schema;
}

/** The schema's type, ignoring a `null` alternative; undefined for a union, `$ref` or untyped schema. */
export function schemaType(schema: JsonSchema): string | undefined {
  const s = unwrapNullable(schema);
  return Array.isArray(s.type) ? s.type.find((t) => t !== 'null') : s.type;
}

// No type keyword and nothing else that fixes one: the schema takes any value.
const untyped = (s: JsonSchema) => s.type === undefined && !s.enum && s.const === undefined && !s.$ref && !s.anyOf && !s.oneOf && !s.properties && !s.items;

/** Whether a schema without one type still takes text: untyped, or a union with a plain text branch. */
const takesText = (s: JsonSchema) =>
  untyped(s) || (s.anyOf ?? s.oneOf ?? []).some((b) => schemaType(b) === 'string' && !b.enum?.length && b.const === undefined);

function controlFor(name: string, raw: JsonSchema, value: unknown): FieldControl {
  const schema = unwrapNullable(raw);
  if (schema.enum?.length) return 'select';
  switch (schemaType(schema)) {
    case 'string':
      if (schema.format === 'email') return 'email';
      if (schema.format === 'date') return 'date';
      if (schema.format === 'date-time') return 'datetime';
      if (longText(name, schema)) return 'textarea';
      return 'text';
    case 'integer':
    case 'number':
      return 'number';
    case 'boolean':
      return 'switch';
    case 'array': {
      const items = schema.items ?? {};
      const textItems = untyped(items) && Array.isArray(value) && value.every((v) => typeof v === 'string');
      return schemaType(items) === 'string' || textItems ? 'chips' : 'json';
    }
    default:
      // The server checks the edit against the full schema, so text the call wrote stays text.
      if (typeof value === 'string' && takesText(schema)) return longText(name, schema) ? 'textarea' : 'text';
      return 'json'; // objects, other unions, `$ref` and untyped values that aren't text have no input, so stay read-only
  }
}

/** One field per top-level property: required ones first, then in the schema's order. `values`, the
 * call's own arguments, decide the control where the schema leaves the type open. */
export function schemaFields(schema: JsonSchema, values: Record<string, unknown> = {}): FieldSpec[] {
  const required = new Set(schema.required ?? []);
  const fields = Object.entries(schema.properties ?? {}).map(([name, prop]): FieldSpec => {
    const control = controlFor(name, prop, values[name]);
    return {
      name,
      label: prop.title?.trim() || toolDisplayName(name),
      required: required.has(name),
      control,
      ...(control === 'select' ? { options: (unwrapNullable(prop).enum ?? []).map((value) => ({ value, label: String(value) })) } : {}),
      schema: unwrapNullable(prop),
    };
  });
  return [...fields.filter((f) => f.required), ...fields.filter((f) => !f.required)];
}
