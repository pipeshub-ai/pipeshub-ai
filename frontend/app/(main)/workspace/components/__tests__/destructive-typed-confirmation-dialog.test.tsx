import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { Theme } from '@radix-ui/themes';
import { DestructiveTypedConfirmationDialog } from '../destructive-typed-confirmation-dialog';

/**
 * Regression coverage for two bugs:
 *
 * 1. Drawer stacking: without an explicit `container`, opening this dialog from
 *    inside `WorkspaceRightPanel` (a `document.body` portal at a higher z-index)
 *    put it behind the drawer. See `SkillEditorPanel`'s `DangerZone`, which
 *    passes `nestedHost` from `useWorkspaceDrawerNestedModalHost` as `container`.
 * 2. Double dimming: a hand-rolled overlay (z-index 999) used to paint on top
 *    of Radix's own overlay, which also contains `Dialog.Content`, so the
 *    dialog itself looked dull. Radix's overlay must be the only backdrop.
 */

afterEach(cleanup);

function renderDialog(container: HTMLElement | null | undefined) {
  return render(
    <Theme>
      <DestructiveTypedConfirmationDialog
        open
        onOpenChange={() => {}}
        heading='Delete "pdf-extractor"?'
        body={<span>Are you sure?</span>}
        confirmationKeyword="pdf-extractor"
        confirmInputLabel='Type "pdf-extractor" to confirm'
        primaryButtonText="Delete"
        onConfirm={vi.fn()}
        container={container}
      />
    </Theme>
  );
}

describe('DestructiveTypedConfirmationDialog', () => {
  it('portals Radix overlay and Dialog.Content into the given container', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);

    renderDialog(host);

    expect(host.querySelector('.rt-DialogOverlay')).not.toBeNull();
    expect(host.textContent).toContain('Delete "pdf-extractor"?');

    host.remove();
  });

  it('does not leak dialog content into an unrelated container element', () => {
    const host = document.createElement('div');
    const other = document.createElement('div');
    document.body.appendChild(host);
    document.body.appendChild(other);

    renderDialog(host);

    expect(other.querySelector('.rt-DialogOverlay')).toBeNull();
    expect(other.textContent).not.toContain('Delete "pdf-extractor"?');

    host.remove();
    other.remove();
  });

  it('falls back to document.body when no container is given', () => {
    renderDialog(undefined);
    expect(document.body.querySelector('.rt-DialogOverlay')).not.toBeNull();
    expect(document.body.textContent).toContain('Delete "pdf-extractor"?');
  });

  it('renders no custom backdrop on top of the Radix overlay', () => {
    renderDialog(undefined);
    expect(document.body.querySelector('[data-testid="destructive-dialog-overlay"]')).toBeNull();
    expect(document.body.querySelectorAll('.rt-DialogOverlay')).toHaveLength(1);
  });
});

describe('DestructiveTypedConfirmationDialog: what is typed (CMDUI3 W1)', () => {
  it('says a wrong entry does not match with a generic line when the caller gives none, and only an exact match is marked', () => {
    renderDialog(undefined);
    const box = screen.getByLabelText('Type "pdf-extractor" to confirm');
    expect(screen.queryByTestId('typed-confirm-mismatch')).toBeNull();
    fireEvent.change(box, { target: { value: 'pdf' } });
    expect(screen.getByTestId('typed-confirm-mismatch').textContent).toContain("Doesn't match. Type it exactly as shown.");
    expect(box.getAttribute('data-state')).toBe('mismatch');
    expect(screen.getByRole('button', { name: 'Delete' }).hasAttribute('disabled')).toBe(true);
    fireEvent.change(box, { target: { value: 'pdf-extractor' } });
    expect(screen.queryByTestId('typed-confirm-mismatch')).toBeNull();
    expect(box.getAttribute('data-state')).toBe('match');
    expect(screen.getByRole('button', { name: 'Delete' }).hasAttribute('disabled')).toBe(false);
  });
});

describe('DestructiveTypedConfirmationDialog on a phone (ORGDEL)', () => {
  afterEach(() => vi.unstubAllGlobals());

  function renderOn(width: 'phone' | 'desktop', phoneSheet: boolean) {
    window.matchMedia = ((query: string) => ({
      matches: width === 'phone',
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    return render(
      <Theme>
        <DestructiveTypedConfirmationDialog
          open
          onOpenChange={() => {}}
          heading="Delete Acme?"
          headingIcon="warning"
          body={<span>Are you sure?</span>}
          confirmationKeyword="Acme"
          confirmInputLabel="Type Acme to confirm"
          primaryButtonText="Delete Acme"
          confirmIcon="delete_forever"
          onConfirm={vi.fn()}
          phoneSheet={phoneSheet}
        />
      </Theme>,
    );
  }

  it('opens as a bottom sheet with full-width buttons when asked to', () => {
    renderOn('phone', true);
    const content = document.querySelector('.rt-DialogContent') as HTMLElement;
    expect(content.style.position).toBe('fixed');
    expect(content.style.bottom).toBe('0px');
    const buttons = Array.from(content.querySelectorAll('button'));
    expect(buttons.every((b) => b.style.width === '100%')).toBe(true);
    cleanup();
  });

  it('stays a centred dialog on a desktop, or when not asked', () => {
    renderOn('desktop', true);
    expect((document.querySelector('.rt-DialogContent') as HTMLElement).style.position).toBe('');
    cleanup();
    renderOn('phone', false);
    expect((document.querySelector('.rt-DialogContent') as HTMLElement).style.position).toBe('');
    cleanup();
  });
});
