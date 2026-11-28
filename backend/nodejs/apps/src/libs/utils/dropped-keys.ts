/**
 * Keys removed whenever request data is copied into a new object (the request sanitizers, the
 * response and log copies). `express.json` makes a `__proto__` property an own key; copying it
 * with `copy[key] = value` would set the copy's prototype, and every later reader of the copy
 * would see properties the request never sent as keys. These keys carry no data a route uses.
 */
export const DROPPED_KEYS: ReadonlySet<string> = new Set([
  '__proto__',
  'constructor',
  'prototype',
]);

/**
 * Sets `value` as an own data property of `target`, also for the keys in DROPPED_KEYS, which are
 * defined rather than assigned so `__proto__` cannot change `target`'s prototype. For copies that
 * should keep every key as data (log lines); request data drops those keys instead.
 */
export function setOwnKey(
  target: Record<string, unknown>,
  key: string,
  value: unknown,
): void {
  if (DROPPED_KEYS.has(key)) {
    Object.defineProperty(target, key, {
      value,
      enumerable: true,
      writable: true,
      configurable: true,
    });
  } else {
    target[key] = value;
  }
}
