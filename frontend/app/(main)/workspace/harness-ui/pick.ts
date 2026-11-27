/** Field pickers for mapping a response by name, so nothing the server adds reaches a component. */

export type Json = Record<string, unknown>;

export const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

export const text = (value: unknown): string | null =>
  typeof value === 'string' && value !== '' ? value : null;

export const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

export const oneOf = <T extends string>(value: unknown, allowed: readonly T[]): T | null =>
  typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : null;

export const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

/** Maps every entry of a list field, dropping entries of the wrong shape. */
export const listOf = <T>(value: unknown, map: (entry: unknown) => T | null): T[] =>
  (Array.isArray(value) ? value : []).map(map).filter((entry): entry is T => entry !== null);

/** An id-to-name map field, keeping only non-empty string names. */
export const nameMap = (value: unknown): Record<string, string> =>
  isObject(value)
    ? Object.fromEntries(
        Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1] !== ''),
      )
    : {};

/** ADR-UI-03: the names of the agents a list answer holds (`names.agents`); none from a server that sends none. */
export const heldAgentNames = (data: unknown): Record<string, string> =>
  nameMap(isObject(data) && isObject(data.names) ? data.names.agents : null);

/** Every call of these screens renders its own failure: no global toast. */
export const QUIET = { suppressErrorToast: true } as const;
