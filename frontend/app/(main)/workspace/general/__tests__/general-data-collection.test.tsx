import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';

// Stable across renders: the page's load effect depends on addToast and t.
const { getMetricsCollection, toast, i18n } = vi.hoisted(() => ({
  getMetricsCollection: vi.fn(),
  toast: { addToast: () => {} },
  i18n: { t: (key: string) => key },
}));

vi.mock('@/app/(main)/workspace/general/api', () => ({
  OrgApi: {
    getOrg: vi.fn().mockResolvedValue({ registeredName: 'Acme', shortName: 'AC', contactEmail: 'a@acme.test' }),
    getLogoUrl: vi.fn().mockResolvedValue(null),
  },
  MetricsApi: { getMetricsCollection, toggleMetricsCollection: vi.fn() },
}));
vi.mock('@/lib/store/user-store', () => {
  const state = { isAdmin: true, isProfileInitialized: true };
  const useUserStore = Object.assign((selector: (s: typeof state) => unknown) => selector(state), { getState: () => state });
  return {
    useUserStore,
    selectIsAdmin: (s: typeof state) => s.isAdmin,
    selectIsProfileInitialized: (s: typeof state) => s.isProfileInitialized,
  };
});
vi.mock('@/lib/store/toast-store', () => ({ useToastStore: (selector: (s: typeof toast) => unknown) => selector(toast) }));
vi.mock('react-i18next', () => ({ useTranslation: () => i18n }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));

import GeneralPage from '@/app/(main)/workspace/general/page';

// Data Collection is shown exactly when telemetry can run, so an admin can always
// see whatever is collected and switch it off; it is hidden when nothing can run.
describe('General page: Data Collection', () => {
  afterEach(cleanup);

  const renderWith = async (metrics: Record<string, unknown>) => {
    getMetricsCollection.mockResolvedValue(metrics);
    render(<GeneralPage />);
    await waitFor(() => expect(screen.getByDisplayValue('Acme')).toBeTruthy());
  };

  it('is hidden when telemetry is switched off on the deployment', async () => {
    await renderWith({ enableMetricCollection: 'false', telemetryEnabled: false });
    expect(screen.queryByText('workspace.general.dataCollection')).toBeNull();
  });

  it('is shown, saying data is being collected, while it is', async () => {
    await renderWith({ enableMetricCollection: 'true', telemetryEnabled: true });
    expect(screen.getByText('workspace.general.dataCollection')).toBeTruthy();
    expect(screen.getByText('workspace.general.dataCollectionActive')).toBeTruthy();
  });

  it('is shown without the collecting notice while switched off', async () => {
    await renderWith({ enableMetricCollection: 'false', telemetryEnabled: true });
    expect(screen.getByText('workspace.general.dataCollection')).toBeTruthy();
    expect(screen.queryByText('workspace.general.dataCollectionActive')).toBeNull();
  });

  it('is shown as before by a server that does not report telemetryEnabled', async () => {
    await renderWith({ enableMetricCollection: 'true' });
    expect(screen.getByText('workspace.general.dataCollection')).toBeTruthy();
  });
});
