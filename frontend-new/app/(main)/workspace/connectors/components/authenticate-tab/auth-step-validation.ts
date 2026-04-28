import type { AuthSchemaField, ConnectorAuthConfig } from '../../types';
import { getUrlValidationError } from '../../utils/url-field';
import {
  shouldRenderOAuthAuthSchemaField,
  type OAuthAuthFieldVisibilityContext,
} from '../../utils/auth-helpers';
import { resolveAuthFields } from './helpers';

/**
 * Auth fields shown in the Authenticate tab, aligned with
 * `AuthenticateTab` (excludes `oauthConfigId` for OAUTH), `conditionalDisplay`,
 * and {@link shouldRenderOAuthAuthSchemaField} when `oauthVisibility` is passed for OAUTH.
 */
export function visibleAuthSchemaFields(
  authConfig: ConnectorAuthConfig | undefined,
  selectedAuthType: string,
  conditionalDisplay: Record<string, boolean>,
  oauthVisibility?: OAuthAuthFieldVisibilityContext | null
): AuthSchemaField[] {
  const fields = resolveAuthFields(authConfig, selectedAuthType);
  const withoutOauthId =
    selectedAuthType === 'OAUTH' ? fields.filter((f) => f.name !== 'oauthConfigId') : fields;
  return withoutOauthId.filter((f) => {
    if (conditionalDisplay[f.name] === false) return false;
    if (
      selectedAuthType === 'OAUTH' &&
      oauthVisibility &&
      !shouldRenderOAuthAuthSchemaField(f.name, oauthVisibility)
    ) {
      return false;
    }
    return true;
  });
}

/** Minimal shape for `valueMissingForField`; `fieldType` is optional on some wire schemas (e.g. filters). */
type FieldTypeHint = { fieldType?: string };

function valueMissingForField(value: unknown, field: FieldTypeHint): boolean {
  const ft = field.fieldType;
  // Auth uses CHECKBOX for booleans; sync/filter schemas use BOOLEAN — neither is "empty" like ''.
  if (ft === 'CHECKBOX' || ft === 'BOOLEAN') {
    return false;
  }
  if (value === undefined || value === null) return true;
  if (typeof value === 'string' && value.trim() === '') return true;
  if (Array.isArray(value) && value.length === 0) return true;
  return false;
}

/**
 * Per-field error messages for empty required values (i18n keys in caller, or short labels).
 * Required CHECKBOX auth fields must be strictly `true` (not merely "present").
 */
export function collectRequiredAuthFieldErrors(
  fields: AuthSchemaField[],
  formDataAuth: Record<string, unknown>,
  messageFor: (field: AuthSchemaField) => string,
  mustBeTrueMessage?: (field: AuthSchemaField) => string
): Record<string, string> {
  const out: Record<string, string> = {};
  const mustBeTrue =
    mustBeTrueMessage ?? ((f: AuthSchemaField) => `${f.displayName} must be true`);

  for (const field of fields) {
    if (field.fieldType === 'CHECKBOX') {
      if (field.required && formDataAuth[field.name] !== true) {
        out[field.name] = mustBeTrue(field);
      }
      continue;
    }

    const v = formDataAuth[field.name];

    if (field.required && valueMissingForField(v, field)) {
      out[field.name] = messageFor(field);
      continue;
    }

    if (field.fieldType === 'URL') {
      const asString = typeof v === 'string' ? v : v != null ? String(v) : '';
      if (asString.trim() !== '') {
        const urlErr = getUrlValidationError(field.displayName, asString);
        if (urlErr) out[field.name] = urlErr;
      }
    }
  }
  return out;
}
