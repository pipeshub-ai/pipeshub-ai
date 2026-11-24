import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { clearSessionCache } from '@/lib/api/session-cache';
import { DemoDataApi, type DemoDataStatus } from '../api';
import { AboutDemoDataDialog } from '../components/about-demo-data-dialog';
import { findSampleAccounts } from '../remove-demo-data';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, unknown>) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      if (vars?.returnObjects) return cur;
      if (typeof cur !== 'string') return key;
      return cur.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(vars?.[name] ?? ''));
    },
  }),
}));

vi.mock('../api', () => ({
  DemoDataApi: { getStatus: vi.fn(), setInclude: vi.fn(), setEnabledForEveryone: vi.fn() },
}));

vi.mock('../remove-demo-data', () => ({ findSampleAccounts: vi.fn() }));
vi.mock('../../api', () => ({
  ConnectorsApi: { getActiveConnectors: vi.fn(async () => ({ success: true, connectors: [] })), getConnectorStats: vi.fn() },
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: { searchAllRecords: vi.fn(async () => ({ items: [] })) },
}));

const HIDDEN_BY_REAL_DATA: DemoDataStatus = {
  hasDemo: true,
  include: false,
  chosen: null,
  realData: true,
  offForEveryone: false,
  demoConnectorIds: ['demo-1'],
};

function renderDialog(isAdmin: boolean) {
  return render(
    <Theme>
      <AboutDemoDataDialog open onOpenChange={() => {}} isAdmin={isAdmin} />
    </Theme>,
  );
}

beforeEach(() => {
  vi.mocked(DemoDataApi.getStatus).mockResolvedValue(HIDDEN_BY_REAL_DATA);
  vi.mocked(DemoDataApi.setInclude).mockResolvedValue({ ...HIDDEN_BY_REAL_DATA, include: true, chosen: true });
  vi.mocked(findSampleAccounts).mockReset();
});

afterEach(() => {
  cleanup();
  clearSessionCache();
});

describe('AboutDemoDataDialog', () => {
  it('says what the demo holds: tools, people, restricted records and questions', () => {
    renderDialog(false);
    expect(screen.getByText('Google Drive')).toBeTruthy();
    // As sync stores them: each Slack thread is one record.
    expect(screen.getByText('65')).toBeTruthy();
    expect(screen.getByText('Tomás Rivera')).toBeTruthy();
    expect(screen.getAllByText(en.demoData.about.people.canSignIn)).toHaveLength(2);
    expect(screen.getByText('Pricing committee')).toBeTruthy();
    expect(screen.getByText('Enterprise pricing strategy 2026')).toBeTruthy();
    expect(screen.getByText(en.chat.demoSuggestions['5'].text)).toBeTruthy();
  });

  it('keeps the sign-in accounts to admins', () => {
    renderDialog(false);
    expect(screen.queryByText(en.demoData.about.accounts.title)).toBeNull();
    expect(findSampleAccounts).not.toHaveBeenCalled();
  });

  it('tells an admin which sample accounts exist and how they sign in', async () => {
    vi.mocked(findSampleAccounts).mockResolvedValue([
      { userId: 'u-alice', email: 'alice@acme-demo.example', name: 'Alice Chen' },
    ]);
    renderDialog(true);
    await waitFor(() => expect(screen.getByText(en.demoData.about.accounts.state.missing)).toBeTruthy());
    // Two people badges, plus Alice's account.
    expect(screen.getAllByText(en.demoData.about.accounts.state.active)).toHaveLength(3);
    expect(screen.getByText(en.demoData.about.accounts.password)).toBeTruthy();
    expect(screen.getByText(en.demoData.about.accounts.missing)).toBeTruthy();
    expect(screen.getByText(/cd \/app\/backend && node dist\/scripts\/set-password-link\.js alice@acme-demo\.example/)).toBeTruthy();
    // Every sample account counts, including one the admin may be signed in as.
    expect(findSampleAccounts).toHaveBeenCalledWith();
  });

  it('names only accounts that exist in the reset command', async () => {
    vi.mocked(findSampleAccounts).mockResolvedValue([
      { userId: 'u-bob', email: 'bob@acme-demo.example', name: 'Bob Okafor' },
    ]);
    renderDialog(true);
    await waitFor(() => expect(screen.getByText(/set-password-link\.js bob@acme-demo\.example/)).toBeTruthy());
    expect(screen.queryByText(/set-password-link\.js alice@/)).toBeNull();
  });

  it('says the accounts could not be checked, rather than missing, and can try again', async () => {
    vi.mocked(findSampleAccounts).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([]);
    renderDialog(true);
    await waitFor(() => expect(screen.getAllByText(en.demoData.about.accounts.state.unknown)).toHaveLength(2));
    expect(screen.queryByText(en.demoData.about.accounts.missing)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: en.demoData.about.accounts.retry }));
    await waitFor(() => expect(screen.getAllByText(en.demoData.about.accounts.state.missing)).toHaveLength(2));
    expect(findSampleAccounts).toHaveBeenCalledTimes(2);
  });

  it('explains why the demo is hidden and lets the person show it', async () => {
    renderDialog(false);
    await waitFor(() => expect(screen.getByText(en.demoData.about.status.hiddenRealData)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: en.demoData.about.status.show }));
    await waitFor(() => expect(DemoDataApi.setInclude).toHaveBeenCalledWith(true));
  });
});
