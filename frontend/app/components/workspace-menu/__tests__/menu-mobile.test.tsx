import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/config', () => ({
  logoutFromWorkspaceMenu: vi.fn(),
  SettingsSection: ({ onAppearanceToggle }: { onAppearanceToggle: () => void }) => (
    <button onClick={onAppearanceToggle}>open-appearance</button>
  ),
}));
vi.mock('../external-links-section', () => ({ ExternalLinksSection: () => null }));
vi.mock('@/app/components/theme-provider', () => ({
  useThemeAppearance: () => ({ preference: 'system', setPreference: vi.fn() }),
}));

import { WorkspaceMenu } from '../menu';

afterEach(() => cleanup());

describe('WorkspaceMenu on mobile', () => {
  it('opens Appearance as a bottom sheet dialog', async () => {
    render(<WorkspaceMenu isOpen onClose={vi.fn()} org={null} />);
    expect(screen.queryByRole('dialog')).toBeNull();

    fireEvent.click(screen.getByText('open-appearance'));
    const sheet = await screen.findByRole('dialog', { name: en.workspaceMenu.appearance });
    expect(sheet.textContent).toContain(en.workspaceMenu.dark);
  });

  it('hides the menu card while a sheet is open so the sheet is not covered', async () => {
    render(<WorkspaceMenu isOpen onClose={vi.fn()} org={null} />);
    fireEvent.click(screen.getByText('open-appearance'));
    await screen.findByRole('dialog', { name: en.workspaceMenu.appearance });
    expect(screen.queryByText('open-appearance')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: en.common.back }));
    expect(screen.getByText('open-appearance')).toBeTruthy();
  });
});
