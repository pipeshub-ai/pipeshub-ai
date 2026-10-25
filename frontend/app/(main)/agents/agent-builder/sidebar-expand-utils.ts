import { useCallback, useState } from 'react';

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

/**
 * Accordion state that opens every group while the palette is searching, so matches are not hidden
 * in collapsed rows. Toggles made during a search are kept apart and dropped when it is cleared,
 * which restores the groups the user had open before.
 */
export function useSearchAwareExpanded(searching: boolean, initial: Record<string, boolean> = {}) {
  const [normal, setNormal] = useState(initial);
  const [inSearch, setInSearch] = useState<Record<string, boolean>>({});
  const [wasSearching, setWasSearching] = useState(searching);
  if (wasSearching !== searching) {
    setWasSearching(searching);
    setInSearch({});
  }
  const isOpen = useCallback(
    (key: string, defaultWhenUnset: boolean) =>
      searching ? (inSearch[key] ?? true) : (normal[key] ?? defaultWhenUnset),
    [searching, inSearch, normal]
  );
  const toggle = useCallback(
    (key: string, defaultWhenUnset: boolean) => {
      if (searching) setInSearch((p) => toggleKeyedBoolean(p, key, true));
      else setNormal((p) => toggleKeyedBoolean(p, key, defaultWhenUnset));
    },
    [searching]
  );
  return { isOpen, toggle };
}
