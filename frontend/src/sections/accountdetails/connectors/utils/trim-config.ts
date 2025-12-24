/**
 * Utility functions to trim leading and trailing whitespace from configuration values
 */

/**
 * Fields that should NOT be trimmed (they may contain intentional whitespace)
 */
const SKIP_TRIM_FIELDS = new Set([
  'certificate',
  'privateKey',
  'private_key',
  'credentials',
  'oauth',
  'json',
  'jsonData',
  'client_secret',
  'clientSecret',
  'secret',
  'token',
  'accessToken',
  'refreshToken',
]);

/**
 * Recursively trims leading and trailing whitespace from string values in a configuration object.
 * Skips certain fields that may contain intentional whitespace (like certificates, keys, etc.)
 * 
 * Only trims string values. Preserves:
 * - Booleans (true/false)
 * - Numbers (integers and floats)
 * - Date objects
 * - Other non-string primitive types
 * 
 * @param obj - The object to trim
 * @param path - Current path in the object (for tracking nested fields)
 * @returns A new object with trimmed string values
 */
export function trimConfigValues(obj: any, path: string = ''): any {
  // Handle null and undefined
  if (obj === null || obj === undefined) {
    return obj;
  }

  // Handle strings - only type that gets trimmed
  if (typeof obj === 'string') {
    // Check if current field name should be skipped
    const fieldName = path.split('.').pop() || '';
    if (SKIP_TRIM_FIELDS.has(fieldName.toLowerCase())) {
      return obj;
    }
    return obj.trim();
  }

  // Handle arrays - recursively process each element
  if (Array.isArray(obj)) {
    return obj.map((item, index) => trimConfigValues(item, `${path}[${index}]`));
  }

  // Handle plain objects - recursively process each property
  // Note: typeof null === 'object' in JavaScript, but we already handled null above
  // Also exclude Date objects and other special object types
  if (
    typeof obj === 'object' &&
    obj.constructor === Object &&
    !(obj instanceof Date) &&
    !(obj instanceof File)
  ) {
    // Use reduce instead of for...of loop to satisfy ESLint
    return Object.entries(obj).reduce((trimmed: any, [key, value]) => {
      const newPath = path ? `${path}.${key}` : key;
      trimmed[key] = trimConfigValues(value, newPath);
      return trimmed;
    }, {});
  }

  // Preserve all other types as-is:
  // - Booleans (typeof boolean === 'boolean')
  // - Numbers (typeof number === 'number')
  // - Date objects (instanceof Date)
  // - File objects (instanceof File)
  // - Other special object types
  return obj;
}

/**
 * Trims whitespace from connector configuration before saving.
 * This ensures consistent data without leading/trailing spaces.
 * 
 * @param config - The configuration object to trim
 * @returns A new configuration object with trimmed values
 */
export function trimConnectorConfig(config: any): any {
  if (!config || typeof config !== 'object') {
    return config;
  }

  const trimmed: any = {};

  // Trim auth section
  if (config.auth && typeof config.auth === 'object') {
    trimmed.auth = trimConfigValues(config.auth, 'auth');
  }

  // Trim sync section
  if (config.sync && typeof config.sync === 'object') {
    trimmed.sync = trimConfigValues(config.sync, 'sync');
  }

  // Trim filters section (nested structure)
  if (config.filters && typeof config.filters === 'object') {
    trimmed.filters = trimConfigValues(config.filters, 'filters');
  }

  // Preserve other properties as-is
  Object.keys(config).forEach((key) => {
    if (!['auth', 'sync', 'filters'].includes(key)) {
      trimmed[key] = config[key];
    }
  });

  return trimmed;
}

