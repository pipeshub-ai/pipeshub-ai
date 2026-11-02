import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const addToast = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiClient: api, streamSSERequest: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (select: (s: { addToast: typeof addToast }) => unknown) => select({ addToast }),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../../components', async (importActual) => ({
  ...(await importActual<typeof import('../../components')>()),
  // Radix only renders the <img> once it has loaded, which never happens in jsdom.
  AvatarUploadWidget: ({ src }: { src?: string | null }) => <span data-testid="logo" data-src={src ?? ''} />,
}));

import GeneralPage from '../page';
import { useOrg } from '../use-org';

function OrgReader() {
  const { error } = useOrg();
  return error ? <span>failed</span> : null;
}
import { useGeneralStore } from '../store';
import { useUserStore } from '@/lib/store/user-store';
import { clearSessionCache } from '@/lib/api/session-cache';

const ORG = {
  _id: 'o1',
  registeredName: 'Acme Inc',
  shortName: 'Acme',
  contactEmail: 'admin@acme.test',
  permanentAddress: { addressLine1: '1 Main St', city: 'Springfield', state: 'IL', postCode: '62701', country: 'US' },
};
let org = ORG;
let metrics: Promise<{ data: unknown }> | null = null;

function answerReads() {
  api.get.mockImplementation(async (url: string) => {
    if (url === '/api/v1/org') return { status: 200, data: org };
    if (url === '/api/v1/org/logo') return { status: 204, data: new Blob([]) };
    if (url === '/api/v1/configurationManager/metricsCollection') {
      return metrics ?? { status: 200, data: { enableMetricCollection: 'false' } };
    }
    throw new Error(`unexpected GET ${url}`);
  });
}

const calls = (url: string) => api.get.mock.calls.filter(([u]) => u === url).length;
const form = () => useGeneralStore.getState().form;

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

describe('General settings page', () => {
  beforeEach(() => {
    installBrowserShims();
    vi.clearAllMocks();
    clearSessionCache();
    useGeneralStore.getState().reset();
    org = ORG;
    metrics = null;
    answerReads();
    api.put.mockResolvedValue({ data: {} });
    URL.createObjectURL = vi.fn(() => 'blob:preview');
    URL.revokeObjectURL = vi.fn();
    signedIn(true);
  });

  afterEach(() => cleanup());

  it('a revisit shows the cached org and makes no org or logo request', async () => {
    (await renderPage()).unmount();
    useGeneralStore.getState().reset();

    await renderPage();

    expect(form().displayName).toBe('Acme');
    expect(calls('/api/v1/org')).toBe(1);
    expect(calls('/api/v1/org/logo')).toBe(1);
    expect(calls('/api/v1/configurationManager/metricsCollection')).toBe(1);
  });

  it('never asks a member for the metrics setting', async () => {
    signedIn(false);

    await renderPage();

    expect(screen.getByDisplayValue('Acme Inc')).toBeTruthy();
    expect(calls('/api/v1/configurationManager/metricsCollection')).toBe(0);
  });

  it("waits for an admin's metrics setting before showing the form", async () => {
    let finish: (value: { data: unknown }) => void = () => undefined;
    metrics = new Promise((resolve) => { finish = resolve; });
    renderInTheme(<GeneralPage />);
    await waitFor(() => expect(calls('/api/v1/configurationManager/metricsCollection')).toBe(1));
    expect(useGeneralStore.getState().isLoading).toBe(true);

    await act(async () => {
      finish({ data: { enableMetricCollection: 'true' } });
    });

    await waitFor(() => expect(useGeneralStore.getState().isLoading).toBe(false));
    expect(form().dataCollection).toBe(true);
  });

  it('saving the name and the data-collection switch together keeps the new values and raises no load error', async () => {
    await renderPage();
    act(() => {
      useGeneralStore.getState().setField('displayName', 'Acme Labs');
      useGeneralStore.getState().setField('dataCollection', true);
    });
    org = { ...ORG, shortName: 'Acme Labs' };
    // The metrics reread after the toggle fails: it must not undo the switch or toast a load error.
    metrics = Promise.reject(new Error('down'));
    metrics.catch(() => undefined);

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(calls('/api/v1/configurationManager/metricsCollection')).toBe(2));
    await waitFor(() => expect(calls('/api/v1/org')).toBe(2));
    expect(form()).toMatchObject({ displayName: 'Acme Labs', dataCollection: true });
    expect(useGeneralStore.getState().isDirty()).toBe(false);
    expect(addToast).not.toHaveBeenCalledWith(expect.objectContaining({ title: 'workspace.general.toasts.loadError' }));
  });

  it('uploading a logo keeps unsaved edits and shows the stored logo', async () => {
    await renderPage();
    act(() => useGeneralStore.getState().setField('city', 'Shelbyville'));
    api.get.mockImplementation(async (url: string) =>
      url === '/api/v1/org/logo'
        ? { status: 200, data: new Blob(['logo'], { type: 'image/png' }) }
        : { status: 200, data: org },
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(['logo'], 'logo.png', { type: 'image/png' })] } });
    });

    await waitFor(() => expect(screen.getByTestId('logo').getAttribute('data-src')).toBe('data:image/png;base64,bG9nbw=='));
    expect(form().city).toBe('Shelbyville');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
  });

  it('a failed upload goes back to the saved logo and shows the error', async () => {
    await renderPage();
    api.put.mockRejectedValueOnce(new Error('too large'));

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(['logo'], 'logo.png', { type: 'image/png' })] } });
    });

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'workspace.general.toasts.logoUploadError' })),
    );
    expect(screen.getByTestId('logo').getAttribute('data-src')).toBe('');
  });

  it('a failed org read shows the load error once, and never offers Save over blank fields', async () => {
    api.get.mockImplementation(async (url: string) => {
      if (url === '/api/v1/org') throw new Error('down');
      return { status: 204, data: new Blob([]) };
    });

    await renderPage();
    act(() => useGeneralStore.getState().setField('city', 'Typed'));

    expect(addToast).toHaveBeenCalledTimes(1);
    expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'workspace.general.toasts.loadError' }));
    // The save bar stays rendered but hidden and inert.
    const saveBar = screen.getByRole('button', { name: /save/i }).parentElement as HTMLElement;
    expect(saveBar.style.pointerEvents).toBe('none');
  });

  it('an org read that failed before this visit is retried: the form fills and no load error shows', async () => {
    // An earlier reader (the sidebar footer) left a failed read in the cache.
    api.get.mockRejectedValueOnce(new Error('down'));
    const footer = renderInTheme(<OrgReader />);
    await screen.findByText('failed');
    footer.unmount();

    await renderPage();

    expect(form().displayName).toBe('Acme');
    expect(addToast).not.toHaveBeenCalledWith(expect.objectContaining({ title: 'workspace.general.toasts.loadError' }));
    act(() => useGeneralStore.getState().setField('city', 'Shelbyville'));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    expect(api.put.mock.calls[0][1]).toMatchObject({ registeredName: 'Acme Inc', shortName: 'Acme', contactEmail: 'admin@acme.test' });
  });

  it('a logo that cannot be read leaves the page usable', async () => {
    api.get.mockImplementation(async (url: string) => {
      if (url === '/api/v1/org/logo') throw new Error('down');
      return url === '/api/v1/org' ? { status: 200, data: org } : { status: 200, data: { enableMetricCollection: 'false' } };
    });

    await renderPage();

    expect(form().displayName).toBe('Acme');
    expect(screen.getByTestId('logo').getAttribute('data-src')).toBe('');
  });
});
