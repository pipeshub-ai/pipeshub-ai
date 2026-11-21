import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, screen, waitFor } from '@testing-library/react';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

// The General page reads the org, its logo and the metrics setting through the session
// cache, so the HTTP client is faked here and each test starts a new session.
const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient: api, streamSSERequest: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (select: (s: { addToast: () => void }) => unknown) => select({ addToast: () => {} }),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import GeneralPage from '@/app/(main)/workspace/general/page';
import { useGeneralStore } from '@/app/(main)/workspace/general/store';
import { useUserStore } from '@/lib/store/user-store';
import { clearSessionCache } from '@/lib/api/session-cache';

const ORG = {
  _id: 'o1',
  registeredName: 'Acme',
  shortName: 'AC',
  contactEmail: 'a@acme.test',
  permanentAddress: { addressLine1: '1 Main St', city: 'Springfield', state: 'IL', postCode: '62701', country: 'US' },
};
const METRICS_URL = '/api/v1/configurationManager/metricsCollection';

// Data Collection is shown exactly when telemetry can run, so an admin can always
// see whatever is collected and switch it off; it is hidden when nothing can run.
describe('General page: Data Collection', () => {
  beforeEach(() => {
    installBrowserShims();
    vi.clearAllMocks();
    clearSessionCache();
    useGeneralStore.getState().reset();
    useUserStore.setState({
      isInitialized: true,
      profile: {
        userId: 'u1', firstName: null, lastName: null, fullName: 'Admin', email: 'a@acme.test',
        isAdmin: true, avatarUrl: null, hasLoggedIn: true, designation: null,
      },
    });
  });
  afterEach(cleanup);

  const renderWith = async (metrics: Record<string, unknown>) => {
    api.get.mockImplementation(async (url: string) => {
      if (url === '/api/v1/org') return { status: 200, data: ORG };
      if (url === '/api/v1/org/logo') return { status: 204, data: new Blob([]) };
      if (url === METRICS_URL) return { status: 200, data: metrics };
      throw new Error(`unexpected GET ${url}`);
    });
    renderInTheme(<GeneralPage />);
    await waitFor(() => expect(screen.getByDisplayValue('Acme')).toBeTruthy());
    // The admin-only metrics setting is read after the org form: wait until it has been.
    await waitFor(() => expect(api.get.mock.calls.some(([u]) => u === METRICS_URL)).toBe(true));
    // Let the answer land and render, so a section that is absent is absent for good.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
  };

  it('is hidden when telemetry is switched off on the deployment', async () => {
    await renderWith({ enableMetricCollection: 'false', telemetryEnabled: false });
    expect(screen.queryByText('workspace.general.dataCollection')).toBeNull();
  });

  it('stays hidden when telemetry is off on the deployment, even if the admin switch was left on', async () => {
    await renderWith({ enableMetricCollection: 'true', telemetryEnabled: false });
    expect(screen.queryByText('workspace.general.dataCollection')).toBeNull();
    expect(screen.queryByText('workspace.general.dataCollectionActive')).toBeNull();
  });

  it('is shown, saying data is being collected, while it is', async () => {
    await renderWith({ enableMetricCollection: 'true', telemetryEnabled: true });
    expect(await screen.findByText('workspace.general.dataCollection')).toBeTruthy();
    expect(screen.getByText('workspace.general.dataCollectionActive')).toBeTruthy();
  });

  it('is shown without the collecting notice while switched off', async () => {
    await renderWith({ enableMetricCollection: 'false', telemetryEnabled: true });
    expect(await screen.findByText('workspace.general.dataCollection')).toBeTruthy();
    expect(screen.queryByText('workspace.general.dataCollectionActive')).toBeNull();
  });

  it('is shown as before by a server that does not report telemetryEnabled', async () => {
    await renderWith({ enableMetricCollection: 'true' });
    expect(await screen.findByText('workspace.general.dataCollection')).toBeTruthy();
  });
});
