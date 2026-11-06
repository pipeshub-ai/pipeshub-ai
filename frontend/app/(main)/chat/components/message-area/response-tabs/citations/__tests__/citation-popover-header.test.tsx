import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { CitationPopoverContent } from '../citation-popover';
import type { CitationData } from '../types';

const LONG_NAME = "darshan.godase@pipeshub.com's Private Collection for the quarterly planning notes";

const citation: CitationData = {
  citationId: 'c1',
  content: 'A technical integration guide covers building and operating connectors.',
  chunkIndex: 1,
  recordId: 'r1',
  recordName: 'connector-builder',
  connector: 'KB',
  connectorInstanceName: LONG_NAME,
  recordType: 'FILE',
  mimeType: 'text/html',
  extension: 'html',
  previewRenderable: true,
  hideWeburl: true,
  citationType: 'vectordb|document',
  origin: 'UPLOAD',
};

afterEach(cleanup);

describe('citation pop-over header', () => {
  it('keeps a long source on one truncated line, with its full name as a tooltip, beside buttons that never shrink', () => {
    render(
      <Theme>
        <CitationPopoverContent citation={citation} onPreview={vi.fn()} onOpenInCollection={vi.fn()} />
      </Theme>,
    );

    const label = screen.getByText(LONG_NAME);
    expect(label.style.whiteSpace).toBe('nowrap');
    expect(label.style.overflow).toBe('hidden');
    expect(label.style.textOverflow).toBe('ellipsis');
    expect(label.getAttribute('title')).toBe(LONG_NAME);

    const source = label.parentElement as HTMLElement;
    expect(source.style.minWidth).toMatch(/^0(px)?$/);

    const buttons = screen.getByRole('button', { name: 'Preview' }).parentElement as HTMLElement;
    expect(buttons.style.flexShrink).toBe('0');
    expect(buttons.contains(screen.getByRole('button', { name: 'Open in Collections' }))).toBe(true);

    const header = source.parentElement as HTMLElement;
    expect(header.contains(buttons)).toBe(true);
    expect(header.className).toMatch(/\brt-r-gap-2\b/);
    expect(header.style.width === '100%' || /\brt-r-w\b/.test(header.className)).toBe(true);
  });
});
