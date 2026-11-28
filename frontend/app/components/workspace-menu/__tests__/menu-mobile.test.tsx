import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { en } from '@/lib/__tests__/test-i18n';
import { runTimersWithPageTornDown } from '@/lib/__tests__/torn-down-page';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/config', () => ({
  logoutFromWorkspaceMenu: vi.fn(),
  SettingsSection: ({ onAppearanceToggle }: { onAppearanceToggle: () => void }) => (
    <button onClick={onAppearanceToggle} data-opens-panel="Appearance">
      open-appearance
    </button>
  ),
}));
vi.mock('../external-links-section', () => ({ ExternalLinksSection: () => null }));
vi.mock('@/app/components/theme-provider', () => ({
  useThemeAppearance: () => ({ preference: 'system', setPreference: vi.fn() }),
}));

import { WorkspaceMenu } from '../menu';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('WorkspaceMenu on mobile', () => {
  it('stays quiet when the page is torn down before the focus tick that follows unmount', () => {
    vi.useFakeTimers();
    render(<WorkspaceMenu isOpen onClose={vi.fn()} org={null} />);
    cleanup();
    // The popover's focus scope reports the close a tick after unmount, possibly after teardown.
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    expect(runTimersWithPageTornDown).not.toThrow();
  });

  it('stays quiet when the page is torn down between closing and its focus tick', () => {
    vi.useFakeTimers();
    function Harness() {
      const [open, setOpen] = React.useState(false);
      return (
        <WorkspaceMenu
          isOpen={open}
          onOpen={() => setOpen(true)}
          onClose={() => setOpen(false)}
          org={null}
          trigger={<button type="button">workspace-menu-trigger</button>}
        />
      );
    }
    render(<Harness />);
    fireEvent.click(screen.getByText('workspace-menu-trigger'));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    // The popover's focus scope reports the close a tick later; the menu then waits one more.
    vi.runOnlyPendingTimers();
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    cleanup();
    expect(runTimersWithPageTornDown).not.toThrow();
  });

  it('opens Appearance as a bottom sheet dialog', async () => {
    render(<WorkspaceMenu isOpen onClose={vi.fn()} org={null} />);
    // The menu itself is a popover (role=dialog); the sheet is not open yet.
    expect(screen.queryByRole('dialog', { name: en.workspaceMenu.appearance })).toBeNull();

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

  it('Back from the sheet puts focus on the menu row that opened it (the row renders again)', async () => {
    render(<WorkspaceMenu isOpen onClose={vi.fn()} org={null} />);
    fireEvent.click(screen.getByText('open-appearance'));
    await screen.findByRole('dialog', { name: en.workspaceMenu.appearance });
    fireEvent.click(screen.getByRole('button', { name: en.common.back }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByText('open-appearance')));
  });
});
