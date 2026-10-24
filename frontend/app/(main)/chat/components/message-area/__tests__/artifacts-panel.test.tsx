import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { ErrorType } from '@/lib/api';
import type { ChatArtifact } from '../../../types';

const kb = vi.hoisted(() => ({ streamRecord: vi.fn(), streamDownloadRecord: vi.fn() }));
vi.mock('@/app/(main)/knowledge-base/api', () => ({ KnowledgeBaseApi: kb }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ArtifactsPanel, isArtifactGone } from '../artifacts-panel';

const SIGNED_URL = 'https://bucket.s3.amazonaws.com/x.png?X-Amz-Signature=abc&X-Amz-Expires=60';

function artifact(overrides: Partial<ChatArtifact> = {}): ChatArtifact {
  return {
    id: 'doc-1',
    fileName: 'chart.png',
    mimeType: 'image/png',
    sizeBytes: 10,
    downloadUrl: SIGNED_URL,
    artifactType: 'CHART',
    recordId: 'rec-1',
    version: 1,
    ...overrides,
  };
}

function gone(statusCode = 404) {
  return { type: ErrorType.NOT_FOUND, message: 'Not Found', statusCode };
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:thumb');
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPanel(items: ChatArtifact[], props: Partial<React.ComponentProps<typeof ArtifactsPanel>> = {}) {
  return render(
    <Theme>
      <ArtifactsPanel artifacts={items} {...props} />
    </Theme>,
  );
}

describe('isArtifactGone', () => {
  it('is true only for 403 and 404', () => {
    expect(isArtifactGone(gone(404))).toBe(true);
    expect(isArtifactGone(gone(403))).toBe(true);
    expect(isArtifactGone(gone(500))).toBe(false);
    expect(isArtifactGone(new Error('x'))).toBe(false);
  });
});

describe('ArtifactsPanel for a deleted artifact', () => {
  it('marks the card unavailable when the thumbnail is gone, without the global toast', async () => {
    kb.streamRecord.mockRejectedValue(gone());

    renderPanel([artifact()]);

    expect(await screen.findByText('No longer available')).toBeTruthy();
    expect(kb.streamRecord).toHaveBeenCalledWith(
      'rec-1',
      expect.objectContaining({ version: 1, suppressErrorToast: isArtifactGone }),
    );
    expect(screen.queryByRole('button', { name: 'Download artifact' })).toBeNull();
  });

  it('marks the card unavailable on a gone download and never opens the fallback URL', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    kb.streamDownloadRecord.mockRejectedValue(gone());

    renderPanel([artifact({ mimeType: 'text/csv', fileName: 'data.csv' })]);
    fireEvent.click(screen.getByRole('button', { name: 'Download artifact' }));

    expect(await screen.findByText('No longer available')).toBeTruthy();
    expect(kb.streamDownloadRecord).toHaveBeenCalledWith(
      'rec-1',
      'data.csv',
      expect.objectContaining({ suppressErrorToast: isArtifactGone }),
    );
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it('marks the card unavailable when the preview reports it gone', async () => {
    const onPreview = vi.fn().mockRejectedValue(gone());

    renderPanel([artifact({ mimeType: 'text/csv', fileName: 'data.csv' })], { onPreview });
    fireEvent.click(screen.getByRole('button', { name: 'Preview artifact' }));

    expect(await screen.findByText('No longer available')).toBeTruthy();
  });

  it('keeps the card usable when the preview fails for another reason', async () => {
    const onPreview = vi.fn().mockRejectedValue(gone(500));

    renderPanel([artifact({ mimeType: 'text/csv', fileName: 'data.csv' })], { onPreview });
    fireEvent.click(screen.getByRole('button', { name: 'Preview artifact' }));

    await waitFor(() => expect(onPreview).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview artifact' })).toBeTruthy());
    expect(screen.queryByText('No longer available')).toBeNull();
  });
});

describe('ArtifactsPanel card', () => {
  it('shows the translated artifact type, not its code', () => {
    kb.streamRecord.mockResolvedValue(new Blob());
    renderPanel([artifact({ artifactType: 'DATA_FILE', mimeType: 'application/json', fileName: 'metrics.json' })]);

    expect(screen.getByText(/Data file/)).toBeTruthy();
    expect(screen.queryByText(/DATA_FILE/)).toBeNull();
  });

  it('draws its actions as one fixed-size, non-shrinking row beside a truncating name', () => {
    const longName = `${'quarterly-ticket-prioritization-'.repeat(6)}.md`;
    renderPanel([artifact({ mimeType: 'text/markdown', fileName: longName, derivedFromCodeArtifactId: 'code-1' })], {
      onPreview: vi.fn(),
      onViewSource: vi.fn(),
    });

    const buttons = ['View source code', 'Preview artifact', 'Download artifact'].map((name) =>
      screen.getByRole('button', { name }),
    );
    for (const button of buttons) {
      expect(button.style.width).toBe('28px');
      expect(button.style.height).toBe('28px');
      expect(button.style.margin).toBe('0px');
      expect(button.style.flexShrink).toBe('0');
    }
    const row = buttons[0].parentElement as HTMLElement;
    expect(buttons.every((b) => b.parentElement === row)).toBe(true);
    expect(row.style.flexShrink).toBe('0');

    const name = screen.getByText(longName);
    expect(name.style.whiteSpace).toBe('nowrap');
    expect(name.style.textOverflow).toBe('ellipsis');
    expect(name.style.overflow).toBe('hidden');
  });

  it('keeps an action the same size while it is busy', async () => {
    let resolve: () => void = () => {};
    const onPreview = vi.fn(() => new Promise<void>((r) => { resolve = r; }));
    renderPanel([artifact({ mimeType: 'text/markdown', fileName: 'notes.md' })], { onPreview });

    fireEvent.click(screen.getByRole('button', { name: 'Preview artifact' }));
    const busy = await screen.findByRole('button', { name: 'Loading preview' });
    expect(busy.style.width).toBe('28px');
    expect(busy.style.height).toBe('28px');
    resolve();
    await screen.findByRole('button', { name: 'Preview artifact' });
  });
});

describe('ArtifactsPanel header', () => {
  it('is a button that toggles aria-expanded and uses translated text', () => {
    renderPanel([artifact({ mimeType: 'text/csv' }), artifact({ id: 'doc-2', mimeType: 'text/csv' })]);

    const header = screen.getByRole('button', { name: /2 artifacts generated/ });
    expect(header.getAttribute('aria-expanded')).toBe('true');

    fireEvent.click(header);

    expect(header.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('button', { name: 'Download artifact' })).toBeNull();
  });

  it('uses the singular for one artifact', () => {
    renderPanel([artifact({ mimeType: 'text/csv' })]);

    expect(screen.getByRole('button', { name: /^1 artifact generated$/ })).toBeTruthy();
  });

  it('shows the newer-version hint from i18n', () => {
    renderPanel([artifact({ mimeType: 'text/csv' })], { latestArtifactVersions: new Map([['rec-1', 4]]) });

    expect(screen.getByText('Newer version available (v4)')).toBeTruthy();
  });
});
