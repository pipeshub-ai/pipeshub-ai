import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { DeleteChatDialog } from '../delete-chat-dialog';

afterEach(() => cleanup());

describe('DeleteChatDialog', () => {
  it('is named by its visible heading and described by its explanation', () => {
    render(
      <Theme>
        <DeleteChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} chatTitle="Quarterly plan" />
      </Theme>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Are you sure?' });
    const describedBy = dialog.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toContain('Quarterly plan');
    expect(screen.queryByText('Delete Confirmation')).toBeNull();
  });

  it('names the typed confirmation field by its visible prompt, and keeps Delete off until DELETE is typed', () => {
    render(
      <Theme>
        <DeleteChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} chatTitle="Quarterly plan" />
      </Theme>,
    );
    expect(screen.getByRole('textbox', { name: 'Type DELETE to confirm' })).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Delete' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('draws no scrim of its own on top of the dialog overlay', () => {
    render(
      <Theme>
        <DeleteChatDialog open onOpenChange={vi.fn()} onConfirm={vi.fn()} chatTitle="Quarterly plan" />
      </Theme>,
    );
    const handBuilt = Array.from(document.querySelectorAll<HTMLElement>('div')).filter(
      (el) => el.style.position === 'fixed' && el.style.inset === '0px',
    );
    expect(handBuilt).toHaveLength(0);
  });
});
