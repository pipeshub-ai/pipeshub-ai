import {
  REDACT_ONLY_PATTERN_SOURCES,
  SECRET_KEY_NAME_FLAGS,
  SECRET_KEY_NAME_SOURCE,
  SECRET_MASK,
  VENDOR_PATTERN_SOURCES,
} from '../contracts/vectors/token-patterns';

/**
 * The shared token vector (contracts/harness/vectors/token-patterns.json),
 * the same shapes the gateways refuse and the approval card masks; what Node
 * serves to someone other than the run's owner goes through it.
 */
const PATTERNS: readonly RegExp[] = [
  ...VENDOR_PATTERN_SOURCES,
  ...REDACT_ONLY_PATTERN_SOURCES,
].map(({ source }) => new RegExp(source, 'g'));

// A key named like a secret holds one whatever its value looks like (a pasted password, a short key).
const SECRET_KEY_NAME = new RegExp(
  SECRET_KEY_NAME_SOURCE,
  SECRET_KEY_NAME_FLAGS,
);

export { SECRET_MASK };

export function redactTokens(text: string): string {
  return PATTERNS.reduce(
    (masked, pattern) => masked.replace(pattern, SECRET_MASK),
    text,
  );
}

export function isSecretKeyName(key: string): boolean {
  return SECRET_KEY_NAME.test(key);
}

function redactValue(value: unknown): unknown {
  if (typeof value === 'string') return redactTokens(value);
  if (Array.isArray(value)) return value.map(redactValue);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        redactTokens(key),
        isSecretKeyName(key) ? SECRET_MASK : redactValue(item),
      ]),
    );
  }
  return value;
}

/** A call's journaled arguments as another person may see them: secret-named values masked whole, the rest by shape. */
export function redactedArgs(args: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(args);
  } catch {
    return redactTokens(args);
  }
  return JSON.stringify(redactValue(parsed));
}

/** Fields that hold a tool call's arguments, as a JSON string or an object. */
const ARGUMENT_FIELDS = new Set([
  'args',
  'argsJson',
  'argsPreview',
  'arguments',
]);

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
  if (value === null || typeof value !== 'object') return false;
  const proto = Object.getPrototypeOf(value) as unknown;
  return proto === Object.prototype || proto === null;
};

/**
 * What Node sends a participant who is not the run's actor (SEC4): tool
 * arguments as `redactedArgs` shapes them, every other string by shape. Only
 * arguments are masked by key name, so `inputTokens` keeps its number. Class
 * instances (an ObjectId, a Date) pass through as they are.
 */
export function shapedForViewer(value: unknown): unknown {
  if (typeof value === 'string') return redactTokens(value);
  if (Array.isArray(value)) return value.map(shapedForViewer);
  if (!isPlainObject(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      redactTokens(key),
      ARGUMENT_FIELDS.has(key) ? shapedArguments(item) : shapedForViewer(item),
    ]),
  );
}

function shapedArguments(value: unknown): unknown {
  if (typeof value === 'string') return redactedArgs(value);
  return isPlainObject(value) || Array.isArray(value)
    ? redactValue(value)
    : shapedForViewer(value);
}
