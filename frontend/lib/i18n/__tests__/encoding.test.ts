import { describe, expect, it } from 'vitest';
import { locales } from '../locales';
import { SUPPORTED_LNG_KEYS } from '../supported-languages';

// UTF-8 read as CP437 or Windows-1252 turns "…" into "ΓÇª" and "ó" into "├│".
const MOJIBAKE = /ΓÇ|â€|Ã|Â|ðŸ|├|┬/;

function garbledStrings(node: unknown, path = ''): string[] {
  if (typeof node === 'string') return MOJIBAKE.test(node) ? [`${path}: ${node}`] : [];
  if (!node || typeof node !== 'object') return [];
  return Object.entries(node).flatMap(([k, v]) => garbledStrings(v, path ? `${path}.${k}` : k));
}

describe('locale text encoding', () => {
  it('has no text garbled by a wrong-encoding round trip', () => {
    for (const key of SUPPORTED_LNG_KEYS) {
      expect(garbledStrings(locales[key as keyof typeof locales]), key).toEqual([]);
    }
  });
});
