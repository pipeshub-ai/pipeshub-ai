import en from '@/lib/i18n/locales/en-US.json';

/** en-US lookup with interpolation and the one/other plural split, for component tests. */
export function enT(key: string, options: Record<string, unknown> = {}): string {
  const find = (path: string): unknown => {
    let cur: unknown = en;
    for (const part of path.split('.')) {
      if (typeof cur !== 'object' || cur === null || !(part in cur)) return undefined;
      cur = (cur as Record<string, unknown>)[part];
    }
    return cur;
  };
  let value = find(key);
  if (value === undefined && typeof options.count === 'number') {
    value = find(`${key}_${options.count === 1 ? 'one' : 'other'}`);
  }
  if (typeof value !== 'string') return key;
  return value.replace(/{{\s*(\w+)\s*}}/g, (_, name: string) => String(options[name] ?? ''));
}
