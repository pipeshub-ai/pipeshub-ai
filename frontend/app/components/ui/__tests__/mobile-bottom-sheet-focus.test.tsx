import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { MobileBottomSheet } from '../mobile-bottom-sheet';

afterEach(() => cleanup());

function Harness({ returnFocusTo }: { returnFocusTo?: () => HTMLElement | null }) {
  const [open, setOpen] = React.useState(false);
  return (
    <Theme>
      <button type="button" onClick={() => setOpen(true)}>
        opener
      </button>
      <button type="button">elsewhere</button>
      {open && (
        <MobileBottomSheet
          open
          title="Appearance"
          onBack={() => setOpen(false)}
          onOpenChange={setOpen}
          returnFocusTo={returnFocusTo}
        >
          <button type="button">row one</button>
          <button type="button">row two</button>
        </MobileBottomSheet>
      )}
    </Theme>
  );
}

async function openSheet() {
  const opener = screen.getByRole('button', { name: 'opener' });
  opener.focus();
  fireEvent.click(opener);
  const sheet = await screen.findByRole('dialog', { name: 'Appearance' });
  return { opener, sheet };
}

describe('MobileBottomSheet focus', () => {
  it('is a modal dialog named by its title and takes focus when it opens', async () => {
    render(<Harness />);
    const { sheet } = await openSheet();
    expect(sheet.getAttribute('aria-modal')).toBe('true');
    await waitFor(() => expect(sheet.contains(document.activeElement)).toBe(true));
  });

  it('keeps Tab inside the sheet', async () => {
    render(<Harness />);
    const { sheet } = await openSheet();
    const last = screen.getByRole('button', { name: 'row two' });
    last.focus();
    fireEvent.keyDown(last, { key: 'Tab' });
    expect(sheet.contains(document.activeElement)).toBe(true);
  });

  it('Back returns focus to the control that opened it', async () => {
    render(<Harness />);
    const { opener } = await openSheet();
    fireEvent.click(screen.getByRole('button', { name: en.common.back }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it('Close returns focus to the control the caller names, when given', async () => {
    render(<Harness returnFocusTo={() => screen.getByRole('button', { name: 'elsewhere' })} />);
    await openSheet();
    fireEvent.click(screen.getByRole('button', { name: en.common.close }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'elsewhere' })));
  });
});
