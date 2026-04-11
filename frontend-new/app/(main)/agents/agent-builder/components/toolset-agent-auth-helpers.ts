import type { AuthSchemaField, ConnectorAuthConfig } from '@/app/(main)/workspace/connectors/types';

export function toolsetSchemaRoot(raw: unknown): Record<string, unknown> | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const t = r.toolset;
  if (t && typeof t === 'object') return t as Record<string, unknown>;
  return r;
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

export function authFieldsForType(
  authConfig: ConnectorAuthConfig | null,
  authType: string
): AuthSchemaField[] {
  if (!authConfig || !authType) return [];
  const schemas = authConfig.schemas;
  if (schemas && schemas[authType]?.fields) {
    return filterFieldsForAuthenticate(schemas[authType].fields || []);
  }
  if (authConfig.schema?.fields) {
    return filterFieldsForAuthenticate(authConfig.schema.fields);
  }
  return [];
}

export function apiErrorDetail(e: unknown): string {
  const ax = e as { response?: { data?: { detail?: string; message?: string } } };
  return (
    ax.response?.data?.detail ||
    ax.response?.data?.message ||
    (e instanceof Error ? e.message : 'Request failed')
  );
}
