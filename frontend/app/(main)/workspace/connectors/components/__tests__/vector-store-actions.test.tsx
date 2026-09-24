import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const cleanupVectorStore = vi.fn(() => Promise.resolve({ accepted: true, operation: 'cleanup' }));
const reindexVectorStore = vi.fn(() => Promise.resolve({ accepted: true, operation: 'reindex' }));

vi.mock('../../api', () => ({
  ConnectorsApi: {
    cleanupVectorStore: () => cleanupVectorStore(),
    reindexVectorStore: () => reindexVectorStore(),
  },
}));

const addToast = vi.fn();
vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: typeof addToast }) => unknown) =>
    selector({ addToast }),
}));

// i18n in tests: return the English fallback, interpolating {{token}} so the
// typed-confirmation prompt still names the literal the user must enter.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback?: string, opts?: Record<string, string>) => {
      if (!fallback) return _key;
      return opts
        ? Object.entries(opts).reduce(
            (acc, [k, v]) => acc.replace(`{{${k}}}`, String(v)),
            fallback
          )
        : fallback;
    },
  }),
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: () => null,
}));

import { VectorStoreActions } from '../vector-store-actions';

const h = React.createElement;

function renderActions() {
  return render(h(Theme, null, h(VectorStoreActions, null)));
}

function openDialog(label: RegExp) {
  fireEvent.click(screen.getByRole('button', { name: label }));
}

function confirmButton(name: RegExp): HTMLButtonElement {
  return screen.getByRole('button', { name }) as HTMLButtonElement;
}

function typeConfirmation(value: string) {
  fireEvent.change(screen.getByPlaceholderText(/DELETE|REINDEX/), {
    target: { value },
  });
}

beforeEach(() => {
  cleanupVectorStore.mockClear();
  reindexVectorStore.mockClear();
  addToast.mockClear();
});
afterEach(() => cleanup());

describe('VectorStoreActions', () => {
  it('does not call the API until the confirmation token is typed', async () => {
    renderActions();
    openDialog(/clean up$/i);

    const confirm = confirmButton(/delete embeddings/i);
    expect(confirm.disabled).toBe(true);

    fireEvent.click(confirm);
    expect(cleanupVectorStore).not.toHaveBeenCalled();

    typeConfirmation('DELETE');
    await waitFor(() =>
      expect(confirmButton(/delete embeddings/i).disabled).toBe(false)
    );
    fireEvent.click(confirmButton(/delete embeddings/i));
    await waitFor(() => expect(cleanupVectorStore).toHaveBeenCalledTimes(1));
  });

  it('rejects the wrong token, including the other operation’s', () => {
    renderActions();
    openDialog(/clean up$/i);

    typeConfirmation('REINDEX');
    expect(confirmButton(/delete embeddings/i).disabled).toBe(true);
    expect(cleanupVectorStore).not.toHaveBeenCalled();
  });

  it('warns that search stays broken until a reindex finishes', () => {
    renderActions();
    openDialog(/clean up$/i);

    expect(
      screen.getByText(/stays broken until a reindex has finished/i)
    ).toBeTruthy();
    expect(screen.getByText(/records are not deleted — only their embeddings/i)).toBeTruthy();
  });

  it('requires its own token for reindex and then starts the job', async () => {
    renderActions();
    openDialog(/reindex$/i);

    expect(confirmButton(/start reindex/i).disabled).toBe(true);
    typeConfirmation('REINDEX');
    await waitFor(() =>
      expect(confirmButton(/start reindex/i).disabled).toBe(false)
    );
    fireEvent.click(confirmButton(/start reindex/i));

    await waitFor(() => expect(reindexVectorStore).toHaveBeenCalledTimes(1));
    expect(cleanupVectorStore).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        expect.objectContaining({ variant: 'success' })
      )
    );
  });

  it('keeps the dialog open when the API rejects, so the operator can retry', async () => {
    cleanupVectorStore.mockRejectedValueOnce(new Error('409 conflict'));
    renderActions();
    openDialog(/clean up$/i);
    typeConfirmation('DELETE');
    fireEvent.click(screen.getByRole('button', { name: /delete embeddings/i }));

    await waitFor(() => expect(cleanupVectorStore).toHaveBeenCalledTimes(1));
    expect(confirmButton(/delete embeddings/i)).toBeTruthy();
    expect(addToast).not.toHaveBeenCalled();
  });
});
