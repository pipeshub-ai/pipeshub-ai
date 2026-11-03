import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { ChatArtifact } from '../../../types';

const streamRecord = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeBaseApi: { streamRecord, streamDownloadRecord: vi.fn() },
}));

import { ArtifactsPanel } from '../artifacts-panel';

const h = React.createElement;

function artifact(overrides: Partial<ChatArtifact>): ChatArtifact {
  return {
    id: 'a1',
    fileName: 'chart.png',
    mimeType: 'image/png',
    downloadUrl: '',
    artifactType: 'IMAGE',
    ...overrides,
  } as ChatArtifact;
}

beforeEach(() => {
  vi.stubGlobal('URL', Object.assign(URL, {
    createObjectURL: vi.fn(() => 'blob:http://localhost/thumb-1'),
    revokeObjectURL: vi.fn(),
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  streamRecord.mockReset();
});

describe('ArtifactsPanel thumbnails', () => {
  // T33
  it('still renders an image artifact thumbnail streamed through the API', async () => {
    streamRecord.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    const { container } = render(h(Theme, null, h(ArtifactsPanel, { artifacts: [artifact({ recordId: 'r1' })] })));
    await waitFor(() =>
      expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:http://localhost/thumb-1'),
    );
  });

  it('still renders a same-origin artifact URL without a recordId', async () => {
    const { container } = render(
      h(Theme, null, h(ArtifactsPanel, { artifacts: [artifact({ downloadUrl: '/api/v1/document/d1/download' })] })),
    );
    await waitFor(() =>
      expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/v1/document/d1/download'),
    );
  });

  it('never auto-loads a thumbnail from a foreign host dressed up as a presigned URL', async () => {
    const { container } = render(
      h(Theme, null, h(ArtifactsPanel, {
        artifacts: [artifact({ downloadUrl: 'https://evil.example/x.png?X-Amz-Signature=1&d=secret' })],
      })),
    );
    await waitFor(() => expect(container.textContent).toContain('chart.png'));
    expect(container.querySelector('img')).toBeNull();
  });
});
