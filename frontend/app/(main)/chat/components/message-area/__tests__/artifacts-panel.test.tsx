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
    expect(screen.queryByRole('button', { name: /^Download / })).toBeNull();
  });

  it('marks the card unavailable on a gone download and never opens the fallback URL', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    kb.streamDownloadRecord.mockRejectedValue(gone());

    renderPanel([artifact({ mimeType: 'text/csv', fileName: 'data.csv' })]);
    fireEvent.click(screen.getByRole('button', { name: /^Download / }));

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
    fireEvent.click(screen.getByRole('button', { name: /^Preview / }));

    expect(await screen.findByText('No longer available')).toBeTruthy();
  });

  it('keeps the card usable when the preview fails for another reason', async () => {
    const onPreview = vi.fn().mockRejectedValue(gone(500));

    renderPanel([artifact({ mimeType: 'text/csv', fileName: 'data.csv' })], { onPreview });
    fireEvent.click(screen.getByRole('button', { name: /^Preview / }));

    await waitFor(() => expect(onPreview).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole('button', { name: /^Preview / })).toBeTruthy());
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

  it('names Preview and Download for the file, in one non-shrinking row beside a truncating name', () => {
    const longName = `${'quarterly-ticket-prioritization-'.repeat(6)}.md`;
    renderPanel([artifact({ mimeType: 'text/markdown', fileName: longName, derivedFromCodeArtifactId: 'code-1' })], {
      onPreview: vi.fn(),
      onViewSource: vi.fn(),
    });

    const preview = screen.getByRole('button', { name: `Preview ${longName}` });
    expect(preview.textContent).toBe('Preview');
    expect(preview.className).toContain('rt-variant-solid');
    const download = screen.getByRole('button', { name: `Download ${longName}` });
    expect(download.className).toContain('rt-variant-soft');
    const row = preview.parentElement as HTMLElement;
    expect(download.parentElement === row || download.parentElement?.parentElement === row).toBe(true);
    expect(row.style.flexShrink).toBe('0');

    const name = screen.getByText(longName);
    expect(name.style.whiteSpace).toBe('nowrap');
    expect(name.style.textOverflow).toBe('ellipsis');
    expect(name.style.overflow).toBe('hidden');
  });

  it('shows the Radix loading state on Preview while it is busy', async () => {
    let resolve: () => void = () => {};
    const onPreview = vi.fn(() => new Promise<void>((r) => { resolve = r; }));
    renderPanel([artifact({ mimeType: 'text/markdown', fileName: 'notes.md' })], { onPreview });

    const preview = screen.getByRole('button', { name: 'Preview notes.md' });
    fireEvent.click(preview);
    await waitFor(() => expect((preview as HTMLButtonElement).disabled).toBe(true));
    // Radix's own loading state: its spinner, not a swapped-in one.
    expect(preview.querySelector('.rt-Spinner')).not.toBeNull();
    resolve();
    await waitFor(() => expect((preview as HTMLButtonElement).disabled).toBe(false));
  });
});

describe('ArtifactsPanel header', () => {
  it('is a button that toggles aria-expanded and uses translated text', () => {
    renderPanel([artifact({ mimeType: 'text/csv' }), artifact({ id: 'doc-2', mimeType: 'text/csv' })]);

    const header = screen.getByRole('button', { name: /2 artifacts generated/ });
    expect(header.getAttribute('aria-expanded')).toBe('true');

    fireEvent.click(header);

    expect(header.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('button', { name: /^Download / })).toBeNull();
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
