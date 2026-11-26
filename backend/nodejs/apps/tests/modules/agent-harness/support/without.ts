/** A shallow copy of `value` without `keys`: what a test's older or narrower shape of a fixture looks like. */
export function without<T extends object, K extends keyof T>(value: T, ...keys: K[]): Omit<T, K> {
  const dropped = new Set<PropertyKey>(keys);
  return Object.fromEntries(Object.entries(value).filter(([key]) => !dropped.has(key))) as Omit<T, K>;
}
