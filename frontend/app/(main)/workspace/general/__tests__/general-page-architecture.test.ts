import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// The General page takes what an edition adds through config.ts, so a build can leave a
// feature out by leaving its directory out. That holds only while the page's own files do
// not name the feature: these checks keep them from growing such a reference again.
const GENERAL = resolve(__dirname, '..');
const FEATURE = 'data-collection';
const read = (...parts: string[]) => readFileSync(join(GENERAL, ...parts), 'utf8');

const ownFiles = [
  ...readdirSync(GENERAL, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.tsx?$/.test(entry.name))
    .map((entry) => entry.name),
  ...readdirSync(join(GENERAL, '__tests__'))
    .filter((name) => name !== 'general-page-architecture.test.ts')
    .map((name) => join('__tests__', name)),
];

describe('General page: what an edition adds stays out of its own files', () => {
  it('covers the page, its store, its API and its cached reads', () => {
    expect(ownFiles).toEqual(expect.arrayContaining(['page.tsx', 'store.ts', 'api.ts', 'use-org.ts', 'general-page-settings.ts']));
  });

  it.each(ownFiles.filter((file) => file !== 'general-page-extras.tsx'))('%s does not name the Data Collection feature', (file) => {
    const source = read(file);
    expect(source).not.toMatch(/data[\s_-]?collection/i);
    expect(source).not.toMatch(/metrics?[\s_-]?collection/i);
    expect(source).not.toMatch(/telemetry/i);
    expect(source).not.toMatch(/\bmetrics\b/i);
  });

  it.each(ownFiles)('%s imports nothing from the feature directory', (file) => {
    expect(read(file)).not.toMatch(new RegExp(`from\\s+['"][^'"]*${FEATURE}`));
    expect(read(file)).not.toMatch(new RegExp(`import\\(\\s*['"][^'"]*${FEATURE}`));
  });

  it('the page reaches the edition only through config.ts', () => {
    const page = read('page.tsx');
    expect(page).toMatch(/import \{[^}]*\buseGeneralPageSettings\b[^}]*\} from '@\/config';/);
  });
});
