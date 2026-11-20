import React from 'react';
import { Theme } from '@radix-ui/themes';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  submit: vi.fn(),
  getContext: vi.fn(),
  success: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('../../api', () => ({
  FeedbackApi: { submit: mocks.submit },
}));

vi.mock('@/lib/store/toast-store', () => ({
  toast: { success: mocks.success, warning: vi.fn(), error: vi.fn() },
}));

vi.mock('../../store', async () => {
  const { create } = await import('zustand');
  return {
    useFeedbackDialogStore: create<{ isOpen: boolean; open: () => void; close: () => void }>((set) => ({
      isOpen: false,
      open: () => set({ isOpen: true }),
      close: () => set({ isOpen: false }),
    })),
  };
});

vi.mock('../../../support/api', () => ({
  SupportApi: { getContext: mocks.getContext },
}));

vi.mock('@/app/(main)/support/store', () => ({
  useSupportDialogStore: (selector: (state: { open: () => void }) => unknown) => selector({ open: vi.fn() }),
}));

import { useFeedbackDialogStore } from '../../store';
import { SendFeedbackDialog } from '../send-feedback-dialog';

function renderOpen() {
  render(
    <Theme>
      <SendFeedbackDialog />
    </Theme>,
  );
  act(() => useFeedbackDialogStore.getState().open());
}

function submitFeedback() {
  fireEvent.change(screen.getByRole('textbox', { name: 'feedback.descriptionPlaceholder' }), {
    target: { value: 'Please add a dark theme for the editor.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'feedback.submit' }));
}

describe('SendFeedbackDialog', { timeout: 15000 }, () => {
  beforeEach(() => {
    mocks.getContext.mockResolvedValue({ smtpConfigured: true, supportEmail: 'support@example.com', workspaceName: 'Acme', details: [] });
  });

  afterEach(() => {
    act(() => useFeedbackDialogStore.getState().close());
    cleanup();
    vi.clearAllMocks();
  });

  it('renders nothing while closed', () => {
    render(
      <Theme>
        <SendFeedbackDialog />
      </Theme>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens as a centered modal dialog, not a panel pinned to the bottom-right corner', () => {
    renderOpen();

    const dialog = screen.getByRole('dialog', { name: 'feedback.title' });
    expect(dialog.style.position).not.toBe('fixed');
    expect(dialog.style.bottom).toBe('');
    expect(dialog.style.right).toBe('');
    // Radix Themes centres Dialog.Content inside its overlay.
    expect(dialog.closest('.rt-DialogOverlay')).not.toBeNull();
  });

  it('closes on Escape', () => {
    renderOpen();

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

    expect(useFeedbackDialogStore.getState().isOpen).toBe(false);
  });

  it('thanks the person and closes once the email went out', async () => {
    mocks.submit.mockResolvedValue({ outcome: 'sent' });
    renderOpen();

    submitFeedback();

    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith('feedback.success'));
    expect(useFeedbackDialogStore.getState().isOpen).toBe(false);
  });

  it('falls back to the email app with the written text when the email did not go out', async () => {
    mocks.submit.mockResolvedValue({ outcome: 'no_smtp' });
    mocks.getContext.mockResolvedValue({ smtpConfigured: true, supportEmail: 'support@example.com', workspaceName: 'Acme', details: [] });
    renderOpen();

    submitFeedback();

    await screen.findByText('support@example.com');
    expect(screen.getByText(/Please add a dark theme/)).toBeTruthy();
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it('says email is not set up and goes straight to the email app, without trying to send', async () => {
    mocks.getContext.mockResolvedValue({ smtpConfigured: false, supportEmail: 'support@example.com', workspaceName: 'Acme', details: [] });
    renderOpen();

    await screen.findByText('support.details.smtpTitle');
    fireEvent.change(screen.getByRole('textbox', { name: 'feedback.descriptionPlaceholder' }), {
      target: { value: 'Please add a dark theme for the editor.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'support.continue' }));

    await screen.findByText('support@example.com');
    expect(mocks.submit).not.toHaveBeenCalled();
  });

  it('refuses files that together are more than one email can carry', () => {
    renderOpen();
    const sized = (name: string, megabytes: number) => {
      const file = new File(['x'], name, { type: 'image/png' });
      Object.defineProperty(file, 'size', { value: megabytes * 1024 * 1024 });
      return file;
    };
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [sized('one.png', 4.5), sized('two.png', 4.5)] } });

    expect(screen.getByText('one.png')).toBeTruthy();
    expect(screen.queryByText('two.png')).toBeNull();
    expect(screen.getByText('feedback.filesTotalTooLarge')).toBeTruthy();
  });
});
