import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor, screen, fireEvent } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const replace = vi.fn();
const getSystemPrompts = vi.fn();
const saveSystemPrompts = vi.fn();

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../api', () => ({
  PromptsApi: {
    getSystemPrompts: (...args: unknown[]) => getSystemPrompts(...args),
    saveSystemPrompts: (...args: unknown[]) => saveSystemPrompts(...args),
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
    useInMcp: false,
    mcpPromptMaxChars: 500,
  });
  saveSystemPrompts.mockReset().mockResolvedValue(undefined);
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

describe('Using the Agent prompt in the MCP server', () => {
  const stored = (agent: string, useInMcp: boolean) =>
    getSystemPrompts.mockResolvedValue({
      customSystemPrompt: '',
      customSystemPromptWebSearch: '',
      customSystemPromptAgent: agent,
      useInMcp,
      mcpPromptMaxChars: 10,
    });
  const toggle = () => screen.getByRole('switch');
  const agentEditor = () => screen.getByPlaceholderText(/Prefer internal knowledge/);
  const saveButton = () => screen.getByRole('button', { name: 'Save' });

  beforeEach(() => signInAs(true));

  it('shows the saved choice, and counts the trimmed prompt against the limit only while it is on', async () => {
    stored('  Be brief. ', true);
    render(<PromptsPage />);

    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'));
    expect(screen.getByTestId('prompt-mcp-count').textContent).toBe('9 / 10 characters');

    fireEvent.click(toggle());
    expect(screen.queryByTestId('prompt-mcp-count')).toBeNull();
  });

  it('saves the choice with the prompts', async () => {
    stored('Be brief.', false);
    render(<PromptsPage />);
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('false'));

    fireEvent.click(toggle());
    fireEvent.click(saveButton());

    await waitFor(() =>
      expect(saveSystemPrompts).toHaveBeenCalledWith({
        customSystemPrompt: '',
        customSystemPromptWebSearch: '',
        customSystemPromptAgent: 'Be brief.',
        useInMcp: true,
      }),
    );
  });

  it('will not save over prompts that failed to load', async () => {
    getSystemPrompts.mockResolvedValue(null);
    render(<PromptsPage />);

    await waitFor(() => expect(screen.getByTestId('prompts-load-error')).toBeTruthy());
    fireEvent.change(agentEditor(), { target: { value: 'Be brief.' } });
    expect((saveButton() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(saveButton());
    expect(saveSystemPrompts).not.toHaveBeenCalled();
  });

  it('will not save a prompt longer than the MCP server takes while it is on', async () => {
    stored('', false);
    render(<PromptsPage />);
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('false'));

    fireEvent.change(agentEditor(), { target: { value: 'Twelve chars' } });
    fireEvent.click(toggle());

    expect(screen.getByRole('alert').textContent).toMatch(/^12 \/ 10 characters\./);
    expect((saveButton() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(saveButton());
    expect(saveSystemPrompts).not.toHaveBeenCalled();

    // Turned off, the same prompt saves: chat has no such limit.
    fireEvent.click(toggle());
    expect((saveButton() as HTMLButtonElement).disabled).toBe(false);
  });
});
