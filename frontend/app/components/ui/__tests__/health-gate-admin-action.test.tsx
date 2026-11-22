import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';

const toastError = vi.fn(() => 'critical-toast');
const toastUpdate = vi.fn();

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
const translation = { t: (k: string) => k, i18n: { language: 'en-US' } };
vi.mock('react-i18next', () => ({ useTranslation: () => translation }));
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

let shouldPoll = true;
const logoutAndRedirect = vi.fn();
const logoutFromWorkspaceMenu = vi.fn();
vi.mock('@/config', () => ({
  useShouldPollServiceHealth: () => shouldPoll,
  logoutAndRedirect: () => logoutAndRedirect(),
  logoutFromWorkspaceMenu: () => logoutFromWorkspaceMenu(),
}));

let electron = false;
vi.mock('@/lib/electron', () => ({ isElectron: () => electron }));

// The component reads plain selectors and store methods from the same hook.
const healthState = {
  startBackgroundPolling: vi.fn(),
  stopBackgroundPolling: vi.fn(),
  retryServerConnection: vi.fn(),
};
let apiServerReachable = true;
const setHealthState = vi.fn();

vi.mock('@/lib/store/services-health-store', () => ({
  useServicesHealthStore: Object.assign(
    (selector: (s: unknown) => unknown) => selector(healthState),
    { setState: (...args: unknown[]) => setHealthState(...args) },
  ),
  selectApiServerReachable: () => apiServerReachable,
  selectBackgroundCheckFailed: () => true,
  selectAppServices: () => ({ query: 'unhealthy' }),
  selectInfraServices: () => ({}),
  selectInfraServiceNames: () => ({}),
  APP_SERVICE_LABELS: { query: 'Query Service' },
  CRITICAL_APP_SERVICES: ['query'],
}));

import { HealthGate } from '../health-gate';

beforeEach(() => {
  toastError.mockClear();
  toastUpdate.mockClear();
  healthState.startBackgroundPolling.mockClear();
  healthState.stopBackgroundPolling.mockClear();
});

afterEach(() => {
  cleanup();
  isAdmin = null;
  shouldPoll = true;
  electron = false;
  apiServerReachable = true;
  logoutAndRedirect.mockClear();
  logoutFromWorkspaceMenu.mockClear();
  setHealthState.mockClear();
});

describe('the unreachable-server screen', () => {
  it('lets a web user log out instead of waiting forever', () => {
    apiServerReachable = false;
    const view = render(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );

    fireEvent.click(view.getByRole('button', { name: 'workspaceMenu.logOut' }));

    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(logoutFromWorkspaceMenu).not.toHaveBeenCalled();
    expect(setHealthState).toHaveBeenCalledWith({ apiServerReachable: true });
  });

  it('lets a desktop user pick another server', () => {
    apiServerReachable = false;
    electron = true;
    const view = render(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );

    fireEvent.click(
      view.getByRole('button', { name: 'electron.serverUrlSetup.changeServer' }),
    );

    expect(logoutFromWorkspaceMenu).toHaveBeenCalledTimes(1);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });
});

describe('service health polling', () => {
  it('never polls for a user outside the operator org', () => {
    shouldPoll = false;
    render(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );

    expect(healthState.startBackgroundPolling).not.toHaveBeenCalled();
    expect(featureFlagsState.fetchFlags).toHaveBeenCalled();
  });

  it('starts once eligibility resolves and stops if it is lost', () => {
    shouldPoll = false;
    const view = render(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );
    expect(healthState.startBackgroundPolling).not.toHaveBeenCalled();

    shouldPoll = true;
    view.rerender(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );
    expect(healthState.startBackgroundPolling).toHaveBeenCalledTimes(1);

    shouldPoll = false;
    view.rerender(
      <HealthGate>
        <div>body</div>
      </HealthGate>,
    );
    expect(healthState.stopBackgroundPolling).toHaveBeenCalledTimes(1);
  });
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
      | { title?: string; action?: { label: string } }
      | undefined;
    expect(updated?.action?.label).toBe('healthGate.toasts.viewStatus');
    // Re-sent on every pass so a language change also reaches the title.
    expect(updated?.title).toBe('healthGate.toasts.servicesUnavailableTitle');
  });
});
