import { describe, expect, it } from 'vitest';
import { createInstance } from 'i18next';
import { getConnectorUiExtras, matchPreset, pageLimitStatus, suggestWebPreset } from '../connector-presets';
import { connectorTypeKey, localizeSyncField, localizeSyncFieldGroup } from '../localize-sync-field';
import en from '@/lib/i18n/locales/en-US.json';
import type { SyncCustomField } from '../../types';

const web = getConnectorUiExtras('web')!;

describe('web presets', () => {
  it('matches a preset on every key it sets, comparing loosely typed form values', () => {
    expect(matchPreset(web.presets, {
      type: 'recursive', discovery: 'sitemap_and_links', restrict_to_start_path: 'true', depth: '5', max_pages: 1000,
    })).toBe('docs');
    expect(matchPreset(web.presets, { type: 'single', depth: 9 })).toBe('onePage');
    expect(matchPreset(web.presets, {
      type: 'recursive', discovery: 'links', restrict_to_start_path: true, depth: 5, max_pages: 1000,
    })).toBeNull();
    expect(matchPreset(web.presets, { max_pages: 2000 }, {
      type: 'recursive', discovery: 'sitemap_and_links', restrict_to_start_path: true, depth: 5,
    })).toBe('helpCenter');
  });

  it('types preset values like the form does', () => {
    const docs = web.presets.find((p) => p.id === 'docs')!;
    expect(typeof docs.values.depth).toBe('number');
    expect(typeof docs.values.restrict_to_start_path).toBe('boolean');
  });

  it('suggests a preset from the URL path', () => {
    expect(suggestWebPreset({ url: 'https://example.com/docs/intro' })).toBe('docs');
    expect(suggestWebPreset({ url: 'support.example.com/hc/en-us' })).toBe('helpCenter');
    expect(suggestWebPreset({ url: 'https://example.com/help' })).toBe('helpCenter');
    expect(suggestWebPreset({ url: 'https://example.com/support/' })).toBe('helpCenter');
    expect(suggestWebPreset({ url: 'https://example.com/blog/docsify' })).toBeNull();
    expect(suggestWebPreset({ url: '' })).toBeNull();
    expect(suggestWebPreset({ url: 'http://[bad' })).toBeNull();
  });

  it('has no presets for other connectors', () => {
    expect(getConnectorUiExtras('rss')).toBeNull();
  });
});

describe('pageLimitStatus', () => {
  it('is empty without a previous run', () => {
    expect(pageLimitStatus(null, 100)).toBeNull();
    expect(pageLimitStatus({ stopReason: null }, 100)).toBeNull();
  });

  it('reports the stop at the limit and warns at 90 %', () => {
    expect(pageLimitStatus({ pagesSaved: 3, pagesStored: 900, stopReason: 'max_pages', skippedMaxPages: 12 }, 1000))
      .toEqual({ saved: 900, stoppedAtLimit: true, leftOut: 12, nearLimit: true });
    expect(pageLimitStatus({ pagesStored: 899 }, '1000'))
      .toEqual({ saved: 899, stoppedAtLimit: false, leftOut: 0, nearLimit: false });
  });

  it('counts pages kept, not pages written by the run', () => {
    // An unchanged re-sync writes almost nothing; a run from before pagesStored existed shows no line.
    expect(pageLimitStatus({ pagesSaved: 950 }, 1000)).toBeNull();
  });
});

describe('localizeSyncField', () => {
  const field: SyncCustomField = {
    name: 'type', displayName: 'Crawl Type', description: 'Schema help', fieldType: 'SELECT',
    options: ['single', 'recursive', 'odd'], optionLabels: { odd: 'Odd one' },
  };

  it('prefers translations, then schema text, then the raw value', async () => {
    const i18n = createInstance();
    await i18n.init({ lng: 'en-US', resources: { 'en-US': { translation: en } } });
    const t = i18n.t.bind(i18n);
    expect(connectorTypeKey('Google Drive')).toBe('googledrive');
    const out = localizeSyncField(field, connectorTypeKey('Web'), t);
    expect(out.displayName).toBe('Crawl');
    expect(out.description).toBe('Schema help');
    expect(out.optionLabels).toEqual({
      single: 'Only this page', recursive: 'This page and the pages it links to', odd: 'Odd one',
    });
    const other = localizeSyncField({ ...field, name: 'x', options: ['a'], optionLabels: undefined }, 'rss', t);
    expect(other).toMatchObject({ displayName: 'Crawl Type', description: 'Schema help', optionLabels: { a: 'a' } });
    expect(other.unit).toBeUndefined();
    expect(localizeSyncFieldGroup({ name: 'advanced', displayName: 'X', description: '' }, 'web', t)).toEqual({
      name: 'advanced', displayName: 'Advanced', description: 'Crawl depth, file size, browser and crawler identity.',
    });
  });
});
