import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AxiosError, AxiosHeaders } from 'axios';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));
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
vi.mock('@/lib/hooks/use-frontend-public-url', () => ({ useFrontendPublicUrl: () => 'https://search.example.test' }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const api = vi.hoisted(() => ({ getStatus: vi.fn(), install: vi.fn() }));
vi.mock('../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../api')>()), LicenseApi: api }));

import LicensePage from '../page';
import type { LicenseStatusView, LicenseView } from '../types';

const DAY = 24 * 60 * 60 * 1000;
const iso = (offsetDays: number) => new Date(Date.now() + offsetDays * DAY).toISOString();

const license = (over: Partial<LicenseView> = {}): LicenseView => ({
  licenseId: 'lic-1',
  licensee: 'Example Labs',
  type: 'pro',
  seats: 25,
  features: ['permissionSync'],
  url: 'https://search.example.test',
  notForProduction: false,
  startDate: iso(-10),
  endDate: iso(300),
  graceEndsAt: iso(303),
  ...over,
});

const view = (over: Partial<LicenseStatusView> = {}): LicenseStatusView => ({
  status: 'none',
  license: null,
  upcoming: null,
  permissionSync: null,
  ignored: 0,
  upgradeGrace: null,
  ...over,
});

/** A refusal as apiClient rejects it: a ProcessedError whose originalError is the axios error. */
const refusal = (code: string, message = 'server words') => {
  const response = {
    status: 400,
    statusText: 'Bad Request',
    data: { error: { code, message } },
    headers: {},
    config: { headers: new AxiosHeaders() },
  };
  return { message, originalError: new AxiosError(message, '400', undefined, undefined, response) };
};

const renderPage = () =>
  render(
    <Theme>
      <LicensePage />
    </Theme>,
  );

const paste = (text: string) => fireEvent.change(screen.getByLabelText('License key'), { target: { value: text } });

beforeEach(() => api.getStatus.mockResolvedValue(view()));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('License page: status', () => {
  it('shows a pro key in force with its seats and permission sync on', async () => {
    api.getStatus.mockResolvedValue(
      view({ status: 'active', license: license(), permissionSync: { allowed: true, until: iso(303) } }),
    );
    renderPage();
    expect(await screen.findByText('Active')).toBeTruthy();
    expect(screen.getByText('Pro')).toBeTruthy();
    expect(screen.getByText('25')).toBeTruthy();
    expect(screen.getByText('On')).toBeTruthy();
    expect(screen.queryByText('Not for production')).toBeNull();
    expect(screen.queryByText('Get a trial key')).toBeNull();
  });

  it('marks a development key as not for production, with unlimited shown in words', async () => {
    api.getStatus.mockResolvedValue(
      view({
        status: 'active',
        license: license({ type: 'development', notForProduction: true, seats: 'unlimited' }),
        permissionSync: { allowed: true, until: iso(303) },
      }),
    );
    renderPage();
    expect(await screen.findByText('Not for production')).toBeTruthy();
    expect(screen.getByText('Development')).toBeTruthy();
    expect(screen.getByText('Unlimited')).toBeTruthy();
  });

  it('with enforcement off and no key: permission sync on, no grace, a trial link carrying the address', async () => {
    renderPage();
    expect(await screen.findByText('No license key')).toBeTruthy();
    expect(screen.getByText('On')).toBeTruthy();
    expect(screen.getByText(/doesn't require a license key yet/)).toBeTruthy();
    expect(screen.queryByText(/Permission sync stays on until/)).toBeNull();
    expect(screen.queryByText('Request a 14-day extension')).toBeNull();
    const trial = screen.getByText('Get a trial key').closest('a');
    expect(trial?.getAttribute('href')).toBe(
      'https://procurement.pipeshub.com/trial?url=https%3A%2F%2Fsearch.example.test',
    );
  });

  it('during the upgrade grace: the end date and an extension link', async () => {
    const endsAt = iso(10);
    api.getStatus.mockResolvedValue(
      view({ permissionSync: { allowed: true, until: endsAt }, upgradeGrace: { endsAt, affected: true } }),
    );
    renderPage();
    expect(await screen.findByText(/Permission sync stays on until/)).toBeTruthy();
    const link = new URL(screen.getByText('Request a 14-day extension').closest('a')!.getAttribute('href')!);
    expect(link.pathname).toBe('/extension');
    expect(link.searchParams.get('url')).toBe('https://search.example.test');
    expect(link.searchParams.get('graceEndsAt')).toBe(endsAt);
  });

  it('after the grace with no key: permission sync off and what that means', async () => {
    api.getStatus.mockResolvedValue(
      view({ permissionSync: { allowed: false, until: null }, upgradeGrace: { endsAt: iso(-1), affected: true } }),
    );
    renderPage();
    expect(await screen.findByText('Off')).toBeTruthy();
    expect(screen.getByText(/Nothing was deleted/)).toBeTruthy();
    expect(screen.queryByText('Request a 14-day extension')).toBeNull();
  });
});

describe('License page: installing a key', () => {
  it('sends the pasted file as JSON and shows the new status', async () => {
    const file = { license: { licenseId: 'lic-1' }, signature: 'sig' };
    api.install.mockResolvedValue({
      installed: true,
      alreadyInstalled: false,
      status: view({ status: 'active', license: license(), permissionSync: { allowed: true, until: iso(303) } }),
    });
    renderPage();
    await screen.findByText('No license key');
    paste(JSON.stringify(file));
    fireEvent.click(screen.getByRole('button', { name: 'Install key' }));
    await waitFor(() => expect(api.install).toHaveBeenCalledWith(file));
    expect(await screen.findByText('Active')).toBeTruthy();
    expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'License key installed' }));
  });

  it('refuses text that is not JSON without calling the server', async () => {
    renderPage();
    await screen.findByText('No license key');
    paste('not a key');
    fireEvent.click(screen.getByRole('button', { name: 'Install key' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/isn't a license key file/);
    expect(api.install).not.toHaveBeenCalled();
  });

  it.each([
    ['HTTP_LICENSE_BAD_SIGNATURE', /signature doesn't match/],
    ['HTTP_LICENSE_WRONG_ADDRESS', /different web address/],
    ['HTTP_LICENSE_EXPIRED', /already ended/],
  ])('words the %s refusal plainly', async (code, words) => {
    api.install.mockRejectedValue(refusal(code));
    renderPage();
    await screen.findByText('No license key');
    paste('{"license":{}}');
    fireEvent.click(screen.getByRole('button', { name: 'Install key' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(words);
  });

  it("keeps the server's sentence for a rarer refusal", async () => {
    api.install.mockRejectedValue(refusal('HTTP_LICENSE_NEVER_IN_FORCE', 'This license would never take effect.'));
    renderPage();
    await screen.findByText('No license key');
    paste('{"license":{}}');
    fireEvent.click(screen.getByRole('button', { name: 'Install key' }));
    expect((await screen.findByRole('alert')).textContent).toBe('This license would never take effect.');
  });

  it('reads an uploaded key file into the box', async () => {
    renderPage();
    await screen.findByText('No license key');
    const upload = new File(['{"license":{"licenseId":"x"}}'], 'key.json', { type: 'application/json' });
    fireEvent.change(screen.getByTestId('license-file-input'), { target: { files: [upload] } });
    await waitFor(() =>
      expect((screen.getByLabelText('License key') as HTMLTextAreaElement).value).toBe('{"license":{"licenseId":"x"}}'),
    );
  });
});
