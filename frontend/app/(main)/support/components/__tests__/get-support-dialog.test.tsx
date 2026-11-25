import React from 'react';
import { Theme } from '@radix-ui/themes';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { SupportContext } from '../../types';

const mocks = vi.hoisted(() => ({
  getContext: vi.fn(),
  submit: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values
        ? `${key} ${Object.entries(values)
            .map(([name, value]) => `${name}=${String(value)}`)
            .join(' ')}`
        : key,
    i18n: { language: 'en' },
  }),
  initReactI18next: { type: '3rdParty', init: () => undefined },
}));

vi.mock('../../api', () => ({
  SupportApi: { getContext: mocks.getContext, submit: mocks.submit },
}));

vi.mock('@/lib/api', () => ({
  isProcessedError: (error: unknown) => typeof error === 'object' && error !== null && 'statusCode' in error,
}));

vi.mock('@/lib/support/recent-errors', () => ({
  installRecentErrorRecorder: () => undefined,
  getRecentErrors: () => [],
}));

vi.mock('@/lib/store/user-store', () => ({
  selectIsAdmin: () => true,
  useUserStore: (selector: (state: unknown) => unknown) => selector({}),
}));

vi.mock('@/app/(main)/feedback/store', () => ({
  useFeedbackDialogStore: (selector: (state: { open: () => void }) => unknown) => selector({ open: vi.fn() }),
}));

import { useSupportDialogStore } from '../../store';
import { SUPPORT_AREA_KEYS } from '../../types';
import { GetSupportDialog } from '../get-support-dialog';

function context(smtpConfigured: boolean): SupportContext {
  return {
    smtpConfigured,
    supportEmail: 'help@example.test',
    workspaceName: 'Acme',
    details: [{ label: 'Workspace', value: 'Acme' }],
  };
}

const sentResult = (outcome: string, reference = 'SUP-ABC234') => ({
  reference,
  outcome,
  supportEmail: 'help@example.test',
});

function openDialog() {
  act(() => useSupportDialogStore.getState().open());
}

function renderAndOpen() {
  render(
    <Theme>
      <GetSupportDialog />
    </Theme>,
  );
  openDialog();
}

async function chooseArea(area: string) {
  fireEvent.click(await screen.findByRole('radio', { name: new RegExp(`support.areas.${area}.label`) }));
  fireEvent.click(screen.getByRole('button', { name: 'support.continue' }));
}

const description = () => screen.getByRole('textbox', { name: /support.details.descriptionLabel/ });

function typeDescription(text: string) {
  fireEvent.change(description(), { target: { value: text } });
}

