import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

let flagsState: { flags: Record<string, boolean> | null } = { flags: null };
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: typeof flagsState) => unknown) => selector(flagsState),
  selectFeatureFlagsLoaded: (s: typeof flagsState) => s.flags !== null,
  selectRerankerEnabled: (s: typeof flagsState) => s.flags?.ENABLE_RERANKER === true,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      key.split('.').reduce<unknown>((cur, part) => (cur as Record<string, unknown>)?.[part], en) ?? key,
  }),
}));

import { RerankerFlagHint } from '../components/reranker-flag-hint';

afterEach(cleanup);

function renderHint(flags: Record<string, boolean> | null) {
  flagsState = { flags };
  return render(
    <Theme>
      <RerankerFlagHint />
    </Theme>
  );
}

describe('RerankerFlagHint', () => {
  it('points to Labs while reranking is off', () => {
    renderHint({});
    expect(screen.getByTestId('reranker-flag-hint').textContent).toContain(
      en.workspace.aiModels.rerankerOffHint
    );
    expect(screen.getByRole('link', { name: 'Labs' }).getAttribute('href')).toBe('/workspace/labs');
  });

  it('is hidden once reranking is on', () => {
    renderHint({ ENABLE_RERANKER: true });
    expect(screen.queryByTestId('reranker-flag-hint')).toBeNull();
  });

  it('is hidden until flags load, so it never flashes', () => {
    renderHint(null);
    expect(screen.queryByTestId('reranker-flag-hint')).toBeNull();
  });
});
