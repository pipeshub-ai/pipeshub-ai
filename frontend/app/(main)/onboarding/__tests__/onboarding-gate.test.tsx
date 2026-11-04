import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';
import { useAuthStore } from '@/config';
import { useUserStore, type UserProfile } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { useOnboardingStore } from '../store';
import { useOnboardingRedirect } from '../use-onboarding-redirect';
import OnboardingPage from '../onboardingPage';

const replace = vi.fn();
const push = vi.fn();
// Stable like the real Next router; a new object per render re-runs effects that depend on it.
const router = { replace, push };
let search = 'step=storage';
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(search),
}));

const getOnboardingStatus = vi.fn();
const updateOnboardingStatus = vi.fn();
vi.mock('../api', () => ({
  getOnboardingStatus: () => getOnboardingStatus(),
  updateOnboardingStatus: (s: string) => updateOnboardingStatus(s),
}));

vi.mock('../components', () => ({
  OnboardingHeader: () => null,
  OnboardingBrandFooter: () => null,
  OnboardingSteps: () => null,
  StepAiModel: () => null,
  StepEmbeddingModel: () => null,
  StepStorage: () => null,
}));

// The label may carry the product name as an interpolation.
const enterLabel = new RegExp(en.onboarding.nav.enterPipeshub.replace(/{{.*?}}/g, '.*'));

function setRole(isAdmin: boolean | null) {
  useUserStore.setState({ profile: { userId: 'u1', fullName: 'U', isAdmin } as UserProfile });
}

function Gate() {
  useOnboardingRedirect();
  return null;
}

beforeEach(() => {
  replace.mockReset();
  push.mockReset();
  getOnboardingStatus.mockReset().mockResolvedValue({ status: 'notConfigured' });
  updateOnboardingStatus.mockReset();
  search = 'step=storage';
  window.history.replaceState(null, '', '/chat');
  useAuthStore.setState({ isHydrated: true, isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
});
afterEach(() => cleanup());

describe('useOnboardingRedirect', () => {
  it('sends an admin to onboarding while the org is not configured', async () => {
    setRole(true);
    render(<Gate />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/onboarding'));
  });

  it.each([false, null])('keeps a user whose admin flag is %s in the app', async (isAdmin) => {
    setRole(isAdmin);
    render(<Gate />);
    await new Promise((r) => setTimeout(r, 0));
    expect(getOnboardingStatus).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});

describe('OnboardingPage', () => {
  it('sends a member who opens /onboarding back to the app', async () => {
    setRole(false);
    render(<Theme><OnboardingPage /></Theme>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'));
    expect(getOnboardingStatus).not.toHaveBeenCalled();
  });

  it('stays on the page and lets the admin retry when saving the status fails', async () => {
    setRole(true);
    useOnboardingStore.setState({ completedStepIds: ['ai-model'] });
    updateOnboardingStatus.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(undefined);
    render(<Theme><OnboardingPage /></Theme>);

    const finish = await screen.findByRole('button', { name: enterLabel });
    fireEvent.click(finish);
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain(en.onboarding.nav.finishFailed),
    );
    expect(replace).not.toHaveBeenCalledWith('/chat');
    // Kept clear of the bottom-right Enter button so the admin can retry.
    expect(useToastStore.getState().toasts.find((t) => t.title === en.onboarding.nav.finishFailed)?.placement).toBe('top');

    fireEvent.click(await screen.findByRole('button', { name: enterLabel }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/chat'));
    expect(updateOnboardingStatus).toHaveBeenCalledTimes(2);
  });
});
