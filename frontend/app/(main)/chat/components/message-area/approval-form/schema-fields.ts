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
      label: fieldLabel(name, prop.title),
      required: required.has(name),
      control,
      ...(control === 'select' ? { options: (unwrapNullable(prop).enum ?? []).map((value) => ({ value, label: String(value) })) } : {}),
      schema: unwrapNullable(prop),
    };
  });
  return [...fields.filter((f) => f.required), ...fields.filter((f) => !f.required)];
}

// Words written as initialisms in a label; a trailing name or key is the field's own plumbing.
const INITIALISM: Record<string, string> = { id: 'ID', ids: 'IDs', url: 'URL', urls: 'URLs', uuid: 'UUID', html: 'HTML', api: 'API' };
const PLUMBING_WORD = new Set(['name', 'key']);

/** A field's label: the schema's title, else its name in sentence case ("issueTypeName" is "Issue type"). */
export function fieldLabel(name: string, title?: string | null): string {
  if (title?.trim()) return title.trim();
  const words = name.split(/[_\-\s.]+|(?<=[a-z0-9])(?=[A-Z])/).filter(Boolean).map((w) => w.toLowerCase());
  if (words.length > 1 && PLUMBING_WORD.has(words[words.length - 1])) words.pop();
  if (words.length === 0) return name;
  const text = words.map((w) => INITIALISM[w] ?? w).join(' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const ID_WORD = new Set(['id', 'ids', 'uuid', 'guid']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** An id the person doesn't judge the call by (`cloudId`, a uuid): shown under Details, never dropped. */
export function isTechnical(field: FieldSpec, value: unknown): boolean {
  return ID_WORD.has(lastWord(field.name)) || field.schema.format === 'uuid' || (typeof value === 'string' && UUID.test(value.trim()));
}

// The type a value has, for an argument the schema doesn't describe.
function valueSchema(value: unknown): JsonSchema {
  if (typeof value === 'string') return { type: 'string' };
  if (typeof value === 'number') return { type: 'number' };
  if (typeof value === 'boolean') return { type: 'boolean' };
  if (Array.isArray(value) && value.every((v) => typeof v === 'string')) return { type: 'array', items: { type: 'string' } };
  return {};
}

/** Every argument of a call as a field, to show it: the schema's fields, then any argument it leaves out. */
export function argumentFields(schema: JsonSchema | undefined, values: Record<string, unknown>): FieldSpec[] {
  const described = schema ? schemaFields(schema, values) : [];
  const known = new Set(described.map((f) => f.name));
  const rest = Object.keys(values).filter((name) => !known.has(name));
  return [...described, ...schemaFields({ type: 'object', properties: Object.fromEntries(rest.map((name) => [name, valueSchema(values[name])])) }, values)];
}
