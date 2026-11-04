import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isProfileInitialized: boolean }) => unknown) =>
    selector({ isAdmin: true, isProfileInitialized: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  selectIsProfileInitialized: (s: { isProfileInitialized: boolean }) => s.isProfileInitialized,
}));

const addToast = vi.hoisted(() => vi.fn());
vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: typeof addToast }) => unknown) => selector({ addToast }),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/smtp-configure-panel', () => ({ SmtpConfigurePanel: () => null }));

const api = vi.hoisted(() => ({
  getSmtpConfig: vi.fn(),
  saveSmtpConfig: vi.fn(),
  deleteSmtpConfig: vi.fn(),
  setSmtpEnabled: vi.fn(),
}));
vi.mock('../api', () => ({ SmtpApi: api }));

import MailPage from '../page';

const STORED = { host: 'smtp.example.test', port: 587, fromEmail: 'noreply@example.test' };

function renderPage() {
  return render(
    <Theme>
      <MailPage />
    </Theme>,
  );
}

beforeEach(() => {
  api.getSmtpConfig.mockResolvedValue(STORED);
  api.deleteSmtpConfig.mockResolvedValue(undefined);
  api.setSmtpEnabled.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('Mail page: turning email off and deleting the settings', () => {
  it('asks before turning email off, then shows it as turned off', async () => {
    renderPage();
    const toggle = await screen.findByRole('switch', { name: 'Send email' });

    fireEvent.click(toggle);
    const dialog = await screen.findByRole('alertdialog', { name: 'Turn off email?' });
    expect(api.setSmtpEnabled).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Turn off' }));

    await waitFor(() => expect(api.setSmtpEnabled).toHaveBeenCalledWith(false));
    expect(await screen.findByText('Turned off')).toBeTruthy();
  });

  it('turns email back on without asking', async () => {
    api.getSmtpConfig.mockResolvedValue({ ...STORED, enabled: false });
    renderPage();
    const toggle = await screen.findByRole('switch', { name: 'Send email' });

    fireEvent.click(toggle);

    await waitFor(() => expect(api.setSmtpEnabled).toHaveBeenCalledWith(true));
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('deletes only after a confirmation that carries a red warning', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete SMTP settings' }));

    const dialog = await screen.findByRole('alertdialog', { name: 'Delete SMTP settings?' });
    expect(within(dialog).getByText(/cannot be undone/)).toBeTruthy();
    expect(api.deleteSmtpConfig).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(api.deleteSmtpConfig).toHaveBeenCalledOnce());
    expect(await screen.findByText('Not configured')).toBeTruthy();
  });

  it('shows what is stored when turning email off fails', async () => {
    api.setSmtpEnabled.mockRejectedValue(new Error('mail service reload failed'));
    renderPage();
    fireEvent.click(await screen.findByRole('switch', { name: 'Send email' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Turn off email?' });
    // The setting was saved before the reload failed.
    api.getSmtpConfig.mockResolvedValue({ ...STORED, enabled: false });

    fireEvent.click(within(dialog).getByRole('button', { name: 'Turn off' }));

    expect(await screen.findByText('Turned off')).toBeTruthy();
    expect(api.getSmtpConfig).toHaveBeenCalledTimes(2);
  });

  it('shows what is stored when deleting fails', async () => {
    api.deleteSmtpConfig.mockRejectedValue(new Error('mail service reload failed'));
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete SMTP settings' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete SMTP settings?' });
    api.getSmtpConfig.mockResolvedValue(null);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    expect(await screen.findByText('Not configured')).toBeTruthy();
    expect(api.getSmtpConfig).toHaveBeenCalledTimes(2);
  });

  it('offers neither control when nothing is configured', async () => {
    api.getSmtpConfig.mockResolvedValue(null);
    renderPage();

    expect(await screen.findByText('Not configured')).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Send email' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete SMTP settings' })).toBeNull();
  });

  it('offers neither control for settings inherited from a parent organization', async () => {
    api.getSmtpConfig.mockResolvedValue({ ...STORED, inherited: true });
    renderPage();

    expect(await screen.findByText('Configured')).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Send email' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete SMTP settings' })).toBeNull();
  });
});
