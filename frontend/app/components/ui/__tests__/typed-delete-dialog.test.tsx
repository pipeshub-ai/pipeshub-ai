/**
 * QAFINAL-FU-AXE-2: the Collections, bulk, KB-page and record-view delete dialogs each drew their own copy of a typed
 * delete, named by a hidden English "Delete Confirmation" while the visible question said something else. They and
 * the chat's now share TypedDeleteDialog, whose visible, translated question is its name.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';

import { BulkDeleteConfirmationDialog } from '@/app/(main)/knowledge-base/components/dialogs/bulk-delete-confirmation-dialog';
import { DeleteConfirmationDialog } from '@/app/(main)/knowledge-base/components/dialogs/delete-confirmation-dialog';
import { DeleteChatDialog } from '@/app/(main)/chat/sidebar/dialogs/delete-chat-dialog';
import { TypedDeleteDialog } from '../typed-delete-dialog';

afterEach(() => cleanup());

const within = (ui: React.ReactElement) => render(<Theme>{ui}</Theme>);
const noop = async () => {};

/** The dialog's name is text a sighted person sees: not inside a visually hidden wrapper. */
function visibleName(dialog: HTMLElement): string {
  const id = dialog.getAttribute('aria-labelledby') ?? '';
  const title = document.getElementById(id);
  expect(title, 'the dialog is labelled by an element').toBeTruthy();
  expect(title!.closest('.rt-VisuallyHidden, [data-radix-visually-hidden]')).toBeNull();
  return title!.textContent ?? '';
}

describe('TypedDeleteDialog', () => {
  it.each([
    ['KB', 'Delete collection?'],
    ['folder', 'Delete folder?'],
    ['record', 'Delete file?'],
  ] as const)('TYPEDDEL-1: deleting a %s is named by its visible question "%s"', (itemType, title) => {
    within(
      <DeleteConfirmationDialog open onOpenChange={() => {}} onConfirm={noop} itemName="Q3 plans" itemType={itemType} />,
    );
    const dialog = screen.getByRole('dialog', { name: title });
    expect(visibleName(dialog)).toBe(title);
    expect(dialog.textContent).toContain('Q3 plans');
    expect(screen.queryByText(/Delete Confirmation/i)).toBeNull();
  });

  it('TYPEDDEL-2: the bulk delete is translated and counts its items', () => {
    within(<BulkDeleteConfirmationDialog open onOpenChange={() => {}} onConfirm={noop} itemCount={1} />);
    expect(visibleName(screen.getByRole('dialog', { name: 'Delete 1 item?' }))).toBe('Delete 1 item?');
    expect(screen.getByRole('button', { name: 'Delete 1 item' })).toBeTruthy();
    cleanup();
    within(<BulkDeleteConfirmationDialog open onOpenChange={() => {}} onConfirm={noop} itemCount={3} />);
    expect(screen.getByRole('dialog', { name: 'Delete 3 items?' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete 3 items' })).toBeTruthy();
    expect(screen.getByRole('dialog').textContent).toContain(en.dialog.bulkDeleteBody);
  });

  it('TYPEDDEL-3: the chat delete uses it, named by its question', () => {
    within(<DeleteChatDialog open onOpenChange={() => {}} onConfirm={noop} chatTitle="Weekly sync" />);
    const dialog = screen.getByRole('dialog', { name: en.chat.deleteChatConfirm });
    expect(dialog.textContent).toContain('Weekly sync');
    expect(dialog.textContent).toContain(en.chat.deleteChatArtifactsNote);
  });

  it('TYPEDDEL-4: Delete stays off until the keyword is typed in the labelled box, then confirms once', async () => {
    const onConfirm = vi.fn(async () => {});
    within(
      <TypedDeleteDialog open onOpenChange={() => {}} onConfirm={onConfirm} title="Delete it?" description="Gone for good." />,
    );
    const box = screen.getByLabelText('Type DELETE to confirm');
    const del = screen.getByRole('button', { name: 'Delete' });
    expect(del.hasAttribute('disabled')).toBe(true);
    fireEvent.change(box, { target: { value: 'DELETE' } });
    expect(del.hasAttribute('disabled')).toBe(false);
    fireEvent.click(del);
    await vi.waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('dialog', { name: 'Delete it?', description: 'Gone for good.' })).toBeTruthy();
  });

  it('TYPEDDEL-5: while deleting, closing is refused', () => {
    const onOpenChange = vi.fn();
    within(
      <TypedDeleteDialog open onOpenChange={onOpenChange} onConfirm={noop} title="Delete it?" description="x" isDeleting />,
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
