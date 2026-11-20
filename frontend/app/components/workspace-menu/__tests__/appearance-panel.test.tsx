import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/theme-provider', () => ({
  useThemeAppearance: () => ({ preference: 'dark', setPreference: vi.fn() }),
}));

import { AppearancePanel } from '../appearance-panel';
import { LanguagePanel } from '../language-panel';

afterEach(() => cleanup());

describe('AppearancePanel', () => {
  it('exposes the choice as a radio group with the current preference checked', () => {
    render(
      <Theme>
        <AppearancePanel isOpen onBack={vi.fn()} />
      </Theme>,
    );
    expect(screen.getByRole('radiogroup', { name: en.workspaceMenu.appearance })).toBeTruthy();
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(screen.getByRole('radio', { name: en.workspaceMenu.dark }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radio', { name: en.workspaceMenu.system }).getAttribute('aria-checked')).toBe('false');
  });
});

describe('LanguagePanel', () => {
  it('keeps plain buttons (not a radio group)', () => {
    render(
      <Theme>
        <LanguagePanel isOpen onBack={vi.fn()} />
      </Theme>,
    );
    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(screen.queryAllByRole('radio')).toHaveLength(0);
  });
});
