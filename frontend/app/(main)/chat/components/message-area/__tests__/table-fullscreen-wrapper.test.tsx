import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { TableFullscreenWrapper } from '../table-fullscreen-wrapper';

afterEach(() => cleanup());

describe('TableFullscreenWrapper', () => {
  it('offers CSV download inside the fullscreen view', () => {
    const onDownloadCsv = vi.fn();
    render(
      <Theme>
        <TableFullscreenWrapper onDownloadCsv={onDownloadCsv}>
          <table><tbody><tr><td>a</td></tr></tbody></table>
        </TableFullscreenWrapper>
      </Theme>,
    );
    fireEvent.click(screen.getByTitle('chat.expandTable'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'chat.downloadCsv' }));
    expect(onDownloadCsv).toHaveBeenCalledTimes(1);
  });
});
