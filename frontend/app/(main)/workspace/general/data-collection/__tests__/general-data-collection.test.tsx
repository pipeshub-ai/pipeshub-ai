import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const addToast = vi.hoisted(() => vi.fn());
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), refresh: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient: api, streamSSERequest: vi.fn() }));
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => router,
}));
vi.mock('@/lib/store/toast-store', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/store/toast-store')>()),
  useToastStore: (select: (s: { addToast: typeof addToast }) => unknown) => select({ addToast }),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
// The page as the default build wires it: the Data Collection switch in the settings slot.
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  GeneralPageExtras: () => null,
  useGeneralPageSettings: (await import('../use-general-data-collection')).useGeneralDataCollection,
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../../../components', async (importActual) => ({
  ...(await importActual<typeof import('../../../components')>()),
  // Radix only renders the <img> once it has loaded, which never happens in jsdom.
  AvatarUploadWidget: ({ src }: { src?: string | null }) => <span data-testid="logo" data-src={src ?? ''} />,
}));

import GeneralPage from '../../page';
import { useGeneralStore } from '../../store';
import { MetricsApi } from '../api';
import { useUserStore } from '@/lib/store/user-store';
import { clearSessionCache } from '@/lib/api/session-cache';

const ORG = {
  _id: 'o1',
  registeredName: 'Acme Inc',
  shortName: 'Acme',
  contactEmail: 'admin@acme.test',
  permanentAddress: { addressLine1: '1 Main St', city: 'Springfield', state: 'IL', postCode: '62701', country: 'US' },
};
const METRICS_URL = '/api/v1/configurationManager/metricsCollection';
let org = ORG;
let metrics: Promise<{ data: unknown }> | { data: unknown } | null = null;

function answerReads() {
  api.get.mockImplementation(async (url: string) => {
    if (url === '/api/v1/org') return { status: 200, data: org };
    if (url === '/api/v1/org/logo') return { status: 204, data: new Blob([]) };
    if (url === METRICS_URL) return metrics ?? { status: 200, data: { enableMetricCollection: 'false' } };
    throw new Error(`unexpected GET ${url}`);
  });
}

const calls = (url: string) => api.get.mock.calls.filter(([u]) => u === url).length;
const form = () => useGeneralStore.getState().form;
/** The switch's state lives with its section, not in the page's form. */
const dataCollectionSwitch = () => screen.getByRole('switch');
const isOn = () => dataCollectionSwitch().getAttribute('aria-checked') === 'true';
const saveBarHidden = () => screen.getByRole('button', { name: /save/i }).closest('[inert]') !== null;

function signedIn(isAdmin: boolean) {
  useUserStore.setState({
    isInitialized: true,
    profile: {
      userId: 'u1', firstName: null, lastName: null, fullName: 'Admin', email: 'a@acme.test',
      isAdmin, avatarUrl: null, hasLoggedIn: true, designation: null,
    },
  });
}

async function renderPage() {
  const view = renderInTheme(<GeneralPage />);
  await waitFor(() => expect(useGeneralStore.getState().isLoading).toBe(false));
  return view;
}

async function renderWith(setting: Record<string, unknown>) {
  metrics = { data: setting };
  return renderPage();
}

