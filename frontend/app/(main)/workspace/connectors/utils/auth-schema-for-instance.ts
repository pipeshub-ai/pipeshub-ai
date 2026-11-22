import type { AuthSchemaDefinition, AuthSchemaField, ConnectorAuthConfig } from '../types';

/** Whether a saved auth value is there; a saved secret comes back as the mask, which counts. */
export function hasSavedAuthValue(value: unknown): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === 'string') return value.trim() !== '';
  return true;
}

function fieldsForInstance(
  fields: AuthSchemaField[],
  savedValues: Record<string, unknown> | null
): AuthSchemaField[] {
  const saved = (name: string) => savedValues !== null && hasSavedAuthValue(savedValues[name]);
  return fields
    .filter((field) => !field.savedOnly || saved(field.name))
    .map((field) =>
      field.requiredUnlessSaved
        ? { ...field, required: Boolean(field.required) && !saved(field.requiredUnlessSaved) }
        : field
    );
}

function definitionForInstance(
  definition: AuthSchemaDefinition | undefined,
  savedValues: Record<string, unknown> | null
): AuthSchemaDefinition | undefined {
  if (!definition?.fields) return definition;
  return { ...definition, fields: fieldsForInstance(definition.fields, savedValues) };
}

/**
 * The auth schema as it applies to one instance: `savedOnly` fields only where a value is saved,
 * `requiredUnlessSaved` resolved to `required`. Done once when the panel loads, so the form's
 * rendering and validation read a plain schema.
 *
 * `savedValues` is the instance's saved auth values, or null for a new instance.
 */
export function authSchemaForInstance(
  auth: ConnectorAuthConfig | undefined,
  savedValues: Record<string, unknown> | null
): ConnectorAuthConfig | undefined {
  if (!auth) return auth;
  const schemas = auth.schemas
    ? Object.fromEntries(
        Object.entries(auth.schemas).map(([type, definition]) => [
          type,
          definitionForInstance(definition, savedValues) as AuthSchemaDefinition,
        ])
      )
    : auth.schemas;
  return { ...auth, schema: definitionForInstance(auth.schema, savedValues), schemas };
}
