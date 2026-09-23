import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';

const toastError = vi.fn(() => 'critical-toast');
const toastUpdate = vi.fn();

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/lib/store/toast-store', () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    warning: vi.fn(),
    update: (...args: unknown[]) => toastUpdate(...args),
    dismiss: vi.fn(),
  },
}));
// A stable object: a fresh one each render would change effect deps and make
// the component tear its own toast down between renders.
const featureFlagsState = { fetchFlags: vi.fn() };
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: unknown) => unknown) => selector(featureFlagsState),
}));

let isAdmin: boolean | null = null;
vi.mock('@/lib/store/user-store', () => ({
  selectIsAdmin: () => isAdmin,
  useUserStore: (selector: (s: unknown) => unknown) => selector(undefined),
}));

// The component reads plain selectors and store methods from the same hook.
const healthState = {
  startBackgroundPolling: vi.fn(),
  stopBackgroundPolling: vi.fn(),
  retryServerConnection: vi.fn(),
};

vi.mock('@/lib/store/services-health-store', () => ({
  useServicesHealthStore: (selector: (s: unknown) => unknown) => selector(healthState),
  selectApiServerReachable: () => true,
  selectBackgroundCheckFailed: () => true,
  selectAppServices: () => ({ query: 'unhealthy' }),
  selectInfraServices: () => ({}),
  selectInfraServiceNames: () => ({}),
  APP_SERVICE_LABELS: { query: 'Query Service' },
  CRITICAL_APP_SERVICES: ['query'],
  formatServiceList: (labels: string[]) => labels.join(' and '),
}));

import { HealthGate } from '../health-gate';

beforeEach(() => {
  toastError.mockClear();
  toastUpdate.mockClear();
});

afterEach(() => {
  cleanup();
  isAdmin = null;
});

describe('the "View status" action on the services toast', () => {
  it('appears once a slow-loading profile turns out to be an admin', () => {
    isAdmin = null;
    const view = render(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );

    // The toast was created before the profile resolved, so it has no action.
    const created = toastError.mock.calls[0]?.[1] as { action?: unknown } | undefined;
    expect(created?.action).toBeUndefined();

    isAdmin = true;
    view.rerender(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );

    const updated = toastUpdate.mock.calls.at(-1)?.[1] as
      | { action?: { label: string } }
      | undefined;
    expect(updated?.action?.label).toBe('View status');
  });
});
