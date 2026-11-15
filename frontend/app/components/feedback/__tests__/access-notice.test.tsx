import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AccessNotices, readAccessNotices } from '../access-notice';
import { NoAccessPage } from '../no-access-page';

afterEach(cleanup);
const ui = (n: React.ReactNode) => render(<Theme>{n}</Theme>);

describe('AccessNotices', () => {
  it('words each code in a polite status region, naming the source when known', () => {
    ui(
      <AccessNotices
        notices={[
          { code: 'permissions_updating' },
          { code: 'connector_syncing', connectorId: 'c1', connectorName: 'Marketing Drive' },
          { code: 'connector_syncing', connectorId: 'c2' },
          { code: 'check_unavailable' },
        ]}
      />
    );
    const region = screen.getByRole('status');
    expect(region.textContent).toContain('Some sources are updating permissions, so a few results may be missing for a few minutes.');
    expect(region.textContent).toContain('Marketing Drive is still syncing, so results from it may be incomplete.');
    expect(region.textContent).toContain('A source is still syncing, so results from it may be incomplete.');
    expect(region.textContent).toContain("We couldn't check which documents you have access to just now");
  });

  it('renders nothing without notices, and ignores malformed ones', () => {
    ui(<AccessNotices notices={[]} />);
    expect(screen.queryByRole('status')).toBeNull();
    expect(readAccessNotices([{ code: 'permissions_updating' }, null, { nope: 1 }])).toHaveLength(1);
    expect(readAccessNotices(undefined)).toEqual([]);
  });
});

describe('NoAccessPage', () => {
  it('is one page with one h1 and says nothing about why', () => {
    ui(<NoAccessPage recordId="r1" />);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe("You don't have access to this item");
    expect(screen.getByText(/moved, deleted or restricted/)).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('offers no link to the original, which would say where the item lives', () => {
    ui(<NoAccessPage recordId="r1" />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });

  it('copies the id and time only, and announces it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    ui(<NoAccessPage recordId="r1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy details for an admin' }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const text = writeText.mock.calls[0][0] as string;
    expect(text).toMatch(/^Record: r1\nTime: \d{4}-/);
    await waitFor(() => expect(screen.getByText('Details copied')).toBeTruthy());
  });
});
