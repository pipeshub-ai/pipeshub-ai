import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import type { Connector } from '../../types';
import { useDemoDataStore } from '../store';
import { useToastStore } from '@/lib/store/toast-store';
import { DemoSourceBadge } from '../components/demo-source-badge';
import { DemoDataRemovalNotice } from '../components/demo-data-removal-notice';
import { findSampleAccounts, removeDemoData } from '../remove-demo-data';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      if (typeof cur !== 'string') return key;
      return cur.replace(/\{\{(\w+)\}\}/g, (_, name: string) => vars?.[name] ?? '');
    },
  }),
}));

// The lookups are exercised in store.test.ts. Here the store is seeded, and
// the listing returns the same connectors, as the real one would.
vi.mock('../../api', () => ({
  ConnectorsApi: {
    getActiveConnectors: vi.fn(async () => ({
      success: true,
      connectors: useDemoDataStore.getState().demoConnectors,
    })),
    getConnectorStats: vi.fn(),
    deleteConnectorInstance: vi.fn(),
  },
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: { searchAllRecords: vi.fn(async () => ({ items: [] })) },
}));

vi.mock('../api', () => ({
  DemoDataApi: { getStatus: vi.fn(async () => null), setInclude: vi.fn() },
}));

vi.mock('../remove-demo-data', () => ({
  findSampleAccounts: vi.fn(),
  removeDemoData: vi.fn(),
}));

const DEMO = { _key: 'demo-1', type: 'Demo', name: 'Acme Corp demo data', isActive: true } as Connector;
const DISABLED_DEMO = { ...DEMO, isActive: false } as Connector;

function seed(state: { demo: boolean; realData: boolean | null; disabled?: boolean }) {
  useDemoDataStore.setState({
    demoConnectors: state.demo ? [state.disabled ? DISABLED_DEMO : DEMO] : [],
    realDataIndexed: state.realData,
  });
}

function renderInTheme(node: React.ReactNode) {
  return render(<Theme>{node}</Theme>);
}

beforeEach(() => {
  vi.mocked(findSampleAccounts).mockReset();
  vi.mocked(removeDemoData).mockReset();
  useToastStore.setState({ toasts: [] });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  useDemoDataStore.getState().reset();
});

describe('DemoSourceBadge', () => {
  it('labels a source from the Demo connector', () => {
    seed({ demo: true, realData: null });
    renderInTheme(<DemoSourceBadge connectorId="demo-1" />);
    expect(screen.getByText(en.demoData.badge.label)).toBeTruthy();
  });

  it('still labels a disabled demo, whose records stay in answers', () => {
    seed({ demo: true, realData: null, disabled: true });
    renderInTheme(<DemoSourceBadge connectorId="demo-1" />);
    expect(screen.getByText(en.demoData.badge.label)).toBeTruthy();
  });

  it('shows nothing for a real source, or a citation saved without a connector id', () => {
    seed({ demo: true, realData: null });
    renderInTheme(
      <>
        <DemoSourceBadge connectorId="slack-1" />
        <DemoSourceBadge connectorId={undefined} />
      </>,
    );
    expect(screen.queryByText(en.demoData.badge.label)).toBeNull();
  });
});

