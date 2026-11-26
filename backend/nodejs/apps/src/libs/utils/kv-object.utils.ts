/**
 * Parses a JSON object stored in the key-value store.
 *
 * Node writes these keys as one JSON layer (a JSON object). Keys written by an
 * older Python writer hold a JSON *string* of that JSON (two layers), so a
 * single `JSON.parse` yields a string and the next property access fails. This
 * reads both forms: if the first parse gives a string, it is parsed once more.
 *
 * Write the result back with a single `JSON.stringify`, which also repairs the
 * stored value in place.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function parseKvObject(raw: string): any {
  const parsed: unknown = JSON.parse(raw);
  return typeof parsed === 'string' ? JSON.parse(parsed) : parsed;
}
