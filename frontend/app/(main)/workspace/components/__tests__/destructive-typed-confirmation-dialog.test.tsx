import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { DestructiveTypedConfirmationDialog } from '../destructive-typed-confirmation-dialog';

/**
 * Regression coverage for the drawer-stacking bug: this dialog's overlay is
 * a plain (non-Radix-portaled) `<Box>` — without an explicit `container`,
 * it rendered wherever the component sat in the tree, so opening it from
 * inside `WorkspaceRightPanel` (a `document.body` portal at a higher
 * z-index) put the overlay + `Dialog.Content` behind the drawer. See
 * `SkillEditorPanel`'s `DangerZone`, which now passes `nestedHost` from
 * `useWorkspaceDrawerNestedModalHost` as `container`.
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
  it('portals both the overlay and Dialog.Content into the given container', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);

    renderDialog(host);

    expect(host.querySelector('[data-testid="destructive-dialog-overlay"]')).not.toBeNull();
    // `Dialog.Content` renders the heading text — also expected inside `host`.
    expect(host.textContent).toContain('Delete "pdf-extractor"?');

    host.remove();
  });

  it('does not leak the overlay into an unrelated container element', () => {
    const host = document.createElement('div');
    const other = document.createElement('div');
    document.body.appendChild(host);
    document.body.appendChild(other);

    renderDialog(host);

    expect(other.querySelector('[data-testid="destructive-dialog-overlay"]')).toBeNull();

    host.remove();
    other.remove();
  });

  it('falls back to document.body when no container is given', () => {
    renderDialog(undefined);
    expect(document.body.querySelector('[data-testid="destructive-dialog-overlay"]')).not.toBeNull();
  });
});
