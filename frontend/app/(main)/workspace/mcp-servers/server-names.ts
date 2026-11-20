/** How two server names compare: case and runs of spaces don't tell servers apart to a reader.
 * Mirrors the backend's `_name_key`, which refuses a second server with the same name. */
export function serverNameKey(name: string): string {
  return name.trim().split(/\s+/).join(' ').toLowerCase();
}

/** `base`, else `base 2`, `base 3`… : the first name none of `taken` already has. */
export function freeServerName(base: string, taken: readonly string[]): string {
  const keys = new Set(taken.map(serverNameKey));
  if (!keys.has(serverNameKey(base))) return base;
  let n = 2;
  while (keys.has(serverNameKey(`${base} ${n}`))) n += 1;
  return `${base} ${n}`;
}
