import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const replace = vi.fn();
const getSystemPrompts = vi.fn();

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../api', () => ({
  PromptsApi: {
    getSystemPrompts: (...args: unknown[]) => getSystemPrompts(...args),
    saveSystemPrompts: vi.fn(),
  },
}));

import PromptsPage from '../page';
import { useUserStore } from '@/lib/store/user-store';

function signInAs(isAdmin: boolean) {
  useUserStore.setState({
    isInitialized: true,
    profile: {
      userId: 'u-1',
      firstName: null,
      lastName: null,
      fullName: null,
      email: null,
      isAdmin,
      avatarUrl: null,
      hasLoggedIn: true,
    },
  });
}

beforeEach(() => {
  replace.mockReset();
  getSystemPrompts.mockReset().mockResolvedValue({
    customSystemPrompt: '',
    customSystemPromptWebSearch: '',
    customSystemPromptAgent: '',
  });
});

afterEach(() => {
  cleanup();
  useUserStore.setState({ isInitialized: false, profile: null });
});

describe('Prompts page access', () => {
  it('sends a member who opens the URL to General without asking for the prompts', async () => {
    signInAs(false);
    const { container } = render(<PromptsPage />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/workspace/general'));
    expect(getSystemPrompts).not.toHaveBeenCalled();
    expect(container.textContent).toBe('');
  });

  it('sends the user to General when the profile loaded without a role', async () => {
    useUserStore.setState({ isInitialized: true, profile: null });
    render(<PromptsPage />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/workspace/general'));
    expect(getSystemPrompts).not.toHaveBeenCalled();
  });

  it('loads the prompts for an admin', async () => {
    signInAs(true);
    render(<PromptsPage />);

    await waitFor(() => expect(getSystemPrompts).toHaveBeenCalledTimes(1));
    expect(replace).not.toHaveBeenCalled();
  });
});
