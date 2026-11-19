import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, screen } from '@testing-library/react';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient: api, streamSSERequest: vi.fn() }));
vi.mock('@/config', () => ({ WorkspaceMenu: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
// Radix only renders the <img> once it has loaded, which never happens in jsdom.
vi.mock('@/app/components/ui/user-avatar', () => ({
  UserAvatar: ({ src }: { src?: string | null }) => <span data-testid="org-logo" data-src={src ?? ''} />,
}));

import { ChatSidebarFooter } from '@/app/(main)/chat/sidebar/footer';
import { MetricsApi, OrgApi } from '../api';
import { updateOrgProfile } from '@/app/(main)/onboarding/api';
import { clearSessionCache } from '@/lib/api/session-cache';

const LOGO = 'data:image/png;base64,bG9nbw==';
let org = { _id: 'o1', registeredName: 'Acme Inc', shortName: 'Acme', domain: 'acme.test', accountType: 'business' };
let logo: Blob | null = null;

function answerReads() {
  api.get.mockImplementation(async (url: string) => {
    if (url === '/api/v1/org') return { status: 200, data: org };
    if (url === '/api/v1/org/logo') return logo ? { status: 200, data: logo } : { status: 204, data: new Blob([]) };
    if (url === '/api/v1/configurationManager/metricsCollection') return { status: 200, data: { enableMetricCollection: 'true' } };
    throw new Error(`unexpected GET ${url}`);
  });
}

const calls = (url: string) => api.get.mock.calls.filter(([u]) => u === url).length;

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderFooter() {
  const view = renderInTheme(<ChatSidebarFooter />);
  await settle();
  return view;
}

const footerLogo = () => screen.getByTestId('org-logo').getAttribute('data-src') || null;

describe('org and logo cache (sidebar footer)', () => {
  beforeEach(() => {
    installBrowserShims();
    vi.useFakeTimers();
    vi.clearAllMocks();
    clearSessionCache();
    org = { _id: 'o1', registeredName: 'Acme Inc', shortName: 'Acme', domain: 'acme.test', accountType: 'business' };
    logo = null;
    answerReads();
    api.put.mockResolvedValue({ data: {} });
    api.delete.mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('reads the org and its logo once per session, across remounts', async () => {
    (await renderFooter()).unmount();
    await settle(3_600_000);
    await renderFooter();

    expect(screen.getByText('Acme')).toBeTruthy();
    expect(calls('/api/v1/org')).toBe(1);
    expect(calls('/api/v1/org/logo')).toBe(1);
  });

  it('shows the new name after the org is saved, with one org request and no logo request', async () => {
    (await renderFooter()).unmount();
    org = { ...org, shortName: 'Acme Labs' };

    await OrgApi.updateOrg({ registeredName: 'Acme Inc', shortName: 'Acme Labs', contactEmail: '', permanentAddress: {}, dataCollectionConsent: false });
    await renderFooter();

    expect(screen.getByText('Acme Labs')).toBeTruthy();
    expect(calls('/api/v1/org')).toBe(2);
    expect(calls('/api/v1/org/logo')).toBe(1);
  });

  it('a failed org save leaves the cache as it was', async () => {
    (await renderFooter()).unmount();
    api.put.mockRejectedValueOnce(new Error('500'));

    await expect(
      OrgApi.updateOrg({ registeredName: 'X', shortName: 'X', contactEmail: '', permanentAddress: {}, dataCollectionConsent: false }),
    ).rejects.toThrow('500');
    await renderFooter();

    expect(screen.getByText('Acme')).toBeTruthy();
    expect(calls('/api/v1/org')).toBe(1);
  });

  it('an uploaded logo is cached from the one read that follows the upload', async () => {
    await renderFooter();
    logo = new Blob(['logo'], { type: 'image/png' });

    // FileReader (data URL) runs on the faked timers, so advance them while the upload settles.
    const upload = OrgApi.uploadLogo(new File(['logo'], 'logo.png', { type: 'image/png' }));
    await settle(50);
    const returned = await upload;
    await settle();

    expect(returned).toBe(LOGO);
    expect(footerLogo()).toBe(LOGO);
    expect(calls('/api/v1/org/logo')).toBe(2);
  });

  it('a removed logo is cleared without another request', async () => {
    logo = new Blob(['logo'], { type: 'image/png' });
    await renderFooter();
    expect(footerLogo()).toBe(LOGO);

    await act(async () => {
      await OrgApi.deleteLogo();
    });
    await settle();

    expect(footerLogo()).toBeNull();
    expect(calls('/api/v1/org/logo')).toBe(1);
  });

  it('saving the metrics switch rereads only the metrics setting', async () => {
    (await renderFooter()).unmount();

    await MetricsApi.toggleMetricsCollection(true);
    await renderFooter();

    expect(calls('/api/v1/org')).toBe(1);
    expect(calls('/api/v1/org/logo')).toBe(1);
  });

  it('the onboarding org step refreshes the footer, even when the footer was not shown', async () => {
    (await renderFooter()).unmount();
    org = { ...org, shortName: 'Acme Onboarded' };

    await updateOrgProfile({
      organizationName: 'Acme Inc', displayName: 'Acme Onboarded', streetAddress: '', city: '', state: '', zipCode: '', country: '',
    } as Parameters<typeof updateOrgProfile>[0]);
    await renderFooter();

    expect(screen.getByText('Acme Onboarded')).toBeTruthy();
  });

  it('a failed org read is not cached: the next mount asks again', async () => {
    api.get.mockRejectedValueOnce(new Error('down'));
    (await renderFooter()).unmount();

    await renderFooter();

    expect(screen.getByText('Acme')).toBeTruthy();
    expect(calls('/api/v1/org')).toBe(2);
  });

  it('a failed logo read is not cached as "no logo": the next mount shows it', async () => {
    logo = new Blob(['logo'], { type: 'image/png' });
    api.get.mockImplementation(async (url: string) => {
      if (url === '/api/v1/org/logo') throw new Error('down');
      return { status: 200, data: org };
    });
    (await renderFooter()).unmount();
    answerReads();

    await renderFooter();

    expect(footerLogo()).toBe(LOGO);
    expect(calls('/api/v1/org/logo')).toBe(2);
  });

  it('an upload whose read-back fails still succeeds, and the logo is read again', async () => {
    await renderFooter();
    logo = new Blob(['logo'], { type: 'image/png' });
    api.get.mockImplementationOnce(async () => {
      throw new Error('down');
    });

    const upload = OrgApi.uploadLogo(new File(['logo'], 'logo.png', { type: 'image/png' }));
    await settle(50);
    await expect(upload).resolves.toBeNull();
    await settle(50);

    expect(footerLogo()).toBe(LOGO);
  });

  it('an org switch rereads the org on screen and shows the new one', async () => {
    await renderFooter();
    org = { ...org, _id: 'o2', shortName: 'Globex' };

    act(() => clearSessionCache({ refetch: true }));
    await settle();

    expect(screen.getByText('Globex')).toBeTruthy();
    expect(calls('/api/v1/org')).toBe(2);
  });

  it('sign-out clears the org: the next session reads it again', async () => {
    (await renderFooter()).unmount();

    clearSessionCache();
    await renderFooter();

    expect(calls('/api/v1/org')).toBe(2);
    expect(calls('/api/v1/org/logo')).toBe(2);
  });
});
