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
// The general page's extra cards as this repository binds them, whatever `@/config` a build supplies. The page
// alone otherwise: the edition's form settings and extras are empty here. What an edition puts in them has its own tests.
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  GeneralSettingsExtras: (await import('../components/general-settings-extras')).GeneralSettingsExtras,
  GeneralPageExtras: () => null,
  useGeneralPageSettings: (await import('../general-page-settings')).useNoGeneralPageSettings,
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
import { useFeatureFlagsStore, type AgentHarnessRollout } from '@/lib/store/feature-flags-store';
import { HARNESS_ORG_ANSWERS, SURFACES_SHOWN } from '@/lib/store/__tests__/harness-org-answers';
import { clearSessionCache } from '@/lib/api/session-cache';

const ORG = {
  _id: 'o1',
  registeredName: 'Acme Inc',
  shortName: 'Acme',
  contactEmail: 'admin@acme.test',
  permanentAddress: { addressLine1: '1 Main St', city: 'Springfield', state: 'IL', postCode: '62701', country: 'US' },
};
let org = ORG;

function answerReads() {
  api.get.mockImplementation(async (url: string) => {
    if (url === '/api/v1/org') return { status: 200, data: org };
    if (url === '/api/v1/org/logo') return { status: 204, data: new Blob([]) };
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
    answerReads();
    api.put.mockResolvedValue({ data: {} });
    URL.createObjectURL = vi.fn(() => 'blob:preview');
    URL.revokeObjectURL = vi.fn();
    signedIn(true);
  });

  afterEach(() => {
    cleanup();
    useFeatureFlagsStore.setState({ agentHarness: null });
  });

  for (const [answer, rollout] of Object.entries(HARNESS_ORG_ANSWERS) as Array<[keyof typeof HARNESS_ORG_ANSWERS, AgentHarnessRollout | null]>) {
    it(`GATE ${answer}: organisation deletion is ${SURFACES_SHOWN[answer] ? 'offered' : 'not offered'} (ADR-OPS-08)`, async () => {
      useFeatureFlagsStore.setState({ agentHarness: rollout });
      await renderPage();
      await screen.findByDisplayValue('Acme Inc');
      expect(screen.queryByTestId('delete-org-section') !== null).toBe(SURFACES_SHOWN[answer]);
    });
  }

  it('with the agent harness on, an admin sees no policy webhook card and the page asks for no such setting', async () => {
    useFeatureFlagsStore.setState({ agentHarness: { useHarness: true } as never });
    await renderPage();
    await screen.findByDisplayValue('Acme Inc');
    expect(screen.queryByText('securityIntegrations.webhook.title')).toBeNull();
    expect(api.get.mock.calls.some(([url]) => String(url).includes('policy-webhook'))).toBe(false);
  });

  it('a revisit shows the cached org and makes no org or logo request', async () => {
    (await renderPage()).unmount();
    useGeneralStore.getState().reset();

    await renderPage();

    expect(form().displayName).toBe('Acme');
    expect(calls('/api/v1/org')).toBe(1);
    expect(calls('/api/v1/org/logo')).toBe(1);
  });

  it('with nothing added by the edition, an admin gets the form and a save sends the org fields alone', async () => {
    await renderPage();

    expect(screen.getByDisplayValue('Acme Inc')).toBeTruthy();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(api.get.mock.calls.map(([url]) => url).sort()).toEqual(['/api/v1/org', '/api/v1/org/logo']);
    // Nothing is unsaved yet: the save bar is put away.
    expect(screen.getByRole('button', { name: /save/i }).closest('[inert]')).not.toBeNull();

    act(() => useGeneralStore.getState().setField('displayName', 'Acme Labs'));
    org = { ...ORG, shortName: 'Acme Labs' };
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(calls('/api/v1/org')).toBe(2));
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put).toHaveBeenCalledWith('/api/v1/org/', {
      registeredName: 'Acme Inc',
      shortName: 'Acme Labs',
      contactEmail: 'admin@acme.test',
      permanentAddress: { addressLine1: '1 Main St', country: 'US', state: 'IL', city: 'Springfield', postCode: '62701' },
    });
    expect(useGeneralStore.getState().isDirty()).toBe(false);
  });

  it('a member gets the read-only page', async () => {
    signedIn(false);

    await renderPage();

    expect(screen.getByDisplayValue('Acme Inc')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull();
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
    // The save bar stays rendered but hidden, out of the tab order, and Save does nothing.
    const save = screen.getByRole('button', { name: /save/i });
    expect(save.closest('[inert]')).not.toBeNull();
    fireEvent.click(save);
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.put).not.toHaveBeenCalled();
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
      return { status: 200, data: org };
    });

    await renderPage();

    expect(form().displayName).toBe('Acme');
    expect(screen.getByTestId('logo').getAttribute('data-src')).toBe('');
  });

  describe('the Edition link, with the form edited', () => {
    const editionLink = () => screen.getByRole('link', { name: 'workspace.enterprise.seeWhatsInEnterprise' });

    it('asks before leaving, and goes on to the Enterprise page once the edits are discarded', async () => {
      await renderPage();
      await screen.findByDisplayValue('Acme Inc');
      act(() => useGeneralStore.getState().setField('city', 'Shelbyville'));

      fireEvent.click(editionLink());

      expect(router.push).not.toHaveBeenCalled();
      fireEvent.click(await screen.findByRole('button', { name: 'workspace.general.discardDialog.confirm' }));
      expect(form().city).toBe('Springfield');
      expect(router.push.mock.calls[0][0].replace(/\/$/, '')).toBe('/workspace/enterprise');
    });

    it('stays, with the edits kept, when the admin continues editing', async () => {
      await renderPage();
      await screen.findByDisplayValue('Acme Inc');
      act(() => useGeneralStore.getState().setField('city', 'Shelbyville'));

      fireEvent.click(editionLink());
      fireEvent.click(await screen.findByRole('button', { name: 'workspace.general.discardDialog.cancel' }));

      expect(form().city).toBe('Shelbyville');
      expect(router.push).not.toHaveBeenCalled();
      // A later Discard from the save bar only discards: the link it was opened for is forgotten.
      act(() => useGeneralStore.getState().setDiscardDialogOpen(true));
      fireEvent.click(await screen.findByRole('button', { name: 'workspace.general.discardDialog.confirm' }));
      expect(router.push).not.toHaveBeenCalled();
    });

    it('leaves without asking when nothing is edited', async () => {
      await renderPage();
      await screen.findByDisplayValue('Acme Inc');

      const event = new MouseEvent('click', { bubbles: true, cancelable: true });
      editionLink().dispatchEvent(event);

      expect(screen.queryByRole('button', { name: 'workspace.general.discardDialog.confirm' })).toBeNull();
      // next/link handles the click itself; the page did not stop it.
      expect(router.push).not.toHaveBeenCalled();
    });
  });
});
