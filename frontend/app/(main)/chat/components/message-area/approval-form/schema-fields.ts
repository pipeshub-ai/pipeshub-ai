import { toolDisplayName } from '../../../../workspace/mcp-servers/tool-rules';
import type { JsonSchema } from '../../../types';

export type FieldControl = 'text' | 'textarea' | 'email' | 'date' | 'datetime' | 'number' | 'switch' | 'select' | 'chips' | 'json';

export type FieldSpec = {
  name: string;
  label: string;
  required: boolean;
  control: FieldControl;
  options?: string[];
  schema: JsonSchema;
};

const LONG_TEXT_NAME = /body|description|content|message|comment|text/;

/** The schema's type, ignoring a `null` alternative. */
export function schemaType(schema: JsonSchema): string | undefined {
  return Array.isArray(schema.type) ? schema.type.find((t) => t !== 'null') : schema.type;
}

function controlFor(name: string, schema: JsonSchema): FieldControl {
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
    case undefined:
      return 'text';
    default:
      return 'json'; // objects and anything the form has no input for stay read-only
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
      ...(control === 'select' ? { options: (prop.enum ?? []).map(String) } : {}),
      schema: prop,
    };
  });
  return [...fields.filter((f) => f.required), ...fields.filter((f) => !f.required)];
}
