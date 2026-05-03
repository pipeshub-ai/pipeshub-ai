/**
 * Read credential-style values from an OAuth registration document returned by
 * `GET /api/v1/oauth/:connectorType/:configId` (or list row `config`), keyed by
 * schema auth field names (camelCase) with snake_case fallbacks.
 */

export function nestedConfigFromOAuthRegistrationDoc(
  full: Record<string, unknown>
): Record<string, unknown> {
  const nested = full.config;
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    return nested as Record<string, unknown>;
  }
  return full;
}

function toSnakeCase(camel: string): string {
  return camel.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

/** Flat `config.auth` on the connector instance (camelCase or snake_case keys). */
export function readAuthValueFromFlatRecord(
  obj: Record<string, unknown> | undefined,
  fieldName: string
): string | undefined {
  if (!obj) return undefined;
  const direct = obj[fieldName];
  if (typeof direct === 'string' && direct.trim()) return direct.trim();
  const snake = toSnakeCase(fieldName);
  const s = obj[snake];
  if (typeof s === 'string' && s.trim()) return s.trim();
  return undefined;
}

export function readRegistrationValueForAuthField(
  nested: Record<string, unknown>,
  fieldName: string
): string | undefined {
  const direct = nested[fieldName];
  if (typeof direct === 'string' && direct.trim()) return direct.trim();

  const snake = toSnakeCase(fieldName);
  const s = nested[snake];
  if (typeof s === 'string' && s.trim()) return s.trim();

  return undefined;
}
