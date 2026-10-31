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

const LONG_TEXT_NAME = /body|description|content|message|comment|text/i;

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

function controlFor(name: string, raw: JsonSchema): FieldControl {
  const schema = unwrapNullable(raw);
  if (schema.enum?.length) return 'select';
  switch (schemaType(schema)) {
    case 'string':
      if (schema.format === 'email') return 'email';
      if (schema.format === 'date') return 'date';
      if (schema.format === 'date-time') return 'datetime';
      if (schema.maxLength !== undefined ? schema.maxLength > 200 : LONG_TEXT_NAME.test(name)) return 'textarea';
      return 'text';
    case 'integer':
    case 'number':
      return 'number';
    case 'boolean':
      return 'switch';
    case 'array':
      return schemaType(schema.items ?? {}) === 'string' ? 'chips' : 'json';
    default:
      return 'json'; // objects, unions, `$ref` and untyped schemas have no input, so stay read-only
  }
}

/** One field per top-level property: required ones first, then in the schema's order. */
export function schemaFields(schema: JsonSchema): FieldSpec[] {
  const required = new Set(schema.required ?? []);
  const fields = Object.entries(schema.properties ?? {}).map(([name, prop]): FieldSpec => {
    const control = controlFor(name, prop);
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
