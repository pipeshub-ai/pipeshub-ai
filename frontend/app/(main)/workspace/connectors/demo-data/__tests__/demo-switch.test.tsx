import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { DemoDataApi, type DemoDataStatus } from '../api';
import { useDemoDataStore } from '../store';
import { useToastStore } from '@/lib/store/toast-store';
import { useDemoSwitch } from '../use-demo-switch';
import { DemoDataSection } from '@/app/(main)/workspace/profile/components/demo-data-section';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));
vi.mock('../api', () => ({ DemoDataApi: { getStatus: vi.fn(), setInclude: vi.fn() } }));
vi.mock('../../api', () => ({ ConnectorsApi: { getActiveConnectors: vi.fn(), getConnectorStats: vi.fn() } }));
vi.mock('@/app/(main)/knowledge-base/api', () => ({ KnowledgeHubApi: { searchAllRecords: vi.fn() } }));

const getStatus = vi.mocked(DemoDataApi.getStatus);
const setInclude = vi.mocked(DemoDataApi.setInclude);

function status(over: Partial<DemoDataStatus> = {}): DemoDataStatus {
  return { hasDemo: true, include: true, chosen: null, realData: false, demoConnectorIds: ['demo-1'], ...over };
}

beforeEach(() => {
  vi.clearAllMocks();
  useToastStore.setState({ toasts: [] });
});

afterEach(() => {
  cleanup();
  useDemoDataStore.getState().reset();
});

describe('demo data switch in the store', () => {
  it('reads the status once, and keeps what the server says after a change', async () => {
    getStatus.mockResolvedValue(status());
    await Promise.all([useDemoDataStore.getState().loadStatus(), useDemoDataStore.getState().loadStatus()]);
    expect(getStatus).toHaveBeenCalledTimes(1);
    expect(useDemoDataStore.getState().status?.include).toBe(true);

    setInclude.mockResolvedValue(status({ include: false, chosen: false }));
    await useDemoDataStore.getState().setInclude(false);
    expect(setInclude).toHaveBeenCalledWith(false);
    expect(useDemoDataStore.getState().status).toMatchObject({ include: false, chosen: false });
  });

  it('forgets the status on reset, e.g. after the demo is removed', async () => {
    useDemoDataStore.setState({ status: status() });
    useDemoDataStore.getState().reset();
    expect(useDemoDataStore.getState().status).toBeNull();
  });
});

function HideButton() {
  const { hideWithUndo } = useDemoSwitch();
  return <button type="button" onClick={() => void hideWithUndo()}>hide</button>;
}

describe('hiding the demo from the chat landing', () => {
  it('hides it, says where to turn it back on, and Undo restores the earlier choice', async () => {
    useDemoDataStore.setState({ status: status() });
    setInclude.mockResolvedValueOnce(status({ include: false, chosen: false })).mockResolvedValueOnce(status());
    render(<Theme><HideButton /></Theme>);

    fireEvent.click(screen.getByRole('button', { name: 'hide' }));

    await waitFor(() => expect(setInclude).toHaveBeenCalledWith(false));
    const toast = await waitFor(() => {
      const found = useToastStore.getState().toasts.find((t) => t.title === en.demoData.switch.hiddenTitle);
      expect(found).toBeTruthy();
      return found!;
    });
    expect(toast.description).toBe(en.demoData.switch.hiddenDescription);
    expect(toast.duration).toBe(10_000);

    toast.action?.onClick?.();
    // It was following the default before, so Undo goes back to the default.
    await waitFor(() => expect(setInclude).toHaveBeenLastCalledWith(null));
  });

  it('tells the person when hiding could not be saved, and offers no undo', async () => {
    setInclude.mockRejectedValue(new Error('offline'));
    render(<Theme><HideButton /></Theme>);

    fireEvent.click(screen.getByRole('button', { name: 'hide' }));

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain(en.demoData.switch.errorTitle),
    );
    expect(useToastStore.getState().toasts.map((t) => t.title)).not.toContain(en.demoData.switch.hiddenTitle);
  });
});

describe('status races', () => {
  it('a read that started before a change cannot undo the change', async () => {
    let answerRead: (s: DemoDataStatus) => void = () => {};
    getStatus.mockImplementation(() => new Promise((resolve) => (answerRead = resolve)));
    setInclude.mockResolvedValue(status({ include: false, chosen: false }));

    const read = useDemoDataStore.getState().loadStatus();
    await useDemoDataStore.getState().setInclude(false);
    answerRead(status({ include: true }));
    await read;

    expect(useDemoDataStore.getState().status).toMatchObject({ include: false, chosen: false });
  });

  it('a change that answers after a reset does not bring the demo back', async () => {
    let answerChange: (s: DemoDataStatus) => void = () => {};
    setInclude.mockImplementation(() => new Promise((resolve) => (answerChange = resolve)));

    const change = useDemoDataStore.getState().setInclude(true);
    useDemoDataStore.getState().reset();
    answerChange(status());
    await change;

    expect(useDemoDataStore.getState().status).toBeNull();
  });
});

describe('DemoDataSection on the profile page', () => {
  it('is not shown when there is no demo', async () => {
    getStatus.mockResolvedValue(status({ hasDemo: false, include: false, demoConnectorIds: [] }));
    render(<Theme><DemoDataSection /></Theme>);
    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    expect(screen.queryByText(en.workspace.profile.demoData.title)).toBeNull();
  });

  it('switches the demo off for this person', async () => {
    getStatus.mockResolvedValue(status());
    setInclude.mockResolvedValue(status({ include: false, chosen: false }));
    render(<Theme><DemoDataSection /></Theme>);

    const toggle = await screen.findByRole('switch', { name: en.workspace.profile.demoData.label });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText(en.workspace.profile.demoData.followingDefault)).toBeTruthy();

    fireEvent.click(toggle);

    await waitFor(() => expect(setInclude).toHaveBeenCalledWith(false));
    await screen.findByRole('button', { name: en.workspace.profile.demoData.useDefault });
  });

  it('goes back to the default', async () => {
    getStatus.mockResolvedValue(status({ include: false, chosen: false }));
    setInclude.mockResolvedValue(status());
    render(<Theme><DemoDataSection /></Theme>);

    fireEvent.click(await screen.findByRole('button', { name: en.workspace.profile.demoData.useDefault }));

    await waitFor(() => expect(setInclude).toHaveBeenCalledWith(null));
  });
});
