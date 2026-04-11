/**
 * Recursively trim string values in a connector config object.
 * Prevents leading/trailing whitespace in API tokens, URLs, credentials, etc.
 */
export function trimConnectorConfig<T>(config: T): T {
  if (typeof config === 'string') return config.trim() as unknown as T;
  if (Array.isArray(config)) return config.map(trimConnectorConfig) as unknown as T;
  if (config !== null && typeof config === 'object') {
    return Object.fromEntries(
      Object.entries(config as Record<string, unknown>).map(([k, v]) => [
        k,
        trimConnectorConfig(v),
      ])
    ) as T;
  }
  return config;
}
