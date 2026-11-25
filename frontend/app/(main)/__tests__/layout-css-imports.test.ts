// @vitest-environment node
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// vitest ignores CSS imports, so a package stylesheet path that does not
// exist only fails `next build`: check each one resolves in node_modules.
const here = path.dirname(fileURLToPath(import.meta.url));
const layout = path.resolve(here, '../layout.tsx');
const require = createRequire(layout);

const packageStylesheets = (source: string): string[] =>
  [...source.matchAll(/^import\s+['"]([^'"./][^'"]*\.css)['"]/gm)].map((match) => match[1]);

describe('app/(main)/layout.tsx stylesheet imports', () => {
  const stylesheets = packageStylesheets(readFileSync(layout, 'utf8'));

  it('imports package stylesheets', () => {
    expect(stylesheets.length).toBeGreaterThan(0);
  });

  it.each(stylesheets)('%s resolves', (specifier) => {
    expect(() => require.resolve(specifier)).not.toThrow();
  });
});
