/**
 * Palette accordions store `Record<string, boolean>`. Keys that are not written until the user
 * toggles are often read as `record[key] ?? defaultWhenUnset`. Flipping with `!record[key]`
 * breaks the first click because `!undefined === true`, which matches an already-expanded UI.
 */

export function toggleKeyedBoolean(
  prev: Record<string, boolean>,
  key: string,
  defaultWhenUnset: boolean
): Record<string, boolean> {
  const stored = prev[key];
  const effective = stored === undefined ? defaultWhenUnset : stored;
  return { ...prev, [key]: !effective };
}
