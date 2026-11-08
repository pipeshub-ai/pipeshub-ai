import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-upload-limits', () => ({
  useUploadLimits: () => ({ maxFileSizeMB: 30, maxFileSizeBytes: 30 * 1024 * 1024 }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/file-icon', () => ({ FileIcon: () => null }));

import { UploadDataSidebar } from '../upload-data-sidebar';

vi.stubGlobal(
  'ResizeObserver',
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

afterEach(cleanup);

function renderSidebar(onSave: (items: unknown[]) => boolean | void) {
  render(
    <Theme>
      <UploadDataSidebar open onOpenChange={() => {}} onSave={onSave} />
    </Theme>,
  );
}

function pickFile(name: string) {
  const input = screen.getByTestId('upload-input-file') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(['x'], name, { type: 'text/plain' })] } });
}

describe('UploadDataSidebar — save', () => {
  it('keeps the picked files when the page refuses the upload', async () => {
    const onSave = vi.fn(() => false);
    renderSidebar(onSave);
    pickFile('readme.txt');
    await screen.findByTestId('upload-selected-file-row');

    fireEvent.click(screen.getByTestId('upload-sidebar-save'));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('upload-selected-file-row')).toBeTruthy();
  });

  it('clears the picked files once the page takes the upload', async () => {
    const onSave = vi.fn(() => true);
    renderSidebar(onSave);
    pickFile('readme.txt');
    await screen.findByTestId('upload-selected-file-row');

    fireEvent.click(screen.getByTestId('upload-sidebar-save'));

    await waitFor(() => expect(screen.queryByTestId('upload-selected-file-row')).toBeNull());
  });
});
