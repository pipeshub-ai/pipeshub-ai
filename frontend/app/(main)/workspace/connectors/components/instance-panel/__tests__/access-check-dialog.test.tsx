import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { SWRConfig } from 'swr';
import '@/lib/__tests__/test-i18n';

const explainAccess = vi.fn();
vi.mock('../../../api', () => ({
  ConnectorsApi: { explainAccess: (...args: unknown[]) => explainAccess(...args) },
}));
const listUsers = vi.fn();
vi.mock('@/app/(main)/workspace/users/api', () => ({
  UsersApi: { listUsers: (...args: unknown[]) => listUsers(...args) },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AccessCheckDialog } from '../access-check-dialog';
import { installDomShims } from '../../../__tests__/fixtures';
import type { AccessExplanation } from '../../../types';

const DANA = { userId: 'u-dana', id: 'k-dana', name: 'Dana Reyes', hasLoggedIn: true };
const answer = (over: Partial<AccessExplanation> = {}): AccessExplanation => ({
  admitted: false,
  code: 'restricted_container',
  container: { name: 'Payroll', restricted: false },
  path: [{ name: 'Finance', restricted: false }, { name: null, restricted: true }],
  reference: 'abcd1234',
  ...over,
});

const settle = () => new Promise((r) => setTimeout(r, 0));

/** A fresh cache per render, so one test's people list is never another's. */
function renderDialog(connectorId = 'conn-1') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <Theme>
        <AccessCheckDialog connectorId={connectorId} />
      </Theme>
    </SWRConfig>
  );
}

const trigger = () => screen.getByRole('button', { name: "Check someone's access" });

async function open() {
  renderDialog();
  fireEvent.click(trigger());
  await waitFor(() => expect(listUsers).toHaveBeenCalled());
  await settle();
}

async function pickDana() {
  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Person' }), { key: 'Enter' });
  await settle();
  fireEvent.click(screen.getByRole('option', { name: 'Dana Reyes' }));
}

async function check(record = 'rec-1') {
  await pickDana();
  fireEvent.change(screen.getByLabelText('Record ID'), { target: { value: record } });
  fireEvent.click(screen.getByRole('button', { name: 'Check' }));
}

beforeEach(() => {
  installDomShims();
  listUsers.mockReset().mockResolvedValue({ users: [DANA], totalCount: 1 });
  explainAccess.mockReset();
});
afterEach(() => cleanup());

