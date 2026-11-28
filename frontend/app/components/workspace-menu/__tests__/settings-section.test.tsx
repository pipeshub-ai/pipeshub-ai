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
  MenuItem: ({ label, href }: { label: React.ReactNode; href?: string }) => (href ? <a href={href}>{label}</a> : <div>{label}</div>),
}));
let isAdmin: boolean | null = true;
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: () => unknown) => selector(),
  selectIsAdmin: () => isAdmin,
}));

import { SettingsSection } from '../settings-section';

afterEach(() => {
  cleanup();
  isAdmin = true;
});

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

  const renderMenu = () => {
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
  };

  it('links an admin to personal and workspace settings', () => {
    renderMenu();
    expect(screen.getByText(en.workspaceMenu.personalSettings).getAttribute('href')).toBe('/workspace/profile/');
    expect(screen.getByText(en.workspaceMenu.workspaceSettings).getAttribute('href')).toBe('/workspace/');
  });

  it.each([false, null])('hides workspace settings when isAdmin is %s', (value) => {
    isAdmin = value;
    renderMenu();
    expect(screen.getByText(en.workspaceMenu.personalSettings)).toBeTruthy();
    expect(screen.queryByText(en.workspaceMenu.workspaceSettings)).toBeNull();
  });
});
