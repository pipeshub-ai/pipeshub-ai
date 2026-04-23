import type { AuthSchemaField, ConnectorAuthConfig } from '../../types';
import { resolveAuthFields } from './helpers';

/**
 * Auth fields shown in the Authenticate tab, aligned with
 * `AuthenticateTab` (excludes `oauthConfigId` for OAUTH) and `conditionalDisplay`.
 */
export function visibleAuthSchemaFields(
  authConfig: ConnectorAuthConfig | undefined,
  selectedAuthType: string,
  conditionalDisplay: Record<string, boolean>
): AuthSchemaField[] {
  const fields = resolveAuthFields(authConfig, selectedAuthType);
  const withoutOauthId =
    selectedAuthType === 'OAUTH' ? fields.filter((f) => f.name !== 'oauthConfigId') : fields;
  return withoutOauthId.filter((f) => {
    if (conditionalDisplay[f.name] === false) return false;
    return true;
  });
}

function valueMissingForField(
  value: unknown,
  field: AuthSchemaField & { fieldType?: string }
): boolean {
  const ft = (field as { fieldType?: string }).fieldType;
  if (ft === 'BOOLEAN' || ft === 'CHECKBOX') {
    if (!field.required) return false;
    return value === undefined || value === null;
  }
  if (value === undefined || value === null) return true;
  if (typeof value === 'string' && value.trim() === '') return true;
  if (Array.isArray(value) && value.length === 0) return true;
  return false;
}

/**
 * Per-field error messages for empty required values (i18n keys in caller, or short labels).
 */
export function collectRequiredAuthFieldErrors(
  fields: AuthSchemaField[],
  formDataAuth: Record<string, unknown>,
  messageFor: (field: AuthSchemaField) => string
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of fields) {
    if (!field.required) continue;
    const v = formDataAuth[field.name];
    if (!valueMissingForField(v, field as AuthSchemaField & { fieldType?: string })) continue;
    out[field.name] = messageFor(field);
  }
  return out;
}
