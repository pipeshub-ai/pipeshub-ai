import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
// The OS resolves to light while the user picked "System".
vi.mock('@/app/components/theme-provider', () => ({
  useThemeAppearance: () => ({ appearance: 'light', preference: 'system' }),
}));
vi.mock('../menu-item', () => ({
  MenuItem: ({ label }: { label: React.ReactNode }) => <div>{label}</div>,
}));

import { SettingsSection } from '../settings-section';

afterEach(() => cleanup());

describe('SettingsSection', () => {
  it('labels Appearance with the chosen preference, not the resolved mode', () => {
    const noop = vi.fn();
    render(
      <SettingsSection
        onWorkspaceSettings={noop}
        onAppearanceToggle={noop}
        isAppearanceActive={false}
        onLanguageToggle={noop}
        isLanguageActive={false}
        onLogout={noop}
      />
    );
    expect(screen.getByText(en.workspaceMenu.system)).toBeTruthy();
    expect(screen.queryByText(en.workspaceMenu.lightMode)).toBeNull();
  });
});