describe('AccessCheckDialog', () => {
  it('is a named dialog whose controls are labelled and which asks for both a person and a record before it checks', async () => {
    await open();

    expect(screen.getByRole('dialog', { name: "Check someone's access" })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Person' })).toBeTruthy();
    expect(screen.getByLabelText('Record ID').getAttribute('aria-describedby')).toBeTruthy();
    const button = screen.getByRole('button', { name: 'Check' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await pickDana();
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Record ID'), { target: { value: '  rec-1 ' } });
    expect(button.disabled).toBe(false);
  });

  it('keeps focus inside the dialog and closes on Escape', async () => {
    await open();

    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('says it is checking, then names the person, the reason, the next step and the reference', async () => {
    let finish: (a: AccessExplanation) => void = () => {};
    explainAccess.mockReturnValue(new Promise<AccessExplanation>((r) => (finish = r)));
    await open();

    await check();
    expect(screen.getByRole('status').textContent).toBe('Checking...');
    expect(explainAccess).toHaveBeenCalledWith('conn-1', 'u-dana', 'rec-1');

    finish(answer());
    const heading = await screen.findByRole('heading', { name: 'Dana Reyes cannot see this record.' });
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByText('Payroll is limited to other people.')).toBeTruthy();
    expect(screen.getByText('Ask its owner to add Dana Reyes.')).toBeTruthy();
    expect(screen.getByText('Where it sits: Finance / a restricted area')).toBeTruthy();
    expect(screen.getByText(/Reference: abcd1234/)).toBeTruthy();
    expect(document.activeElement).toBe(heading);
  });

  it('says "a restricted area" for a container the admin cannot read, and never a code or a technical word', async () => {
    explainAccess.mockResolvedValue(answer({ container: { name: null, restricted: true } }));
    await open();

    await check();

    expect(await screen.findByText('a restricted area is limited to other people.')).toBeTruthy();
    const text = screen.getByRole('dialog').textContent ?? '';
    expect(text).not.toMatch(/restricted_container|scope|stamp|ACL|token/i);
  });

  it('shows what to do when the person can see the record', async () => {
    explainAccess.mockResolvedValue(answer({ admitted: true, code: 'granted', container: null, path: [] }));
    await open();

    await check();

    expect(await screen.findByRole('heading', { name: 'Dana Reyes can see this record.' })).toBeTruthy();
    expect(screen.getByText(/may not be indexed yet/)).toBeTruthy();
    expect(screen.queryByText(/Where it sits/)).toBeNull();
  });

  it.each([
    [404, 'We could not find that person or record in this connector.'],
    [429, 'Too many checks. Try again in a minute.'],
    [500, 'We could not check. Try again.'],
  ])('a %i answer reads "%s" in an alert and moves focus to the result', async (status, words) => {
    explainAccess.mockRejectedValue({ type: 'UNKNOWN_ERROR', message: 'raw server words', statusCode: status });
    await open();

    await check();

    expect((await screen.findByRole('alert')).textContent).toBe(words);
    expect(screen.queryByText(/raw server words/)).toBeNull();
    expect(document.activeElement?.textContent).toBe('Result');
  });

  it('reads the people only once the dialog is open', async () => {
    renderDialog();
    await settle();
    expect(listUsers).not.toHaveBeenCalled();

    fireEvent.click(trigger());

    await waitFor(() => expect(listUsers).toHaveBeenCalledTimes(1));
  });

  it('says so when the list of people cannot be loaded, and asks again on Try again', async () => {
    listUsers.mockRejectedValueOnce(new Error('nope'));
    renderDialog();
    fireEvent.click(trigger());
    expect(await screen.findByText('We could not load the list of people.')).toBeTruthy();
    expect(listUsers).toHaveBeenCalledWith({ limit: 100 }, { suppressErrorToast: true });

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    await waitFor(() => expect(listUsers).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull());
    await pickDana();
  });

  it('shows the record the verdict is about, and drops the verdict when the record or the person changes', async () => {
    explainAccess.mockResolvedValue(answer());
    await open();
    await check('rec-1');
    await screen.findByRole('heading', { name: 'Dana Reyes cannot see this record.' });
    expect(screen.getByText('About record rec-1')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Record ID'), { target: { value: 'rec-2' } });

    expect(screen.queryByRole('heading', { name: /Dana Reyes/ })).toBeNull();
    expect(screen.queryByText('About record rec-1')).toBeNull();
  });

  it('ignores the answer to a check whose inputs changed while it was running', async () => {
    let finish: (a: AccessExplanation) => void = () => {};
    explainAccess.mockReturnValue(new Promise<AccessExplanation>((r) => (finish = r)));
    await open();
    await check('rec-1');
    expect(screen.getByRole('status')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Record ID'), { target: { value: 'rec-2' } });
    finish(answer());
    await settle();

    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('heading', { name: /Dana Reyes/ })).toBeNull();
    expect(screen.queryByText(/Reference:/)).toBeNull();
  });

  it('returns focus to the button that opened it', async () => {
    renderDialog();
    const opener = trigger();
    fireEvent.click(opener);
    await screen.findByRole('dialog');

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(opener);
  });

  it('starts clean for another connector: the old result is gone and an answer still on its way is dropped', async () => {
    explainAccess.mockResolvedValueOnce(answer());
    const view = renderDialog();
    fireEvent.click(trigger());
    await waitFor(() => expect(listUsers).toHaveBeenCalled());
    await settle();
    await check('rec-1');
    await screen.findByRole('heading', { name: 'Dana Reyes cannot see this record.' });

    view.rerender(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <Theme>
          <AccessCheckDialog connectorId="conn-2" />
        </Theme>
      </SWRConfig>
    );

    await waitFor(() => expect(screen.queryByRole('heading', { name: /Dana Reyes/ })).toBeNull());
    expect((screen.getByLabelText('Record ID') as HTMLInputElement).value).toBe('');

    let finish: (a: AccessExplanation) => void = () => {};
    explainAccess.mockReturnValueOnce(new Promise<AccessExplanation>((r) => (finish = r)));
    await check('rec-9');
    view.rerender(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <Theme>
          <AccessCheckDialog connectorId="conn-3" />
        </Theme>
      </SWRConfig>
    );
    finish(answer());
    await settle();

    expect(screen.queryByRole('heading', { name: /Dana Reyes/ })).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('offers no link or button that opens the record, whatever the answer', async () => {
    for (const result of [
      answer(),
      answer({ admitted: true, code: 'granted', container: null, path: [] }),
      answer({ container: { name: null, restricted: true } }),
    ]) {
      cleanup();
      explainAccess.mockResolvedValue(result);
      await open();
      await check('rec-1');
      await screen.findByRole('heading', { name: /Dana Reyes (can|cannot) see this record\./ });

      const dialog = screen.getByRole('dialog');
      expect(dialog.querySelectorAll('a, [href], [role="link"]').length).toBe(0);
      const buttons = screen.getAllByRole('button').map((b) => b.textContent);
      // Only the form's own controls (the person picker shows the chosen name): nothing that opens a record.
      expect(buttons.every((n) => ['Check', 'Close', 'Dana Reyes', ''].includes(n ?? ''))).toBe(true);
    }
  });
});
