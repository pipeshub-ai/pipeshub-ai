import React, { useState } from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { ConfirmationDialog, type ConfirmationDialogProps } from '../confirmation-dialog';

// jsdom lays nothing out; treat connected elements as rendered.
beforeEach(() => {
  vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(function (this: Element) {
    return (this.isConnected ? [{}] : []) as unknown as DOMRectList;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

const base: ConfirmationDialogProps = {
  open: true,
  onOpenChange: () => {},
  title: 'Remove Priya?',
  message: 'She loses access to this workspace.',
  confirmLabel: 'Remove',
  onConfirm: () => {},
};

function renderDialog(props: Partial<ConfirmationDialogProps> = {}) {
  return render(
    <Theme>
      <ConfirmationDialog {...base} {...props} />
    </Theme>,
  );
}

const dialog = () => screen.getByRole('alertdialog');

describe('ConfirmationDialog default size', () => {
  it('renders as before: 37.5rem wide, plain title, string message as the description', () => {
    renderDialog();
    const content = dialog();
    expect(content.style.maxWidth).toBe('37.5rem');
    expect(content.style.padding).toBe('var(--space-5)');
    expect(content.style.borderRadius).toBe('var(--radius-5)');
    const title = screen.getByRole('heading', { name: 'Remove Priya?' });
    expect(title.querySelector('span')).toBeNull();
    expect(title.style.color).toBe('var(--text-primary)');
    const description = screen.getByText('She loses access to this workspace.');
    expect(description.style.marginTop).toBe('var(--space-2)');
    expect(content.getAttribute('aria-describedby')).toBe(description.id);
  });

  it('keeps a node message in a plain box', () => {
    renderDialog({ message: <span>Markup message</span> });
    const box = screen.getByText('Markup message').parentElement!;
    expect(box.tagName).toBe('DIV');
    expect(box.style.marginTop).toBe('var(--space-2)');
  });
});

describe('ConfirmationDialog compact size', () => {
  it('is 380px wide with a 16px medium title', () => {
    renderDialog({ size: 'compact' });
    const content = dialog();
    expect(content.style.maxWidth).toBe('calc(var(--space-9) * 6 - var(--space-1))');
    expect(content.style.padding).toBe('var(--space-5)');
    const titleText = screen.getByText('Remove Priya?');
    expect(titleText.tagName).toBe('SPAN');
    expect(titleText.className).toContain('rt-r-size-3');
    expect(titleText.className).toContain('rt-r-weight-medium');
    expect(titleText.style.color).toBe('var(--text-primary)');
  });

  it('describes the dialog with a string message, right under the title', () => {
    renderDialog({ size: 'compact' });
    const description = screen.getByText('She loses access to this workspace.');
    expect(description.className).toContain('rt-r-size-2');
    expect(description.style.marginTop).toBe('0px');
    expect(description.style.color).toBe('var(--text-secondary)');
    expect(dialog().getAttribute('aria-describedby')).toBe(description.id);
  });

  it('describes the dialog with a node message too', () => {
    renderDialog({ size: 'compact', message: <span>Markup message</span> });
    const description = screen.getByText('Markup message').parentElement!;
    expect(description.tagName).toBe('DIV');
    expect(description.className).toContain('rt-r-size-2');
    expect(description.id).toBeTruthy();
    expect(dialog().getAttribute('aria-describedby')).toBe(description.id);
  });

  it('puts the outline gray Cancel first and focuses it on open', () => {
    renderDialog({ size: 'compact' });
    const buttons = screen.getAllByRole('button');
    expect(buttons[0].textContent).toBe('Cancel');
    expect(buttons[0].className).toContain('rt-variant-outline');
    expect(buttons[0].getAttribute('data-accent-color')).toBe('gray');
    expect(document.activeElement).toBe(buttons[0]);
  });
});

/** An opener that removes itself while the dialog is open, as a deleted row does. */
function Harness({ returnFocus }: { returnFocus?: boolean }) {
  const [open, setOpen] = useState(false);
  const [rowGone, setRowGone] = useState(false);
  return (
    <Theme>
      <button type="button" id="fallback">
        Add user
      </button>
      {!rowGone && (
        <button type="button" onClick={() => setOpen(true)}>
          Remove row
        </button>
      )}
      <button type="button" data-testid="remove-row" onClick={() => setRowGone(true)} hidden />
      <button type="button" data-testid="close" onClick={() => setOpen(false)} hidden />
      <ConfirmationDialog
        {...base}
        open={open}
        onOpenChange={setOpen}
        returnFocus={returnFocus}
        returnFocusFallback={() => document.getElementById('fallback')}
      />
    </Theme>
  );
}

async function openRemoveAndClose() {
  const opener = screen.getByRole('button', { name: 'Remove row' });
  act(() => opener.focus());
  act(() => opener.click());
  expect(screen.getByRole('alertdialog')).toBeTruthy();
  act(() => (screen.getByTestId('remove-row') as HTMLButtonElement).click());
  act(() => (screen.getByTestId('close') as HTMLButtonElement).click());
  // Radix moves focus on close in a zero-delay timeout.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('ConfirmationDialog focus return', () => {
  it('sends focus to the fallback when the opener is gone and returnFocus is set', async () => {
    render(<Harness returnFocus />);
    await openRemoveAndClose();
    expect(document.activeElement?.id).toBe('fallback');
  });

  it('leaves Radix focus handling alone without returnFocus', async () => {
    render(<Harness />);
    await openRemoveAndClose();
    expect(document.activeElement).toBe(document.body);
  });
});