describe('DemoDataRemovalNotice', () => {
  it('asks an admin once real data has arrived', () => {
    seed({ demo: true, realData: true });
    renderInTheme(<DemoDataRemovalNotice isAdmin />);
    expect(screen.getByText(en.demoData.removalNotice.title)).toBeTruthy();
  });

  it('still asks when the demo has been disabled but not removed', () => {
    seed({ demo: true, realData: true, disabled: true });
    renderInTheme(<DemoDataRemovalNotice isAdmin />);
    expect(screen.getByText(en.demoData.removalNotice.title)).toBeTruthy();
  });

  it('stays quiet before any real data is indexed', () => {
    seed({ demo: true, realData: false });
    renderInTheme(<DemoDataRemovalNotice isAdmin />);
    expect(screen.queryByText(en.demoData.removalNotice.title)).toBeNull();
  });

  it('is not shown to members, who cannot remove the connector', () => {
    seed({ demo: true, realData: true });
    renderInTheme(<DemoDataRemovalNotice isAdmin={false} />);
    expect(screen.queryByText(en.demoData.removalNotice.title)).toBeNull();
  });

  it('"Keep for now" hides it, and it stays hidden in this browser', () => {
    seed({ demo: true, realData: true });
    renderInTheme(<DemoDataRemovalNotice isAdmin />);

    fireEvent.click(screen.getByRole('button', { name: en.demoData.removalNotice.keep }));
    expect(screen.queryByText(en.demoData.removalNotice.title)).toBeNull();

    cleanup();
    renderInTheme(<DemoDataRemovalNotice isAdmin />);
    expect(screen.queryByText(en.demoData.removalNotice.title)).toBeNull();
  });

  it('removes the demo and the sample accounts it lists, then forgets the demo', async () => {
    seed({ demo: true, realData: true });
    const accounts = [
      { userId: 'm1', email: 'alice@acme-demo.example', name: 'Alice Chen' },
      { userId: 'm2', email: 'bob@acme-demo.example', name: 'Bob Okafor' },
    ];
    vi.mocked(findSampleAccounts).mockResolvedValue(accounts);
    vi.mocked(removeDemoData).mockResolvedValue({ failedAccounts: [] });
    renderInTheme(<DemoDataRemovalNotice isAdmin />);

    fireEvent.click(screen.getByRole('button', { name: en.demoData.removalNotice.remove }));
    await screen.findByText(/Alice Chen, Bob Okafor/);
    fireEvent.click(screen.getByRole('button', { name: en.demoData.removeDialog.confirm }));

    await waitFor(() => expect(removeDemoData).toHaveBeenCalledWith(['demo-1'], accounts));
    await waitFor(() => expect(useDemoDataStore.getState().demoConnectors).toEqual([]));
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain(en.demoData.removeDialog.successTitle);
  });

  it('keeps the sample accounts when that box is unticked', async () => {
    seed({ demo: true, realData: true });
    vi.mocked(findSampleAccounts).mockResolvedValue([
      { userId: 'm1', email: 'alice@acme-demo.example', name: 'Alice Chen' },
    ]);
    vi.mocked(removeDemoData).mockResolvedValue({ failedAccounts: [] });
    renderInTheme(<DemoDataRemovalNotice isAdmin />);

    fireEvent.click(screen.getByRole('button', { name: en.demoData.removalNotice.remove }));
    fireEvent.click(await screen.findByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: en.demoData.removeDialog.confirm }));

    await waitFor(() => expect(removeDemoData).toHaveBeenCalledWith(['demo-1'], []));
  });

  it('says which accounts are left when some could not be deleted', async () => {
    seed({ demo: true, realData: true });
    const alice = { userId: 'm1', email: 'alice@acme-demo.example', name: 'Alice Chen' };
    vi.mocked(findSampleAccounts).mockResolvedValue([alice]);
    vi.mocked(removeDemoData).mockResolvedValue({ failedAccounts: [alice] });
    renderInTheme(<DemoDataRemovalNotice isAdmin />);

    fireEvent.click(screen.getByRole('button', { name: en.demoData.removalNotice.remove }));
    await screen.findByText(/Alice Chen/);
    fireEvent.click(screen.getByRole('button', { name: en.demoData.removeDialog.confirm }));

    await waitFor(() => {
      const toast = useToastStore.getState().toasts.find((t) => t.variant === 'warning');
      expect(toast?.description).toContain('alice@acme-demo.example');
    });
  });

  it('keeps the demo and explains when removal fails', async () => {
    seed({ demo: true, realData: true });
    vi.mocked(findSampleAccounts).mockResolvedValue([]);
    vi.mocked(removeDemoData).mockRejectedValue(new Error('An agent still uses this connector.'));
    renderInTheme(<DemoDataRemovalNotice isAdmin />);

    fireEvent.click(screen.getByRole('button', { name: en.demoData.removalNotice.remove }));
    const confirm = await screen.findByRole('button', { name: en.demoData.removeDialog.confirm });
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain(en.demoData.removeDialog.errorTitle),
    );
    expect(useDemoDataStore.getState().demoConnectors).toEqual([DEMO]);
  });
});
