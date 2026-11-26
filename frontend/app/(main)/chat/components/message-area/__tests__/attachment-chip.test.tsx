import React from 'react';
import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { i18n } from '@/lib/i18n';
import { AttachmentChip } from '../attachment-chip';
import type { AttachmentRef } from '../../../types';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const upload = (overrides: Partial<AttachmentRef> = {}): AttachmentRef => ({
  recordId: 'rec-1',
  recordName: 'q3-plan.pdf',
  mimeType: 'application/pdf',
  extension: 'pdf',
  virtualRecordId: 'vr-1',
  ...overrides,
});

const renderChip = (attachment: AttachmentRef, onOpen = vi.fn()) => {
  render(
    <Theme>
      <AttachmentChip attachment={attachment} onOpen={onOpen} />
    </Theme>,
  );
  return onOpen;
};

describe('AttachmentChip: an upload erased with another conversation (QA15G-9, ERA-30)', () => {
  it('reads as removed, names the file, and opens nothing', () => {
    const onOpen = renderChip(upload({ removed: true }));
    const chip = screen.getByTestId('attachment-removed-chip');
    expect(chip.textContent).toContain('q3-plan.pdf');
    expect(chip.textContent).toContain(en.chat.attachments.removed);
    expect(chip.getAttribute('aria-label')).toBe(
      en.chat.attachments.removedTitle.replace('{{name}}', 'q3-plan.pdf'),
    );
    expect(screen.queryByRole('button')).toBeNull();
    fireEvent.click(chip);
    fireEvent.keyDown(chip, { key: 'Enter' });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('a live upload is a button that opens it, by click or keyboard', () => {
    const onOpen = renderChip(upload());
    expect(screen.queryByTestId('attachment-removed-chip')).toBeNull();
    const chip = screen.getByRole('button', { name: /q3-plan\.pdf/ });
    fireEvent.click(chip);
    fireEvent.keyDown(chip, { key: ' ' });
    expect(onOpen).toHaveBeenCalledTimes(2);
    expect(onOpen).toHaveBeenCalledWith(upload());
  });
});
