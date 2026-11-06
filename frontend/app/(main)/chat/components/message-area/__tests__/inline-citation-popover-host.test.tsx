import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { InlineCitationPopoverHost } from '../response-tabs/citations/inline-citation-popover-host';
import { useInlineCitationPopoverStore } from '../response-tabs/citations/citation-popover-store';
import type { CitationData } from '../response-tabs/citations';

const citation: CitationData = {
  citationId: 'c1',
  content: 'snippet',
  chunkIndex: 1,
  recordId: 'r1',
  recordName: 'handbook.pdf',
  connector: 'ONEDRIVE',
  recordType: 'FILE',
  webUrl: 'https://example.com/handbook.pdf',
  mimeType: 'application/pdf',
  extension: 'pdf',
  previewRenderable: true,
  hideWeburl: false,
  citationType: 'vectordb|document',
};

afterEach(() => {
  act(() => useInlineCitationPopoverStore.getState().close());
  cleanup();
});

function openFrom(anchor: HTMLElement) {
  act(() => {
    useInlineCitationPopoverStore.getState().open({
      key: 'k1',
      anchor,
      citation,
      callbacks: { onPreview: vi.fn() },
    });
  });
}

function setup() {
  const view = render(
    <Theme>
      <button type="button" data-testid="pill">1</button>
      <button type="button" data-testid="elsewhere">Elsewhere</button>
      <InlineCitationPopoverHost />
    </Theme>,
  );
  const pill = view.getByTestId('pill');
  pill.focus();
  openFrom(pill);
  return { view, pill };
}

describe('InlineCitationPopoverHost focus', () => {
  it('hands focus back to the citation when Escape closes it from inside the popover', async () => {
    const { view, pill } = setup();
    const preview = await view.findByRole('button', { name: 'Preview' });
    preview.focus();
    expect(document.activeElement).toBe(preview);
    fireEvent.keyDown(preview, { key: 'Escape' });
    await waitFor(() => expect(view.queryByRole('button', { name: 'Preview' })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(pill));
  });

  it('hands focus back to the citation when Escape closes it while the citation has focus', async () => {
    const { view, pill } = setup();
    await view.findByRole('button', { name: 'Preview' });
    fireEvent.keyDown(pill, { key: 'Escape' });
    await waitFor(() => expect(view.queryByRole('button', { name: 'Preview' })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(pill));
  });

  it('leaves focus where the person put it when the popover closes another way', async () => {
    const { view } = setup();
    await view.findByRole('button', { name: 'Preview' });
    const elsewhere = view.getByTestId('elsewhere');
    elsewhere.focus();
    act(() => useInlineCitationPopoverStore.getState().close('k1'));
    await waitFor(() => expect(view.queryByRole('button', { name: 'Preview' })).toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(document.activeElement).toBe(elsewhere);
  });
});
