import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { describe, expect, it } from 'vitest';
import { GALLERY_ARTIFACT_TYPES } from '../types';

const LOCALES = join(__dirname, '..', '..', '..', '..', 'lib', 'i18n', 'locales');

describe('artifact type labels', () => {
  // The backend files anything it cannot classify as OTHER; without a label the
  // gallery printed the raw code.
  it.each(readdirSync(LOCALES).filter((f) => f.endsWith('.json')))('%s labels every gallery type', (file) => {
    const types = JSON.parse(readFileSync(join(LOCALES, file), 'utf8')).artifacts.types;
    for (const type of [...GALLERY_ARTIFACT_TYPES, 'OTHER']) {
      expect(types[type], `${file} artifacts.types.${type}`).toBeTruthy();
    }
  });

  it('offers OTHER in the type filter', () => {
    expect(GALLERY_ARTIFACT_TYPES).toContain('OTHER');
  });
});
