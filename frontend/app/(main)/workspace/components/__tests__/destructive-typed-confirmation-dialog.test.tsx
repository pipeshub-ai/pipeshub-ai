import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
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
