import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { FilePreviewFullscreen } from '../file-preview-fullscreen';

const viewport = vi.hoisted(() => ({ belowDesktop: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({
  useIsMobile: () => false,
  useIsBelowDesktop: () => viewport.belowDesktop,
}));
vi.mock('../renderers/file-preview-renderer', () => ({
  FilePreviewRenderer: () => <div data-testid="document" />,
}));
vi.mock('../citations-panel', () => ({
  CitationsPanel: () => <div data-testid="citations" />,
}));

const citations = [{ id: 'c1', content: 'Refunds within 30 days', pageNumbers: [1] }];

function renderPreview() {
  return render(
    <Theme>
      <FilePreviewFullscreen
        source="chat"
        file={{ id: 'f1', name: 'policy.pdf', url: 'blob:x', type: 'application/pdf' }}
        citations={citations}
        onClose={() => {}}
      />
    </Theme>,
  );
}

function visible(testId: string): boolean {
  let el: HTMLElement | null = screen.getByTestId(testId);
  while (el) {
    if (el.style.display === 'none') return false;
    el = el.parentElement;
  }
  return true;
}

describe('FilePreviewFullscreen citations', () => {
  afterEach(() => {
    cleanup();
    viewport.belowDesktop = false;
  });

  it('shows citations beside the document on desktop', () => {
    renderPreview();

    expect(screen.queryByRole('radio', { name: 'Citations' })).toBeNull();
    expect(visible('document')).toBe(true);
    expect(visible('citations')).toBe(true);
  });

  it('puts citations behind a tab below desktop so the document keeps the full width', () => {
    viewport.belowDesktop = true;
    renderPreview();

    expect(visible('document')).toBe(true);
    expect(visible('citations')).toBe(false);

    // Same tab name as the desktop preview tabs.
    expect(screen.getByRole('radio', { name: 'Preview' })).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'Citations' }));

    expect(visible('document')).toBe(false);
    expect(visible('citations')).toBe(true);
  });
});