describe('General settings page: Data Collection', () => {
  beforeEach(() => {
    installBrowserShims();
    vi.clearAllMocks();
    clearSessionCache();
    useGeneralStore.getState().reset();
    org = ORG;
    metrics = null;
    answerReads();
    api.put.mockResolvedValue({ data: {} });
    signedIn(true);
  });

  afterEach(() => cleanup());

  it('a revisit reads the setting from the cache', async () => {
    (await renderPage()).unmount();
    useGeneralStore.getState().reset();

    await renderPage();

    expect(calls(METRICS_URL)).toBe(1);
  });

  it('never asks a member for the metrics setting', async () => {
    signedIn(false);

    await renderPage();

    expect(screen.getByDisplayValue('Acme Inc')).toBeTruthy();
    expect(calls(METRICS_URL)).toBe(0);
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it("waits for an admin's metrics setting before showing the form", async () => {
    let finish: (value: { data: unknown }) => void = () => undefined;
    metrics = new Promise((resolve) => { finish = resolve; });
    renderInTheme(<GeneralPage />);
    await waitFor(() => expect(calls(METRICS_URL)).toBe(1));
    expect(useGeneralStore.getState().isLoading).toBe(true);

    await act(async () => {
      finish({ data: { enableMetricCollection: 'true' } });
    });

    await waitFor(() => expect(useGeneralStore.getState().isLoading).toBe(false));
    await waitFor(() => expect(isOn()).toBe(true));
    expect(saveBarHidden()).toBe(true);
  });

  it('a setting that cannot be read is asked for once more, then the load error shows and Save stays away', async () => {
    metrics = Promise.reject(new Error('down'));
    metrics.catch(() => undefined);

    await renderPage();

    expect(calls(METRICS_URL)).toBe(2);
    expect(addToast).toHaveBeenCalledTimes(1);
    expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'workspace.general.toasts.loadError' }));
    expect(saveBarHidden()).toBe(true);
  });

  it('saving the name and the data-collection switch together keeps the new values and raises no load error', async () => {
    await renderPage();
    act(() => useGeneralStore.getState().setField('displayName', 'Acme Labs'));
    fireEvent.click(dataCollectionSwitch());
    org = { ...ORG, shortName: 'Acme Labs' };
    // The metrics reread after the toggle fails: it must not undo the switch or toast a load error.
    metrics = Promise.reject(new Error('down'));
    metrics.catch(() => undefined);

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(calls(METRICS_URL)).toBe(2));
    await waitFor(() => expect(calls('/api/v1/org')).toBe(2));
    // The organization update first, then the toggle. The update does not carry the switch: its handler never read it.
    expect(api.put.mock.calls.map(([url]) => url)).toEqual(['/api/v1/org/', `${METRICS_URL}/toggle`]);
    expect(api.put.mock.calls[0][1]).toMatchObject({ shortName: 'Acme Labs' });
    expect(api.put.mock.calls[0][1]).not.toHaveProperty('dataCollectionConsent');
    expect(api.put.mock.calls[1][1]).toEqual({ enableMetricCollection: true });
    expect(form()).toMatchObject({ displayName: 'Acme Labs' });
    expect(isOn()).toBe(true);
    await waitFor(() => expect(saveBarHidden()).toBe(true));
    expect(addToast).not.toHaveBeenCalledWith(expect.objectContaining({ title: 'workspace.general.toasts.loadError' }));
  });

  it('a save that leaves the switch alone does not toggle the setting', async () => {
    await renderWith({ enableMetricCollection: 'true' });
    act(() => useGeneralStore.getState().setField('city', 'Shelbyville'));

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.put).toHaveBeenCalled());
    await waitFor(() => expect(saveBarHidden()).toBe(true));
    expect(api.put.mock.calls.map(([url]) => url)).toEqual(['/api/v1/org/']);
    expect(api.put.mock.calls[0][1]).not.toHaveProperty('dataCollectionConsent');
  });

  it('the switch alone offers Save, and Discard puts it back', async () => {
    await renderPage();
    expect(saveBarHidden()).toBe(true);

    fireEvent.click(dataCollectionSwitch());
    expect(isOn()).toBe(true);
    expect(saveBarHidden()).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /discard/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'workspace.general.discardDialog.confirm' }));

    await waitFor(() => expect(isOn()).toBe(false));
    expect(saveBarHidden()).toBe(true);
    expect(api.put).not.toHaveBeenCalled();
  });

  it('a failed toggle reports the save error and keeps the switch unsaved', async () => {
    await renderPage();
    fireEvent.click(dataCollectionSwitch());
    api.put.mockImplementation(async (url: string) => {
      if (url.endsWith('/toggle')) throw new Error('500');
      return { data: {} };
    });

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'workspace.general.toasts.saveError' })),
    );
    expect(saveBarHidden()).toBe(false);
  });

  // Shown exactly when telemetry can run, so an admin can always see whatever is collected
  // and switch it off; hidden when nothing can run.
  it('is hidden when telemetry is switched off on the deployment', async () => {
    await renderWith({ enableMetricCollection: 'false', telemetryEnabled: false });
    expect(screen.queryByText('workspace.general.dataCollection')).toBeNull();
  });

  it('stays hidden when telemetry is off on the deployment, even if the switch was left on', async () => {
    await renderWith({ enableMetricCollection: 'true', telemetryEnabled: false });
    expect(screen.queryByText('workspace.general.dataCollection')).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it('is shown when the deployment says telemetry can run', async () => {
    await renderWith({ enableMetricCollection: 'true', telemetryEnabled: true });
    expect(screen.getByText('workspace.general.dataCollection')).toBeTruthy();
    expect(isOn()).toBe(true);
  });

  it('is shown as before by a server that does not report telemetryEnabled', async () => {
    await renderWith({ enableMetricCollection: 'false' });
    expect(screen.getByText('workspace.general.dataCollection')).toBeTruthy();
    expect(isOn()).toBe(false);
  });

  it('saving the switch rereads only the metrics setting', async () => {
    await renderPage();

    await act(async () => {
      await MetricsApi.toggleMetricsCollection(true);
    });

    await waitFor(() => expect(calls(METRICS_URL)).toBe(2));
    expect(calls('/api/v1/org')).toBe(1);
    expect(calls('/api/v1/org/logo')).toBe(1);
  });
});
