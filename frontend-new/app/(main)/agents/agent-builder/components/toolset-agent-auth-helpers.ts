import type {
  AuthSchemaField,
  ConnectorAuthConfig,
  DocumentationLink,
} from '@/app/(main)/workspace/connectors/types';
import type { ToolsetOauthConfigListRow } from '@/app/(main)/toolsets/api';
import { normalizeDocumentationLinks } from '@/app/(main)/workspace/connectors/normalize-documentation-links';

export { normalizeDocumentationLinks };

export function toolsetSchemaRoot(raw: unknown): Record<string, unknown> | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const t = r.toolset;
  if (t && typeof t === 'object') return t as Record<string, unknown>;
  return r;
}

/** Reads `toolset.documentationLinks` or `toolset.config.documentationLinks` from a schema API payload. */
export function documentationLinksFromToolsetSchema(raw: unknown): DocumentationLink[] {
  const root = toolsetSchemaRoot(raw);
  if (!root) return [];
  const fromTop = normalizeDocumentationLinks(root.documentationLinks);
  if (fromTop.length) return fromTop;
  const cfg = root.config as Record<string, unknown> | undefined;
  return normalizeDocumentationLinks(cfg?.documentationLinks);
}

export function primaryHttpDocumentationUrl(
  links: readonly Pick<DocumentationLink, 'url'>[] | undefined
): string {
  const url = links?.[0]?.url;
  return typeof url === 'string' && url.startsWith('https://') ? url : '';
}

export function getToolsetAuthConfigFromSchema(raw: unknown): ConnectorAuthConfig | null {
  const root = toolsetSchemaRoot(raw);
  if (!root) return null;
  const config = root.config as Record<string, unknown> | undefined;
  const auth = (config?.auth ?? root.auth) as ConnectorAuthConfig | undefined;
  return auth ?? null;
}

export function filterFieldsForAuthenticate(fields: unknown[]): AuthSchemaField[] {
  if (!Array.isArray(fields)) return [];
  return fields.filter((field) => {
    const f = field as { usage?: string };
    const usage = String(f?.usage || 'BOTH').toUpperCase();
    if (usage === 'BOTH') return true;
    return usage !== 'CONFIGURE';
  }) as AuthSchemaField[];
}

/** Admin instance creation — fields intended for org-level configuration only. */
export function filterFieldsForConfigure(fields: unknown[]): AuthSchemaField[] {
  if (!Array.isArray(fields)) return [];
  return fields.filter((field) => {
    const f = field as { usage?: string };
    const usage = String(f?.usage || 'BOTH').toUpperCase();
    if (usage === 'BOTH') return true;
    return usage !== 'AUTHENTICATE';
  }) as AuthSchemaField[];
}

function pickSchemaEntryForAuthType(
  authConfig: ConnectorAuthConfig,
  authType: string
): { fields?: unknown[] } | undefined {
  const schemas = authConfig.schemas;
  if (!schemas) return undefined;
  const upper = authType.toUpperCase();
  const entry =
    (schemas as Record<string, { fields?: unknown[] } | undefined>)[authType] ??
    (schemas as Record<string, { fields?: unknown[] } | undefined>)[upper];
  if (entry?.fields) return entry;
  if (upper === 'OAUTH') {
    const o = (schemas as Record<string, { fields?: unknown[] } | undefined>).OAUTH
      ?? (schemas as Record<string, { fields?: unknown[] } | undefined>).oauth;
    if (o?.fields) return o;
  }
  return undefined;
}

export function authFieldsForType(
  authConfig: ConnectorAuthConfig | null,
  authType: string
): AuthSchemaField[] {
  if (!authConfig || !authType) return [];
  const sub = pickSchemaEntryForAuthType(authConfig, authType);
  if (sub?.fields) {
    return filterFieldsForAuthenticate(sub.fields || []);
  }
  if (authConfig.schema?.fields) {
    return filterFieldsForAuthenticate(authConfig.schema.fields);
  }
  return [];
}

export function configureAuthFieldsForType(
  authConfig: ConnectorAuthConfig | null,
  authType: string
): AuthSchemaField[] {
  if (!authConfig || !authType) return [];
  const sub = pickSchemaEntryForAuthType(authConfig, authType);
  if (sub?.fields) {
    return filterFieldsForConfigure(sub.fields || []);
  }
  if (authConfig.schema?.fields) {
    return filterFieldsForConfigure(authConfig.schema.fields);
  }
  return [];
}

/**
 * Hydrate org OAuth app form values from GET /oauth-configs row + schema field names.
 * Merges top-level row keys and `extraConfig` (e.g. instance_url normalized into extraConfig).
 */
export function oauthConfigureSeedValuesFromListRow(
  row: ToolsetOauthConfigListRow | undefined,
  fields: AuthSchemaField[]
): Record<string, unknown> {
  if (!row || !fields.length) return {};
  const names = new Set(fields.map((f) => f.name));
  const merged: Record<string, unknown> = {};
  const raw = row as unknown as Record<string, unknown>;
  for (const [k, v] of Object.entries(raw)) {
    if (k === 'extraConfig' || !names.has(k)) continue;
    if (v === null || v === undefined) continue;
    if (typeof v === 'string' && !v.trim()) continue;
    merged[k] = Array.isArray(v) ? v.join(',') : v;
  }
  if (row.extraConfig) {
    for (const [k, v] of Object.entries(row.extraConfig)) {
      if (!names.has(k)) continue;
      if (v === null || v === undefined || String(v).trim() === '') continue;
      merged[k] = v;
    }
  }
  return merged;
}

/** Normalize field names for org OAuth app id/secret detection (ignore separators). */
function oauthAppCredentialKeyNormalized(fieldName: string): string {
  return fieldName.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Org-level OAuth app credentials — never collect or display these in end-user (authenticate) flows. */
export function isOrgOAuthAppCredentialFieldName(fieldName: string): boolean {
  const n = oauthAppCredentialKeyNormalized(fieldName);
  return (
    n === 'clientid' ||
    n === 'clientsecret' ||
    n === 'oauthclientid' ||
    n === 'oauthclientsecret'
  );
}

export function apiErrorDetail(e: unknown): string {
  const ax = e as { response?: { data?: { detail?: string; message?: string } } };
  return (
    ax.response?.data?.detail ||
    ax.response?.data?.message ||
    (e instanceof Error ? e.message : 'Request failed')
  );
}

/** Deterministic serialization for comparing auth field maps (e.g. OAuth dirty state). */
export function stableStringifyRecord(values: Record<string, unknown>): string {
  const keys = Object.keys(values).sort();
  return JSON.stringify(keys.map((k) => [k, values[k]]));
}
