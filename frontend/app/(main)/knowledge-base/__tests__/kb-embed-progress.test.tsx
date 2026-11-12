import { describe, expect, it } from 'vitest';
import { createInstance } from 'i18next';
import type { TFunction } from 'i18next';
import enUS from '@/lib/i18n/locales/en-US.json';
import { getKbItemStatusLabel } from '../components/kb-list-view';
import { hubNode } from './kb-page-harness';

// The real English catalogue, so the key, the plural form and the number format are all exercised.
const i18n = createInstance();
void i18n.init({ lng: 'en-US', resources: { 'en-US': { translation: enUS } }, interpolation: { escapeValue: false } });
const t = i18n.t.bind(i18n) as unknown as TFunction;

const record = (fields: Parameters<typeof hubNode>[0] extends infer P ? Partial<P> : never) =>
  hubNode({ id: 'r1', name: 'big.csv', indexingStatus: 'IN_PROGRESS', ...fields });

describe('embedding progress in the status of an in-progress record', () => {
  it('shows how many chunks are embedded', () => {
    expect(getKbItemStatusLabel(record({ embedDone: 340, embedTotal: 1200 }), t)).toBe('Embedding 340 / 1,200 chunks');
  });

  it('shows a complete count as it is, while the record is still in progress', () => {
    expect(getKbItemStatusLabel(record({ embedDone: 1200, embedTotal: 1200 }), t)).toBe('Embedding 1,200 / 1,200 chunks');
  });

  it('uses the singular for one chunk', () => {
    expect(getKbItemStatusLabel(record({ embedDone: 0, embedTotal: 1 }), t)).toBe('Embedding 0 / 1 chunk');
  });

  it('shows exactly what it showed before when the API sends no progress', () => {
    expect(getKbItemStatusLabel(record({}), t)).toBe('In progress');
    expect(getKbItemStatusLabel(record({ embedDone: null, embedTotal: null }), t)).toBe('In progress');
  });

  it.each([
    [5, undefined],
    [undefined, 5],
    [5, 0],
    [-1, 5],
    [Number.NaN, 5],
    [1, Number.POSITIVE_INFINITY],
  ])('ignores an unusable pair (%s, %s)', (embedDone, embedTotal) => {
    expect(getKbItemStatusLabel(record({ embedDone, embedTotal }), t)).toBe('In progress');
  });

  it('holds a count that ran ahead of the total at the total', () => {
    expect(getKbItemStatusLabel(record({ embedDone: 1300, embedTotal: 1200 }), t)).toBe('Embedding 1,200 / 1,200 chunks');
  });

  it('keeps the short label short', () => {
    expect(getKbItemStatusLabel(record({ embedDone: 340, embedTotal: 1200 }), t, false)).toBe('In progress');
  });

  it('shows no progress on a record that is not in progress', () => {
    const done = hubNode({ id: 'r2', name: 'a.csv', indexingStatus: 'COMPLETED', embedDone: 340, embedTotal: 1200 });
    expect(getKbItemStatusLabel(done, t)).toBe('Completed');
  });
});