// The first Radix dialog render in a busy worker can pass the 5 s default.
describe('GetSupportDialog', { timeout: 15000 }, () => {
  beforeAll(() => {
    // Radix Tooltip measures its arrow with ResizeObserver, which jsdom lacks.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  afterAll(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    act(() => useSupportDialogStore.getState().close());
    cleanup();
    vi.clearAllMocks();
  });

  it('offers every area, keeps each step blocked until it is complete, then sends', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockResolvedValue(sentResult('sent'));
    renderAndOpen();

    await screen.findByText('support.areas.connectors.label');
    expect(screen.getAllByRole('radio')).toHaveLength(SUPPORT_AREA_KEYS.length);
    const continueButton = screen.getByRole('button', { name: 'support.continue' });
    expect(continueButton.hasAttribute('disabled')).toBe(true);
    // The reason is the button's description (and its tooltip), not a line of text in the footer.
    const reasonId = continueButton.getAttribute('aria-describedby') ?? '';
    expect(document.getElementById(reasonId)?.textContent).toBe('support.blocked.area');

    await chooseArea('connectors');

    const sendButton = () => screen.getByRole('button', { name: 'support.send' });
    const reason = () => document.getElementById(sendButton().getAttribute('aria-describedby') ?? '')?.textContent;
    expect(sendButton().hasAttribute('disabled')).toBe(true);
    expect(reason()).toBe('support.blocked.description min=10 left=10');

    typeDescription('Sync');
    expect(reason()).toBe('support.blocked.description min=10 left=6');

    typeDescription('Sync stopped at noon');
    expect(sendButton().hasAttribute('disabled')).toBe(false);
    expect(sendButton().hasAttribute('aria-describedby')).toBe(false);

    fireEvent.click(sendButton());

    await screen.findByText('SUP-ABC234');
    expect(screen.getAllByText('support.result.sentTitle').length).toBeGreaterThan(0);
    expect(mocks.submit).toHaveBeenCalledTimes(1);
    expect(mocks.submit.mock.calls[0][0]).toMatchObject({
      area: 'connectors',
      description: 'Sync stopped at noon',
      recentErrors: [],
      files: [],
    });
  });

  it('says the email is still on its way when the mail server is slow', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockResolvedValue(sentResult('pending'));
    renderAndOpen();
    await chooseArea('chat');
    typeDescription('Answers stopped citing sources');

    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));

    await screen.findByText('SUP-ABC234');
    expect(screen.getByText('support.result.pendingBody email=help@example.test')).toBeTruthy();
  });

  it('says what is sent, opens the full list, and goes back to the areas from the area card', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    renderAndOpen();
    await chooseArea('connectors');

    expect(
      screen.getByText(
        'support.details.sentWith list=support.details.sentParts.workspace and support.details.sentParts.browser',
      ),
    ).toBeTruthy();
    expect(screen.queryByText('support.details.rows.workspace')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'support.details.viewDetails' }));
    expect(screen.getByText('support.details.rows.workspace')).toBeTruthy();
    expect(screen.getByText('Acme')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'support.changeArea' }));
    expect(screen.getByRole('radio', { name: /support.areas.connectors.label/, checked: true })).toBeTruthy();
  });

  it('goes back to the areas from the footer and keeps what was typed', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    renderAndOpen();
    await chooseArea('connectors');
    typeDescription('Sync stopped at noon');

    fireEvent.click(screen.getByRole('button', { name: 'support.back' }));
    expect(screen.getByRole('radio', { name: /support.areas.connectors.label/, checked: true })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'support.back' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'support.continue' }));
    expect((description() as HTMLTextAreaElement).value).toBe('Sync stopped at noon');
  });

  it('starts from an empty form every time it is opened', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');

    act(() => useSupportDialogStore.getState().close());
    openDialog();

    await screen.findByRole('radio', { name: /support.areas.other.label/ });
    expect(mocks.getContext).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('radio', { checked: true })).toBeNull();
    expect(screen.getByRole('button', { name: 'support.continue' }).hasAttribute('disabled')).toBe(true);
    await chooseArea('other');
    expect((description() as HTMLTextAreaElement).value).toBe('');
  });

  it('uses a new request key for each open', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockResolvedValue(sentResult('sent'));
    renderAndOpen();

    for (const round of [1, 2]) {
      await chooseArea('other');
      typeDescription('Something is off here');
      fireEvent.click(screen.getByRole('button', { name: 'support.send' }));
      await waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(round));
      act(() => useSupportDialogStore.getState().close());
      if (round === 1) openDialog();
    }

    const [first, second] = mocks.submit.mock.calls.map(([payload]) => (payload as { requestKey: string }).requestKey);
    expect(first).toBeTruthy();
    expect(second).not.toBe(first);
  });

  it('hands over a ready email when the workspace has no SMTP', async () => {
    mocks.getContext.mockResolvedValue(context(false));
    mocks.submit.mockResolvedValue(sentResult('no_smtp', 'SUP-XYZ789'));
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');

    fireEvent.click(screen.getByRole('button', { name: 'support.continue' }));

    const link = await screen.findByRole('link', { name: /support.email.open/ });
    const href = link.getAttribute('href') ?? '';
    expect(href.startsWith('mailto:help@example.test?')).toBe(true);
    expect(decodeURIComponent(href)).toContain('] Acme · SUP-XYZ789');
    expect(decodeURIComponent(href)).toContain('Workspace: Acme');
    expect(screen.getByText('support.email.noSmtp reference=SUP-XYZ789')).toBeTruthy();
  });

  it('hands over a ready email when the send failed', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockResolvedValue(sentResult('failed', 'SUP-XYZ789'));
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');

    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));

    await screen.findByText('support.email.failed reference=SUP-XYZ789');
    expect(screen.getByRole('link', { name: /support.email.open/ })).toBeTruthy();
  });

  it('offers the email app instead of failing when the hourly limit is reached', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockRejectedValue({ statusCode: 429, message: 'Too many requests' });
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');

    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));

    await screen.findByText('support.email.limit');
    await waitFor(() => expect(screen.getByRole('link', { name: /support.email.open/ })).toBeTruthy());
  });

  it('offers the feedback link only where feedback can actually be sent', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    renderAndOpen();
    await screen.findByRole('radio', { name: /support.areas.other.label/ });
    expect(screen.getByRole('button', { name: /support.areaStep.idea/ })).toBeTruthy();

    act(() => useSupportDialogStore.getState().close());
    mocks.getContext.mockResolvedValue(context(false));
    openDialog();
    await screen.findByRole('radio', { name: /support.areas.other.label/ });
    // Without SMTP the feedback dialog refuses to open, so the link would only close this one.
    expect(screen.queryByRole('button', { name: /support.areaStep.idea/ })).toBeNull();
  });

  it('lets the person email support when the server cannot take the request at all', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockRejectedValue({ statusCode: 503, message: 'Service unavailable' });
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');

    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));

    await screen.findByText('support.errors.send');
    fireEvent.click(screen.getByRole('button', { name: /support.errors.emailInstead/ }));

    expect(screen.getByText('support.email.unsaved')).toBeTruthy();
    const href = screen.getByRole('link', { name: /support.email.open/ }).getAttribute('href') ?? '';
    expect(decodeURIComponent(href)).toContain('Something is off here');
  });

  it('sends an edited request under a new key, and an unchanged retry under the same one', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockRejectedValue({ statusCode: 503, message: 'Service unavailable' });
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');
    const keys = () => mocks.submit.mock.calls.map(([payload]) => (payload as { requestKey: string }).requestKey);

    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));
    await screen.findByText('support.errors.send');
    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));
    await waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(2));
    typeDescription('Something is off here, with more detail');
    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));
    await waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(3));

    const [first, second, third] = keys();
    expect(second).toBe(first);
    expect(third).not.toBe(first);
  });

  it('shows the server message for a rejected field and offers no email detour for it', async () => {
    mocks.getContext.mockResolvedValue(context(true));
    mocks.submit.mockRejectedValue({ statusCode: 400, message: 'Attachments can total up to 8 MB' });
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');

    fireEvent.click(screen.getByRole('button', { name: 'support.send' }));

    await screen.findByText('Attachments can total up to 8 MB');
    expect(screen.queryByRole('button', { name: /support.errors.emailInstead/ })).toBeNull();
  });

  it('refuses files that together are more than one email can carry, and says the email fallback has no files', async () => {
    mocks.getContext.mockResolvedValue(context(false));
    mocks.submit.mockResolvedValue(sentResult('no_smtp', 'SUP-XYZ789'));
    renderAndOpen();
    await chooseArea('other');
    typeDescription('Something is off here');

    const sized = (name: string, megabytes: number) => {
      const file = new File(['x'], name, { type: 'image/png' });
      Object.defineProperty(file, 'size', { value: megabytes * 1024 * 1024 });
      return file;
    };
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [sized('one.png', 4.5), sized('two.png', 4.5)] } });

    expect(screen.getByText('one.png')).toBeTruthy();
    expect(screen.queryByText('two.png')).toBeNull();
    expect(screen.getByText('support.details.filesTotalTooLarge')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'support.continue' }));

    await screen.findByRole('link', { name: /support.email.open/ });
    expect(screen.getByText('support.email.attachFiles count=1')).toBeTruthy();
    expect((mocks.submit.mock.calls[0][0] as { files: File[] }).files).toHaveLength(1);
  });
});
