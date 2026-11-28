import * as fs from 'fs';
import * as yaml from 'js-yaml';

type Spec = Record<string, unknown>;

const isObject = (value: unknown): value is Spec =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** A tag list of named entries, as the spec's top-level `tags`; an operation's `tags` are plain names. */
const isTagList = (value: unknown): value is Array<Spec & { name: string }> =>
  Array.isArray(value) &&
  value.every((tag) => isObject(tag) && typeof tag.name === 'string');

/**
 * Deep-merges a spec fragment: objects merge key by key, a list of named tags merges by name, any other value
 * replaces, and `null` removes the key.
 */
export function mergeSpecFragment(base: Spec, fragment: Spec): Spec {
  const merged: Spec = { ...base };
  for (const [key, value] of Object.entries(fragment)) {
    const current = merged[key];
    if (value === null) {
      Reflect.deleteProperty(merged, key);
    } else if (key === 'tags' && isTagList(value) && isTagList(current)) {
      // A fragment updates or adds its own tags; the tags it doesn't name (the edition's, ones added later) stay.
      const byName = new Map(current.map((tag) => [tag.name, tag]));
      for (const tag of value) byName.set(tag.name, tag);
      merged[key] = [...byName.values()];
    } else if (isObject(value) && isObject(merged[key])) {
      merged[key] = mergeSpecFragment(merged[key], value);
    } else {
      merged[key] = value;
    }
  }
  return merged;
}

/** Each entry lists candidate locations of one fragment; the first that exists is read. */
const fragments: string[][] = [];

/** Adds a YAML fragment merged over the published spec. */
export function useSpecFragment(candidatePaths: string[]): void {
  if (!fragments.some((f) => f.join() === candidatePaths.join())) {
    fragments.push(candidatePaths);
  }
}

/** The spec with every registered fragment merged over it, in registration order. */
export function withSpecFragments(spec: Spec): Spec {
  return fragments.reduce((merged, candidates) => {
    const file = candidates.find((p) => fs.existsSync(p));
    return file
      ? mergeSpecFragment(
          merged,
          yaml.load(fs.readFileSync(file, 'utf8')) as Spec,
        )
      : merged;
  }, spec);
}
