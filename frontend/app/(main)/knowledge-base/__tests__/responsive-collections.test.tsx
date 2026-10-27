import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { KbGridView } from '../components/kb-grid-view';
import { KbListView } from '../components/kb-list-view';
import { Header } from '../components/header';
import { hubNode, installBrowserShims, renderInTheme } from './kb-page-harness';

const env = vi.hoisted(() => ({ mobile: false, belowDesktop: false, canHover: true }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({
  useIsMobile: () => env.mobile,
  useIsBelowDesktop: () => env.belowDesktop,
}));
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  PermissionLockIcon: () => <span data-testid="permission-lock" />,
}));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => env.canHover }));

beforeAll(installBrowserShims);
beforeEach(() => {
  env.mobile = false;
  env.belowDesktop = false;
  env.canHover = true;
});
afterEach(cleanup);

const items = [hubNode({ id: 'r1', name: 'Runbook', sizeInBytes: 10, createdAt: 1700000000000 })];

function renderList(extra: Partial<React.ComponentProps<typeof KbListView>> = {}) {
  const onItemClick = vi.fn();
  renderInTheme(
    <KbListView
      items={items}
      selectedItems={new Set()}
      allSelected={false}
      sort={{ field: 'name', order: 'asc' }}
      onSelectAll={() => {}}
      onSelectItem={() => {}}
      onItemClick={onItemClick}
      onSort={() => {}}
      onRename={async () => {}}
      {...extra}
    />,
  );
  return { onItemClick };
}

describe('collections layout below desktop', () => {
  it('grid columns fit the width instead of being fixed at three', () => {
    const { container } = renderInTheme(
      <KbGridView items={items} selectedItems={new Set()} onSelectItem={() => {}} onItemClick={() => {}} />,
    );
    const grids = Array.from(container.querySelectorAll<HTMLElement>('div')).filter((el) =>
      el.style.gridTemplateColumns.includes('auto-fill'),
    );
    expect(grids).toHaveLength(1);
  });

  it('mobile header offers a share button', () => {
    env.mobile = true;
    const onShare = vi.fn();
    renderInTheme(
      <Header
        pageViewMode="collections"
        onFind={() => {}}
        onRefresh={() => {}}
        onShare={onShare}
        onCreateFolder={() => {}}
        onUpload={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Share collection' }));
    expect(onShare).toHaveBeenCalled();
  });

  it('mobile header keeps Find, New and Share inline and moves Refresh into the overflow menu', () => {
    env.mobile = true;
    env.belowDesktop = true;
    const { container } = renderInTheme(
      <Header pageViewMode="collections" onFind={() => {}} onRefresh={() => {}} onShare={() => {}} onRecentlyDeletedClick={() => {}} onUpload={() => {}} />,
    );
    expect(screen.getByRole('button', { name: 'Find' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Share collection' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull();
    expect(container.querySelector('[data-icon-only]')).toBeNull();
    expect(screen.getByRole('button', { name: 'More options' })).toBeTruthy();
  });

  it('mobile Share shows the lock and ignores taps when sharing is denied', () => {
    env.mobile = true;
    const onShare = vi.fn();
    renderInTheme(
      <Header pageViewMode="collections" onFind={() => {}} onRefresh={() => {}} onShare={onShare} sharePermissionDenied />,
    );
    expect(screen.getByTestId('permission-lock')).toBeTruthy();
    const share = screen.getByRole('button', { name: 'Share collection' });
    expect(share.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(share);
    expect(onShare).not.toHaveBeenCalled();
  });

  it('list keeps Size and Created on desktop and drops them below it', () => {
    renderList();
    expect(screen.getByText('Size')).toBeTruthy();
    expect(screen.getByText('Created')).toBeTruthy();
    cleanup();
    env.belowDesktop = true;
    renderList();
    expect(screen.queryByText('Size')).toBeNull();
    expect(screen.queryByText('Created')).toBeNull();
    expect(screen.getByText('Updated')).toBeTruthy();
  });

  it('tapping a name on a touch device opens the item instead of renaming it', () => {
    env.canHover = false;
    const { onItemClick } = renderList();
    fireEvent.click(screen.getByText('Runbook'));
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(onItemClick).toHaveBeenCalled();
  });

  it('clicking a name with a mouse still starts a rename', () => {
    renderList();
    fireEvent.click(screen.getByText('Runbook'));
    expect(screen.getByRole('textbox')).toBeTruthy();
  });

  it('tablet header folds Refresh and the view toggle into the overflow menu', () => {
    env.belowDesktop = true;
    const { container } = renderInTheme(
      <Header pageViewMode="collections" onFind={() => {}} onRefresh={() => {}} onShare={() => {}} onUpload={() => {}} />,
    );
    expect(screen.getByRole('button', { name: /Find/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Refresh/ })).toBeNull();
    expect(container.querySelector('[data-icon-only]')).toBeNull();
    expect(screen.getByRole('button', { name: 'More options' })).toBeTruthy();
  });

  it('phone list drops the Status and Source columns and names the source under the file', () => {
    env.mobile = true;
    env.belowDesktop = true;
    const record = { ...items[0], sourceName: 'Drive', sourceType: 'GOOGLE_DRIVE' } as unknown as (typeof items)[0];
    renderList({ items: [record], showSourceColumn: true });
    expect(screen.queryByText('Status')).toBeNull();
    expect(screen.queryByText('Source')).toBeNull();
    expect(screen.getByText('Drive')).toBeTruthy();
  });

  it('desktop list keeps the Status and Source columns', () => {
    const record = { ...items[0], sourceName: 'Drive', sourceType: 'GOOGLE_DRIVE' } as unknown as (typeof items)[0];
    renderList({ items: [record], showSourceColumn: true });
    expect(screen.getByText('Status')).toBeTruthy();
    expect(screen.getByText('Source')).toBeTruthy();
  });
});
