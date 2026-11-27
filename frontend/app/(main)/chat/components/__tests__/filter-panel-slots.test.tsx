import React from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, renderHook } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const prompt = vi.hoisted(() => ({ show: true }));
vi.mock('@/lib/hooks/use-enterprise-prompt', () => ({ useEnterprisePrompt: () => prompt.show }));
// These tests cover the default tab's notice, whatever the build's config provides.
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  EnterpriseFeatureNotice: (await import('@/app/components/ui/enterprise-badge')).EnterpriseFeatureNotice,
}));
import {
  FilterPanelFooterExtras,
  useFilterPanelExtraTab,
} from '../chat-panel/expansion-panels/filter-panel-extras';
import { SelectedFiltersRow } from '../selected-filters-row';
import { SelectedCollections } from '../selected-collections';

const pills = [
  { id: 'a1', name: 'Drive', kind: 'connector' as const },
  { id: 'k1', name: 'Pricing', kind: 'collection' as const },
];
const selection = { apps: ['a1', 'k1'], kb: [] };

describe('chat filter panel slots (defaults)', () => {
  // jsdom has no ResizeObserver; the pill row may watch its own width.
  beforeAll(() => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  });
  afterAll(() => vi.unstubAllGlobals());
  afterEach(cleanup);

  it('adds a Quick filters tab that says where the feature is, and does not open on it', () => {
    const { result } = renderHook(() => useFilterPanelExtraTab());
    expect(result.current?.label).toBeTruthy();
    expect(result.current?.openFirst).toBeFalsy();
    const Panel = result.current!.Panel;
    const { getByTestId } = render(
      <Theme>
        <Panel selection={selection} onSelectionChange={() => {}} />
      </Theme>
    );
    expect(getByTestId('enterprise-feature-notice')).toBeTruthy();
  });

  it('adds no tab for people the notice is not shown to', () => {
    prompt.show = false;
    const { result } = renderHook(() => useFilterPanelExtraTab());
    expect(result.current).toBeNull();
    prompt.show = true;
  });

  it('adds nothing to the filter panel footer', () => {
    const { container } = render(
      <FilterPanelFooterExtras selection={selection} onSelectionChange={() => {}} />
    );
    expect(container.innerHTML).toBe('');
  });

  it('shows the composer pills exactly as before', () => {
    const plain = render(
      <Theme>
        <SelectedCollections collections={pills} removable onRemove={() => {}} />
      </Theme>
    ).container.innerHTML;
    cleanup();
    const slot = render(
      <Theme>
        <SelectedFiltersRow
          collections={pills}
          removable
          onRemove={() => {}}
          selection={selection}
          onSelectionChange={() => {}}
        />
      </Theme>
    ).container.innerHTML;
    expect(slot).toBe(plain);
  });
});
