/** How two server names compare: case, runs of spaces and look-alike Unicode forms don't tell
 * servers apart to a reader. Mirrors the backend's `_name_key` (NFKC, then Python's `casefold`),
 * which refuses a second server with the same name. JavaScript has no case folding, so the two
 * letters whose folding differs from lower case are mapped as `casefold` does. */
export function serverNameKey(name: string): string {
  return name
    .normalize('NFKC')
    .trim()
    .split(/\s+/)
    .join(' ')
    .toLowerCase()
    .replace(/ß/g, 'ss')
    .replace(/ς/g, 'σ');
}

/** `base`, else `base 2`, `base 3`… : the first name none of `taken` already has. */
export function freeServerName(base: string, taken: readonly string[]): string {
  const keys = new Set(taken.map(serverNameKey));
  if (!keys.has(serverNameKey(base))) return base;
  let n = 2;
  while (keys.has(serverNameKey(`${base} ${n}`))) n += 1;
  return `${base} ${n}`;
}
